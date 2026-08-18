"""What the experience gate actually lets through — run against a real Postgres.

The same argument as `test_education_sql.py`, on the fifth axis: `experience_min <= %s` is
`NULL` — not `false` — for a null column, and a `WHERE` clause drops a row on `NULL` exactly
as it drops it on `false`. The `is null or` half of `experience.experience_predicate` is the
only thing standing between a junior subscriber and a digest emptied of the ~75% of postings
that never state a tenure requirement. A string inspection cannot prove it works; these tests
execute it — on the predicate alone, on the display filter, and on the real shortlist query,
narrow and widened.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_experience_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import experience, geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "exptest-"

# posting_id -> experience_min. Most are null on purpose: that is the shape of the real table,
# so "unknown always passes" is exercised by the majority of the fixture set.
FIXTURES = {
    "unstated-1": None,
    "unstated-2": None,
    "czech-no-description": None,
    "wants-1y": 1,
    "wants-3y": 3,
    "wants-5y": 5,
    "wants-10y": 10,
}

UNSTATED = {"unstated-1", "unstated-2", "czech-no-description"}


@pytest.fixture(scope="module", autouse=True)
def db():
    """Point the store at the test database and insert the fixture postings."""
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    rows = [
        {"posting_id": PREFIX + pid, "source": "test", "url": f"https://x.test/{pid}",
         "title": "Data Analyst", "company": "Test", "description": "",
         "country_code": "CZ", "city": "prague", "remote_signal": False, "region": "cz",
         "location": "Praha", "work_mode": None, "experience_min": years,
         "eligibility": "eligible", "seniority": "mid", "work_type": "permanent",
         "role_category": "data_analysis", "dedup_key": PREFIX + pid}
        for pid, years in FIXTURES.items()
    ]
    store.upsert_postings(rows)
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def allowed(profile: dict) -> set[str]:
    """The fixture postings this profile's experience gate lets through."""
    sql, params = experience.experience_predicate(profile)
    with store.cursor() as cur:
        cur.execute(
            f"select posting_id from postings p where p.posting_id like %s and ({sql})",
            [PREFIX + "%", *params],
        )
        return {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}


# ------------------------------------------------------ the rule everything rests on ---

def test_a_posting_with_no_stated_requirement_always_passes():
    """`experience_min <= 2` evaluates to NULL for a null column, and a WHERE clause drops a
    NULL row exactly as it drops a false one. Without the `is null or` half, a junior's gate
    would silently delete the ~75% of the corpus that states nothing."""
    for years in (0, 1, 3, 20):
        got = allowed({"years_experience": years})
        assert UNSTATED <= got, (
            f"years={years} dropped unstated postings: missing {UNSTATED - got}"
        )


def test_silence_widens():
    """A profile that never stated its years — every pre-CV subscriber — gets no filter."""
    assert allowed({}) == set(FIXTURES)
    assert allowed({"years_experience": None}) == set(FIXTURES)
    assert allowed({"years_experience": "junk"}) == set(FIXTURES)


def test_a_demand_above_the_subscribers_years_is_excluded():
    """The feature working: three years of experience stops the 5y and 10y demands, keeps
    everything at or below, and keeps the unstated majority."""
    got = allowed({"years_experience": 3})
    assert {"wants-1y", "wants-3y"} <= got
    assert "wants-5y" not in got
    assert "wants-10y" not in got
    assert UNSTATED <= got


def test_zero_years_is_a_real_value_not_a_missing_one():
    """A first-jobber's 0 must filter (exclude every stated demand), not widen — `0 or None`
    style falsiness in the predicate would silently turn entry-level subscribers into
    no-preference ones."""
    got = allowed({"years_experience": 0})
    assert got == UNSTATED


# ------------------------------------------------- composed into the real queries ---

def _shortlist(profile_extra: dict) -> tuple[set[str], dict]:
    profile = {
        "id": None, "countries": ["CZ"], "cities": [], "remote_scope": "eu",
        "work_modes": list(geo.WORK_MODES), "stack": [], "label": "",
        "eligible_only": True, **profile_extra,
    }
    rows, meta = store.query_shortlist_meta(profile, limit=200)
    return ({r["posting_id"][len(PREFIX):] for r in rows
             if r["posting_id"].startswith(PREFIX)}, meta)


def test_the_gate_is_applied_by_the_real_shortlist_query(monkeypatch):
    """`experience_predicate` sitting unused would look identical in every test above — this
    runs the query the digest actually calls, on the narrow path."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, meta = _shortlist({"years_experience": 3,
                            "role_categories": ["data_analysis"]})
    assert not meta["widened"], "this case is meant to exercise the narrow path"
    assert "wants-3y" in ids
    assert "wants-5y" not in ids
    assert "wants-10y" not in ids
    assert UNSTATED <= ids


def test_widening_the_shortlist_does_not_re_admit_an_excluded_demand():
    """The gate lives in `_hard_gate`, outside the recall branch, so the retrieval floor's
    widened pass must not hand back roles demanding more years than the subscriber has."""
    ids, meta = _shortlist({"years_experience": 3,
                            "role_categories": ["design"]})
    assert meta["widened"], "expected the floor to widen — the assertions below only mean " \
                            "something on the widened path"
    assert UNSTATED <= ids, f"widening dropped unstated postings: missing {UNSTATED - ids}"
    assert "wants-5y" not in ids
    assert "wants-10y" not in ids


def test_the_matches_display_filter_keeps_the_unknown_majority():
    """The /matches `max_experience` filter is exclusion-polarity: "drop what demands more
    than N", so a null `experience_min` is KEPT — the opposite of the equality filters
    (seniority), where null is excluded. Executed against the same SQL the page uses."""
    filt, params = store._match_filters(max_experience=3)
    with store.cursor() as cur:
        cur.execute(
            f"select posting_id from postings p where p.posting_id like %s {filt}",
            [PREFIX + "%", *params],
        )
        got = {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}
    assert UNSTATED <= got
    assert {"wants-1y", "wants-3y"} <= got
    assert "wants-5y" not in got and "wants-10y" not in got
