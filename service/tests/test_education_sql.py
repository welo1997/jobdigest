"""What the education gate actually lets through — run against a real Postgres.

`education.education_predicate` is assembled as SQL text, and the interesting failure is
semantic rather than syntactic: **a null that fails a comparison instead of passing it**. In
SQL, `education_min = any(array['secondary'])` is `NULL` — not `false` — for a null column, and
a `WHERE` clause drops a row on `NULL` exactly as it drops it on `false`. So the `is null or`
half is not belt-and-braces, it is the only thing standing between a subscriber and an empty
digest, and inspecting the string cannot prove it works. These tests execute it.

That matters more here than for any other filter in this codebase: `education_min` is null for
~97% of live postings and for **100%** of the Czech and Slovak inventory, which carries no
description text for the classifier to read. A gate that dropped nulls would not narrow a
digest, it would empty it.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_education_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import education, geo, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "edutest-"

# posting_id -> education_min. **Most are null on purpose**: that is the shape of the real
# table, so the "unknown always passes" rule is exercised by the majority of the fixture set
# rather than by a single case bolted on at the end.
FIXTURES = {
    "unstated-1": None,
    "unstated-2": None,
    "unstated-3": None,
    "czech-no-description": None,
    "wants-secondary": "secondary",
    "wants-vocational": "vocational",
    "wants-bachelor": "bachelor",
    "wants-master": "master",
    "wants-doctorate": "doctorate",
}

UNSTATED = {"unstated-1", "unstated-2", "unstated-3", "czech-no-description"}


@pytest.fixture(scope="module", autouse=True)
def db():
    """Point the store at the test database and insert the fixture postings.

    Rows are namespaced and deleted afterwards, so this leaves nothing behind even if the
    database is a shared dev one.
    """
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    rows = [
        {"posting_id": PREFIX + pid, "source": "test", "url": f"https://x.test/{pid}",
         "title": "Data Analyst", "company": "Test", "description": "",
         "country_code": "CZ", "city": "prague", "remote_signal": False, "region": "cz",
         "location": "Praha", "work_mode": None, "education_min": level,
         "eligibility": "eligible", "seniority": "mid", "work_type": "permanent",
         "role_category": "data_analysis", "dedup_key": PREFIX + pid}
        for pid, level in FIXTURES.items()
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
    """The fixture postings this profile's education gate lets through."""
    sql, params = education.education_predicate(profile)
    with store.cursor() as cur:
        cur.execute(
            f"select posting_id from postings p where p.posting_id like %s and ({sql})",
            [PREFIX + "%", *params],
        )
        return {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}


# ------------------------------------------------------ the rule everything rests on ---

def test_a_posting_with_no_stated_requirement_always_passes():
    """`education_min = any(...)` evaluates to NULL for a null column, and a WHERE clause drops
    a NULL row exactly as it drops a false one. Without the `is null or` half this returns none
    of the four unstated fixtures — and in production, none of the ~20 000 postings that carry
    no requirement, including every Czech and Slovak one."""
    for level in education.LEVELS:
        got = allowed({"education_levels": [level]})
        assert UNSTATED <= got, (
            f"selecting only {level!r} dropped unstated postings: missing {UNSTATED - got}"
        )


def test_selecting_everything_returns_every_posting():
    """The default state, and the one every existing subscriber is migrated into."""
    assert allowed({"education_levels": list(education.LEVELS)}) == set(FIXTURES)
    assert allowed({}) == set(FIXTURES)


def test_a_profile_that_predates_the_migration_is_not_narrowed():
    """A row written before migration 014 has no `education_levels` key at all. It must behave
    exactly like "no preference", not like "accepts nothing"."""
    assert allowed({"education_levels": None}) == set(FIXTURES)
    assert allowed({"education_levels": []}) == set(FIXTURES)


# --------------------------------------------------------------- what it excludes ---

def test_a_requirement_above_what_was_selected_is_excluded():
    """The feature working: someone who ticked only high school and vocational stops being
    emailed the roles that spell out a degree."""
    got = allowed({"education_levels": ["secondary", "vocational"]})
    assert "wants-secondary" in got and "wants-vocational" in got
    assert "wants-bachelor" not in got
    assert "wants-master" not in got
    assert "wants-doctorate" not in got
    # ...and the unstated majority is still there, which is what keeps the digest full.
    assert UNSTATED <= got


def test_each_level_admits_exactly_itself_plus_the_unstated():
    """Set membership, not a ceiling — nothing in the code ranks the levels, so ticking one
    admits that one. Pinned because it is the difference between this filter and an intuitive
    reading of it, and the UI copy depends on which is true."""
    for level in education.LEVELS:
        assert allowed({"education_levels": [level]}) == UNSTATED | {f"wants-{level}"}


def test_an_unknown_level_from_a_stale_client_widens_rather_than_empties():
    """`clean_levels` drops what it does not recognise and an empty result widens to all five.
    A client sending a level this build has never heard of must not produce an empty digest."""
    assert allowed({"education_levels": ["nonsense"]}) == set(FIXTURES)


# ------------------------------------------------- composed into the real shortlist ---

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
    """`education_predicate` sitting unused would look identical in every test above — this
    runs the query the digest actually calls.

    The floor is lowered because this fixture set is deliberately smaller than
    `SHORTLIST_FLOOR`; without that every case here would take the widened path and the narrow
    one would never be exercised at all.
    """
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, meta = _shortlist({"education_levels": ["secondary"],
                            "role_categories": ["data_analysis"]})
    assert not meta["widened"], "this case is meant to exercise the narrow path"
    assert "wants-secondary" in ids
    assert "wants-bachelor" not in ids
    assert "wants-doctorate" not in ids
    assert UNSTATED <= ids


def test_widening_the_shortlist_does_not_re_admit_an_excluded_requirement():
    """The retrieval floor drops the *recall* predicate when a shortlist comes back too small.
    The education gate is deliberately built outside that branch, so widening must not hand
    back roles demanding a qualification the subscriber said they do not have. Asking for a
    category none of the fixtures carry is what forces the widened path here."""
    ids, meta = _shortlist({"education_levels": ["secondary"],
                            "role_categories": ["design"]})
    assert meta["widened"], "expected the floor to widen — the assertions below only mean " \
                            "something on the widened path"
    assert UNSTATED <= ids, f"widening dropped unstated postings: missing {UNSTATED - ids}"
    assert "wants-bachelor" not in ids
    assert "wants-master" not in ids
    assert "wants-doctorate" not in ids
