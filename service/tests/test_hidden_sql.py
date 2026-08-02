"""Hiding a job, executed against a real Postgres.

`test_webapp_lifecycle.py` pins the endpoints against a fake store, which proves the HTTP
contract and nothing about the queries underneath it. The queries are where this feature can
actually be wrong: `matched_jobs` and `match_count` carry the hidden filter *separately*, so
one of them drifting renders "12 matches" above a list that can only ever reach 10; the
digest reads the same function, so a filter that silently stopped applying would keep
emailing a job someone already applied to; and `set_matches_hidden` is authorised solely by
`profile_id` appearing in its WHERE clause.

Same harness and the same reason as `test_geo_sql.py` — see its docstring for the throwaway
database recipe. Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "hidetest-"
POSTINGS = [f"{PREFIX}{i}" for i in range(5)]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    # Inserted directly rather than through `upsert_postings`: the postings here are only
    # scaffolding for the `matches` queries under test, and going through the ingest path
    # would couple this file to every future column added to it.
    with store.cursor(commit=True) as cur:
        for pid in POSTINGS:
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, region, "
                "  eligibility, seniority, work_type, role_category, dedup_key, is_active) "
                "values (%s,'test',%s,'Data Analyst','Test','cz','eligible','mid',"
                "  'permanent','data_analysis',%s,true) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", pid),
            )
    try:
        yield
    finally:
        with store.cursor(commit=True) as cur:
            cur.execute("delete from postings where posting_id like %s", (PREFIX + "%",))
            cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _profile(email: str) -> str:
    """A subscriber with one scored match per fixture posting, best-first by index."""
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute("insert into profiles (email, label) values (%s, 'hide test') "
                    "returning id::text as id", (email,))
        pid = cur.fetchone()["id"]
    for i, posting in enumerate(POSTINGS):
        store.upsert_match(pid, posting, 10 - i, "why")
    return pid


@pytest.fixture
def profile():
    return _profile(f"{PREFIX}a@example.test")


def visible(pid: str) -> list[str]:
    return [j["posting_id"] for j in store.matched_jobs(pid, limit=50)]


def hidden(pid: str) -> list[str]:
    return [j["posting_id"] for j in store.matched_jobs(pid, limit=50, hidden=True)]


def test_a_hidden_job_leaves_the_list_and_the_count_agrees(profile):
    """The header and the rows must never be able to disagree — they are two queries."""
    assert visible(profile) == POSTINGS
    assert store.match_count(profile) == 5 and store.match_count(profile, hidden=True) == 0

    assert store.set_matches_hidden(profile, [POSTINGS[0], POSTINGS[3]], True) == 2

    assert visible(profile) == [POSTINGS[1], POSTINGS[2], POSTINGS[4]]
    assert store.match_count(profile) == 3
    assert store.match_count(profile, hidden=True) == 2
    assert sorted(hidden(profile)) == sorted([POSTINGS[0], POSTINGS[3]])


def test_hiding_is_never_a_delete(profile):
    """The match row survives with its score and summary, which is what makes unhide real."""
    store.set_matches_hidden(profile, [POSTINGS[1]], True)
    row = store.matched_jobs(profile, limit=50, hidden=True)[0]
    assert row["posting_id"] == POSTINGS[1]
    assert row["score"] == 9 and row["summary"] == "why"

    assert store.set_matches_hidden(profile, [POSTINGS[1]], False) == 1
    assert visible(profile) == POSTINGS                     # back in its old rank
    assert store.match_count(profile, hidden=True) == 0


def test_a_re_score_cannot_resurrect_a_hidden_job(profile):
    """The matcher upserts scores nightly and must not write `status` while doing it —
    otherwise every hidden job comes back the next morning, in an email."""
    store.set_matches_hidden(profile, [POSTINGS[2]], True)
    store.upsert_match(profile, POSTINGS[2], 10, "re-scored")
    assert POSTINGS[2] not in visible(profile)
    assert hidden(profile) == [POSTINGS[2]]


def test_one_subscriber_cannot_hide_anothers_match(profile):
    """`profile_id` in the WHERE clause is the authorisation, not a convenience."""
    other = _profile(f"{PREFIX}b@example.test")
    assert store.set_matches_hidden(profile, POSTINGS, True) == 5
    assert visible(other) == POSTINGS                       # untouched
    assert store.match_count(other, hidden=True) == 0


def test_unknown_ids_and_repeat_calls_change_nothing(profile):
    """A stale page re-submitting what it already hid is ordinary traffic, not an error."""
    assert store.set_matches_hidden(profile, [POSTINGS[0]], True) == 1
    assert store.set_matches_hidden(profile, [POSTINGS[0]], True) == 0
    assert store.set_matches_hidden(profile, ["no-such-posting"], True) == 0
    assert store.set_matches_hidden(profile, [], True) == 0
    assert store.match_count(profile, hidden=True) == 1


def test_hidden_paging_is_deterministic_across_a_bulk_hide(profile):
    """One bulk hide stamps a single `updated_at` across the batch, so `posting_id` is what
    keeps the ordering total — without it a row could appear on two pages or on none."""
    store.set_matches_hidden(profile, POSTINGS, True)
    first = [j["posting_id"] for j in store.matched_jobs(profile, limit=2, hidden=True)]
    second = [j["posting_id"] for j in store.matched_jobs(profile, limit=2, offset=2, hidden=True)]
    third = [j["posting_id"] for j in store.matched_jobs(profile, limit=2, offset=4, hidden=True)]
    walked = first + second + third
    assert len(set(walked)) == 5 and sorted(walked) == sorted(POSTINGS)
