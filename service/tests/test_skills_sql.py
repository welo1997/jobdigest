"""What the skills facet actually stores and filters — run against a real Postgres.

`service/skills.py` is unit-tested in `test_skills.py`; this file tests the *storage and query*
half, the part a string cannot show you:

  * `upsert_postings` binds `skills` through a 23-placeholder positional template and adapts a
    Python list to `text[]` — one shifted `%s` would write it into the wrong column, and an
    empty list must land as NULL, not an untyped `ARRAY[]` Postgres refuses to coerce;
  * the facet predicate `skills && array[...]` is index-backed by `idx_postings_skills` and
    only *narrows* — null and empty rows are never matched by a positive selection but are
    never excluded when no skill is selected (the null-passes rule shared with geo/education);
  * the `unnest` facet-count query the search API will build its chips from.

Needs a throwaway database, and deliberately does NOT read `DATABASE_URL`: this file inserts
rows, and `DATABASE_URL` on a developer's machine can point at the live box.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 postgres:16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_skills_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "skilltest-"

# posting_id -> skills as build_row would produce them (a list, possibly empty).
FIXTURES = {
    "python-sql": ["python", "sql"],
    "python-only": ["python"],
    "react-ts": ["react", "typescript"],
    "empty": [],  # classified, named no skill -> must store as NULL
    "unscanned": None,  # never classified -> NULL
}


@pytest.fixture(scope="module", autouse=True)
def db():
    """Point the store at the test database and upsert the fixture postings through the real
    `upsert_postings` path — so the positional template and list->text[] adaptation are what is
    under test, not a hand-written INSERT."""
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    rows = [
        {
            "posting_id": PREFIX + pid,
            "source": "test",
            "url": f"https://x.test/{pid}",
            "title": "Engineer",
            "company": "Test",
            "description": "",
            "role_category": "software_engineering",
            "dedup_key": PREFIX + pid,
            "skills": sk,
        }
        for pid, sk in FIXTURES.items()
    ]
    store.upsert_postings(rows)
    yield
    with store.cursor(commit=True) as cur:
        cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
    if previous_dsn is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_dsn
    store._POOL = None


def _skills(pid: str):
    with store.cursor() as cur:
        cur.execute(
            "select skills from postings where posting_id = %s", (PREFIX + pid,)
        )
        return cur.fetchone()["skills"]


# --------------------------------------------------------------- storage round-trip ---


def test_a_list_round_trips_as_text_array():
    assert _skills("python-sql") == ["python", "sql"]
    assert _skills("react-ts") == ["react", "typescript"]


def test_an_empty_list_is_stored_as_null_not_an_empty_array():
    """The empty->null rule in `upsert_postings`. Both NULL and `'{}'` would be equivalent for
    the facet, but psycopg2 renders `[]` as an untyped array Postgres cannot coerce, so the
    write must send NULL — and this proves it does rather than raising."""
    assert _skills("empty") is None
    assert _skills("unscanned") is None


# --------------------------------------------------------------- the facet predicate ---


def _match(*wanted: str) -> set[str]:
    with store.cursor() as cur:
        cur.execute(
            "select posting_id from postings "
            "where posting_id like %s and skills && %s",
            (PREFIX + "%", list(wanted)),
        )
        return {r["posting_id"].removeprefix(PREFIX) for r in cur.fetchall()}


def test_a_positive_selection_matches_only_rows_that_have_the_skill():
    assert _match("python") == {"python-sql", "python-only"}
    assert _match("typescript") == {"react-ts"}


def test_selecting_several_skills_is_an_or_over_containment():
    # `&&` is array-overlap: a posting matches if it has ANY of the selected skills.
    assert _match("python", "react") == {"python-sql", "python-only", "react-ts"}


def test_null_and_empty_are_never_matched_by_a_positive_selection():
    """A facet only narrows. The unclassified rows must never surface for a chosen skill — but
    they are also never removed when no skill is selected (no predicate is added), which is the
    same null-passes contract as geo/education."""
    for skill in ("python", "sql", "react", "typescript", "go"):
        assert "empty" not in _match(skill)
        assert "unscanned" not in _match(skill)


def test_an_unknown_skill_matches_nothing():
    assert _match("cobol") == set()


# --------------------------------------------------------------- facet counts ---


def test_unnest_facet_counts_are_available_for_chips():
    """The shape the search API builds filter chips from: skill -> how many postings name it.
    Null/empty rows contribute nothing (unnest of NULL yields no rows)."""
    with store.cursor() as cur:
        cur.execute(
            "select s as skill, count(*) as n "
            "from postings, unnest(skills) as s "
            "where posting_id like %s group by s order by n desc, s",
            (PREFIX + "%",),
        )
        counts = {r["skill"]: r["n"] for r in cur.fetchall()}
    assert counts["python"] == 2
    assert counts["sql"] == 1
    assert counts["react"] == 1
    assert counts["typescript"] == 1
