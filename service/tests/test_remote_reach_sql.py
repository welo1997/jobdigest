"""`scope_raw` / `remote_reach` / `reach_areas` against a real Postgres — column wiring, backfill.

Two things here cannot be checked by reading the code.

The first is the upsert. `store._UPSERT_SQL` binds **by position** through a hand-written
`%s` template, and these three columns were added into the middle of it, moving every column after
them. A shifted bind does not necessarily fail: it writes one text column into another and both
sides type-check. The only proof is to write a row and read the fields back by name.

The second is the backfill's agreement with the ingest path. `ingest.build_row` and
`backfill_remote_reach.run` each decide independently whether a posting is remote and what its
reach is, and they must not drift — a backfill that classifies rows the ingest would classify
differently leaves the column meaning two things at once, which is the failure the
`backfill_geo` / `backfill_education` runbooks exist to prevent.

    docker run --rm -d --name jd_test_pg -e POSTGRES_USER=jobmatch \\
        -e POSTGRES_PASSWORD=jobmatch -e POSTGRES_DB=jobmatch -p 5434:5432 pgvector/pgvector:pg16
    docker exec -i jd_test_pg psql -U jobmatch -d jobmatch < service/db/schema.sql
    TEST_DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5434/jobmatch python -m pytest \\
        service/tests/test_remote_reach_sql.py

Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import backfill_remote_reach, ingest, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "reachtest-"


class FakePosting:
    """The shape `ingest.build_row` reads — a `JobPosting` without importing the adapters."""

    def __init__(self, pid, location=None, description="", scope_raw=None, remote_signal=None):
        self.posting_id = PREFIX + pid
        self.source = "test"
        self.title = "Data Analyst"
        self.company = "Test"
        self.url = f"https://x.test/{pid}"
        self.description = description
        self.location = location
        self.country_code = None
        self.remote_signal = remote_signal
        self.salary_raw = None
        self.currency = None
        self.posted_at = None
        self.source_category = None
        self.scope_raw = scope_raw


# (posting, expected remote_reach, expected reach_areas)
CASES = [
    # A board-published scope field, which is the trusted layer.
    (FakePosting("wwr-anywhere", location="Anywhere in the World",
                 scope_raw="Anywhere in the World", remote_signal=True),
     "anywhere", ["eea", "na"]),
    (FakePosting("wwr-europe", location="Europe Only", scope_raw="Europe Only",
                 remote_signal=True), "region", ["eea"]),
    (FakePosting("himalayas-us", location="United States",
                 scope_raw="United States | UTC-10..UTC-5, UTC+14", remote_signal=True),
     "country", []),
    # Scope carried in the location text instead (Jobicy, Remotive, Working Nomads).
    (FakePosting("jobicy-emea", location="EMEA", remote_signal=True), "region", ["eea"]),
    # Reaches North America and nothing else — the row EU-International must never return.
    (FakePosting("na-only", location="Remote, Canada; Remote, US", remote_signal=True),
     "region", ["na"]),
    # APAC is a real region we offer no row for: `region`, but no area.
    (FakePosting("apac-only", location="APAC", remote_signal=True), "region", []),
    (FakePosting("remotive-worldwide", location="Worldwide", remote_signal=True),
     "anywhere", ["eea", "na"]),
    # Remote, but the posting never said how far. The majority case.
    (FakePosting("remote-silent", location="Remote", remote_signal=True), None, []),
    # Not remote: reach is a category error and must stay null even though the location text
    # would happily resolve to a country. Note `remote_signal=None` on the hybrid row rather than
    # False — a source's own remote claim deliberately outranks a bare "hybrid" in the location
    # (`geo.work_mode` only lets a *qualified* hybrid phrase overrule it), so passing True here
    # would make the row genuinely remote and test nothing.
    (FakePosting("berlin-onsite", location="Berlin, Germany", remote_signal=False), None, []),
    (FakePosting("prague-hybrid", location="Praha (hybrid)", remote_signal=None), None, []),
]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    store.upsert_postings([ingest.build_row(p) for p, _, _ in CASES])
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
        store._POOL = None


def stored() -> dict[str, dict]:
    with store.cursor() as cur:
        cur.execute(
            "select posting_id, location, scope_raw, remote_reach, reach_areas, "
            "       work_mode, remote_signal "
            "from postings where posting_id like %s", (PREFIX + "%",))
        return {r["posting_id"]: dict(r) for r in cur.fetchall()}


def test_the_upsert_writes_the_new_columns_to_the_right_places():
    """The positional-bind guard. `_UPSERT_SQL` gained three columns in the middle of its list.

    If a bind shifted, `scope_raw` would land in `education_min` or `remote_reach` in `salary_raw`
    — all text columns, so nothing raises and nothing looks wrong until a filter returns the
    wrong rows.
    """
    rows = stored()
    himalayas = rows[PREFIX + "himalayas-us"]
    assert himalayas["scope_raw"] == "United States | UTC-10..UTC-5, UTC+14"
    assert himalayas["remote_reach"] == "country"
    assert himalayas["location"] == "United States"
    # A posting whose board publishes no scope field stores null there, not an empty string.
    assert rows[PREFIX + "jobicy-emea"]["scope_raw"] is None
    assert rows[PREFIX + "jobicy-emea"]["remote_reach"] == "region"
    # The array column round-trips as an array, in `geo.REACH_AREAS` order.
    assert rows[PREFIX + "wwr-anywhere"]["reach_areas"] == ["eea", "na"]
    assert rows[PREFIX + "na-only"]["reach_areas"] == ["na"]
    # And nothing leaked into the neighbouring columns.
    with store.cursor() as cur:
        cur.execute("select count(*) as n from postings where posting_id like %s "
                    "and (education_min is not null or salary_raw is not null)", (PREFIX + "%",))
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("posting, expected, expected_areas", CASES,
                         ids=[p.posting_id for p, _, _ in CASES])
def test_ingest_stores_the_expected_reach(posting, expected, expected_areas):
    row = stored()[posting.posting_id]
    assert row["remote_reach"] == expected
    # Empty is stored as NULL, not as an empty array — see `upsert_postings`.
    assert (row["reach_areas"] or []) == expected_areas


def test_reach_is_null_for_everything_that_is_not_fully_remote():
    """Berlin on-site resolves to Germany all day long — and must still have no reach.

    Deriving one anyway would fill the column with trivially-`country` rows, and the coverage
    number would then describe the corpus's on-site half rather than the question the column asks.
    """
    rows = stored()
    for pid in ("berlin-onsite", "prague-hybrid"):
        row = rows[PREFIX + pid]
        assert row["work_mode"] != "remote"
        assert row["remote_reach"] is None
        assert row["reach_areas"] is None, \
            "an on-site job appeared under an international heading"


def test_the_backfill_agrees_with_the_ingest_and_changes_nothing():
    """Run over rows the ingest just wrote, the backfill must be a no-op.

    This is the drift check. Any disagreement between `ingest.build_row` and
    `backfill_remote_reach.run` shows up here as a non-zero `changed`, and it means the column
    holds one answer for freshly-ingested rows and another for backfilled ones.
    """
    scanned, changed = backfill_remote_reach.run(batch=500)
    assert scanned >= len(CASES)
    assert changed == 0


def test_the_backfill_clears_a_row_that_stopped_being_remote():
    """A reach left behind on a row that is no longer remote is a stale promise.

    `work_mode` is re-derived on every ingest and can flip — that is what `backfill_geo` exists
    for, and jobs.cz's 3 744 mislabelled hybrid rows are why. The reach has to follow it down.
    """
    pid = PREFIX + "wwr-anywhere"
    with store.cursor(commit=True) as cur:
        cur.execute("update postings set remote_signal = false, work_mode = 'hybrid' "
                    "where posting_id = %s", (pid,))
    try:
        _scanned, changed = backfill_remote_reach.run(batch=500)
        assert changed == 1
        assert stored()[pid]["remote_reach"] is None
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("update postings set remote_signal = true, work_mode = 'remote' "
                        "where posting_id = %s", (pid,))
        backfill_remote_reach.run(batch=500)
    assert stored()[pid]["remote_reach"] == "anywhere"
