"""The digest's candidate window, executed against a real Postgres.

`build_digest` asks `matched_jobs` for `limit * 6` rows and then drops the already-emailed
ones. Which rows come back is decided by SQL, so this can only be tested against SQL — a
fake store returns whatever the fake was told to return, and would have shown green
throughout the bug this file exists for.

**The bug, measured in production on 2026-08-07.** Already-sent jobs were filtered in Python
*after* the window was cut, and their postings stay `is_active` for ever, so every job a
subscriber had been emailed permanently occupied a candidate slot. A subscriber 15 days into
a daily digest had 21 of 30 slots filled with jobs already in their inbox; the 9 survivors
were all cross-id duplicates that `dedupe_key` then collapsed; the email went out with **1
job instead of 5, while 25 unsent matches scoring >= 6 sat outside the window.** Nothing
raised, `sendable_n` recorded 1, and a throttled digest is indistinguishable from a quiet
day. The ceiling tightens the longer someone subscribes, so it reads as the product going
quiet on exactly its longest-running subscribers.

Two properties are load-bearing and pull in opposite directions, which is why both are here:
the digest must *not* see sent jobs, and `/matches` must *still* see them — it is the
complete record, and `match_count` carries the filters separately from `matched_jobs`.

Same harness and the same reason as `test_geo_sql.py` and `test_hidden_sql.py` — see the
former's docstring for the throwaway database recipe. Skipped when TEST_DATABASE_URL is
unset, and CI fails if it goes back to skipping.
"""

from __future__ import annotations

import os

import pytest

from service import digest, store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "windowtest-"
#: More than `DEFAULT_LIMIT * 6` (30), so the window is genuinely a window. Distinct
#: companies and titles throughout — `dedupe_key` collapsing them would confound the count
#: being measured here, and the dedupe behaviour has its own file.
N = 40
POSTINGS = [f"{PREFIX}{i:02d}" for i in range(N)]


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for i, pid in enumerate(POSTINGS):
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, city, "
                "  region, eligibility, seniority, work_type, role_category, dedup_key, "
                "  is_active) "
                "values (%s,'test',%s,%s,%s,'prague','cz','eligible','mid',"
                "  'permanent','data_analysis',%s,true) "
                "on conflict (posting_id) do nothing",
                (pid, f"https://x.test/{pid}", f"Data Analyst {i}", f"Company {i}", pid),
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


#: Every match scores the same, which makes `POSTINGS[:30]` *exactly* the window.
#: `matched_jobs` orders by `score desc, posted_at desc nulls last, posting_id`, so with one
#: score and a null `posted_at` the zero-padded id is the whole ordering — deterministic, and
#: it lets a test say "the top 30 have all been sent" and mean it. A first attempt cycled the
#: scores (`10 - i % 5`); that scattered the sent rows through the ordering, left unsent
#: high scorers inside the window, and the suite passed against the *unfixed* code. The
#: shape being reproduced is the real one: the best matches get emailed first, so the sent
#: rows are the top of the ordering, not a sample of it.
SCORE = 8


@pytest.fixture
def profile():
    """A subscriber matched to all 40 postings, every one a strong (>= EMAIL_MIN_SCORE) fit."""
    email = f"{PREFIX}a@example.test"
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email = %s", (email,))
        cur.execute("insert into profiles (email, label) values (%s, 'window test') "
                    "returning id::text as id", (email,))
        pid = cur.fetchone()["id"]
    for posting in POSTINGS:
        store.upsert_match(pid, posting, SCORE, "why")
    return {"id": pid, "email": email}


def _send(profile_id: str, postings: list[str]) -> None:
    store.record_sends(profile_id, [(p, 9) for p in postings])


# --------------------------------------------------------------------------------------


def test_sent_jobs_do_not_consume_the_candidate_window(profile):
    """The regression itself: 30 sent jobs must not starve a digest of 10 unsent ones.

    Mutation check for this file — drop `exclude_sent=True` in `build_digest` and this goes
    red with 0 jobs, which is exactly what production sent.
    """
    _send(profile["id"], POSTINGS[:30])          # the whole window, all already emailed

    jobs = digest.build_digest(profile)

    assert len(jobs) == digest.DEFAULT_LIMIT, (
        "the digest fell back to the leftovers of a window full of already-sent jobs"
    )
    assert all(j["posting_id"] in POSTINGS[30:] for j in jobs)
    assert all((j.get("score") or 0) >= digest.EMAIL_MIN_SCORE for j in jobs)


def test_the_window_does_not_tighten_as_history_grows(profile):
    """A subscriber 5 days in and one 30 days in must get the same-sized digest.

    The old behaviour degraded smoothly with tenure, which is why it survived: every
    individual day looked like an ordinary quiet one.
    """
    # 26 is included deliberately: the old behaviour did not fail at a cliff, it *tapered*
    # (4 jobs, then 1, then 0), and a taper is what let it run unnoticed for weeks.
    for sent_so_far in (0, 10, 20, 26, 30):
        with store.cursor(commit=True) as cur:
            cur.execute("delete from digest_sends where profile_id = %s", (profile["id"],))
        _send(profile["id"], POSTINGS[:sent_so_far])
        jobs = digest.build_digest(profile)
        assert len(jobs) == digest.DEFAULT_LIMIT, f"starved after {sent_so_far} sends"


def test_matches_page_still_shows_everything_and_the_count_agrees(profile):
    """`exclude_sent` must never leak into the web path.

    `/matches` is the complete record — that is what makes email suppression safe — and
    `match_count` carries the filter separately, so a leak renders "40 matches" above a
    list that stops at 10.
    """
    _send(profile["id"], POSTINGS[:30])

    assert len(store.matched_jobs(profile["id"], limit=100)) == N
    assert store.match_count(profile["id"]) == N
    assert len(store.matched_jobs(profile["id"], limit=100, exclude_sent=True)) == N - 30


def test_a_job_is_still_never_emailed_twice(profile):
    """The guarantee the window fix must not trade away."""
    first = digest.build_digest(profile)
    _send(profile["id"], [j["posting_id"] for j in first])
    second = digest.build_digest(profile)

    assert {j["posting_id"] for j in first} & {j["posting_id"] for j in second} == set()


def test_hidden_jobs_are_still_excluded_from_the_digest(profile):
    """`hidden` and `exclude_sent` are both filters on the same query — adding one must not
    have dropped the other."""
    store.set_matches_hidden(profile["id"], POSTINGS[:3], True)

    jobs = digest.build_digest(profile)

    assert all(j["posting_id"] not in POSTINGS[:3] for j in jobs)
