"""What the language gate actually lets through — run against a real Postgres.

The sixth axis, same argument as `test_experience_sql.py`: `language = any(%s)` is `NULL` — not
`false` — for a null column, and a `WHERE` clause drops a NULL row exactly as it drops a false
one. The `language is null or language = 'en' or ...` shape of `language.language_predicate` is
the only thing standing between a subscriber and a digest emptied of every posting whose text we
could not read (most of the CZ/SK title-only corpus) plus every English one (the bulk of
inventory). A string inspection cannot prove it works; these tests execute it — on the predicate
alone, and on the real shortlist query, narrow and widened.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_language_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import geo, language, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "langtest-"

# posting_id -> language. Nulls on purpose: that is the shape of the real table (short/ambiguous
# text), so "unknown always passes" is exercised by part of the fixture set.
FIXTURES = {
    "unstated-1": None,
    "unstated-2": None,
    "english-1": "en",
    "czech-1": "cs",
    "russian-1": "ru",
    "german-1": "de",
}

UNSTATED = {"unstated-1", "unstated-2"}


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
         "location": "Praha", "work_mode": None, "language": lang,
         "eligibility": "eligible", "seniority": "mid", "work_type": "permanent",
         "role_category": "data_analysis", "dedup_key": PREFIX + pid}
        for pid, lang in FIXTURES.items()
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
    """The fixture postings this profile's language gate lets through."""
    sql, params = language.language_predicate(profile)
    where = "true" if sql == "true" else sql
    with store.cursor() as cur:
        cur.execute(
            f"select posting_id from postings p where p.posting_id like %s and ({where})",
            [PREFIX + "%", *params],
        )
        return {r["posting_id"][len(PREFIX):] for r in cur.fetchall()}


# ------------------------------------------------------ the rules everything rests on ---

def test_a_posting_with_no_detected_language_always_passes():
    """`language = any(...)` is NULL for a null column, and a WHERE clause drops a NULL row
    exactly as it drops a false one. Without the `is null or` half, the whole short/ambiguous
    majority — most of the CZ/SK corpus — vanishes from the gate."""
    for langs in (["cs"], ["cs", "en"], ["ru"], ["de", "fr"]):
        got = allowed({"understood_languages": langs})
        assert UNSTATED <= got, f"understood={langs} dropped unstated: missing {UNSTATED - got}"


def test_english_always_passes_even_when_not_declared():
    """English is the market's lingua franca and the bulk of inventory; the gate hardcodes it so
    a subscriber who only declared Czech still receives English postings."""
    got = allowed({"understood_languages": ["cs"]})
    assert "english-1" in got, "an English posting was refused for a cs-only subscriber"


def test_silence_widens():
    """A profile that declared no languages — every existing subscriber — gets no filter."""
    assert allowed({}) == set(FIXTURES)
    assert allowed({"understood_languages": None}) == set(FIXTURES)
    assert allowed({"understood_languages": []}) == set(FIXTURES)
    assert allowed({"understood_languages": ["zz"]}) == set(FIXTURES)  # all-unknown -> empty


def test_a_foreign_language_is_excluded():
    """The feature working: a cs+en reader keeps Czech, English and the unknown majority, and
    loses the confidently-Russian and confidently-German ads."""
    got = allowed({"understood_languages": ["cs", "en"]})
    assert {"english-1", "czech-1"} <= got
    assert UNSTATED <= got
    assert "russian-1" not in got
    assert "german-1" not in got


def test_declaring_a_language_admits_it():
    """A reader who adds Russian gets the Russian ad back — the set is additive."""
    got = allowed({"understood_languages": ["cs", "en", "ru"]})
    assert "russian-1" in got
    assert "german-1" not in got


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
    """`language_predicate` sitting unused would look identical in every test above — this runs
    the query the digest actually calls, on the narrow path."""
    monkeypatch.setattr(store, "SHORTLIST_FLOOR", 1)
    ids, meta = _shortlist({"understood_languages": ["cs", "en"],
                            "role_categories": ["data_analysis"]})
    assert not meta["widened"], "this case is meant to exercise the narrow path"
    assert {"english-1", "czech-1"} <= ids
    assert "russian-1" not in ids
    assert "german-1" not in ids
    assert UNSTATED <= ids


def test_widening_the_shortlist_does_not_re_admit_a_foreign_language():
    """The gate lives in `_hard_gate`, outside the recall branch, so the retrieval floor's
    widened pass must not hand back a Russian ad a cs/en reader cannot read."""
    ids, meta = _shortlist({"understood_languages": ["cs", "en"],
                            "role_categories": ["design"]})
    assert meta["widened"], "expected the floor to widen — the assertions below only mean " \
                            "something on the widened path"
    assert UNSTATED <= ids, f"widening dropped unstated postings: missing {UNSTATED - ids}"
    assert "english-1" in ids
    assert "russian-1" not in ids
    assert "german-1" not in ids
