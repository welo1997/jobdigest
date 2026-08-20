"""`deactivate_old` — the content-age staleness axis, executed against a real Postgres.

`deactivate_stale` asks whether the *source* still lists a job; `deactivate_old` asks whether the
job's *own* `posted_at` is ancient. A national register and several ATS boards re-list roles for
months or years after they are filled (measured 2026-08-20: ~1 300 active rows older than a year,
oldest from 2016), so only the second axis catches them. Three properties are load-bearing and a
`WHERE` typo would make any of them silently wrong, so they run against a real database:

  - an old `posted_at` is deactivated;
  - a **NULL `posted_at` is left active** — three sources never provide a date, and an age we
    cannot read is not an age we act on (the same abstain-on-unknown polarity as geo/liveness);
  - a recent `posted_at` is left active, and nothing is ever deleted.

Same harness as `test_hidden_sql.py`. Skipped when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import os

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "agetest-"
# (posting_id suffix -> posted_at SQL). old is >365d, recent is well within, null is unknown.
POSTINGS = {
    "old": "(current_date - 500)",       # ~16 months — the mpsv/úřad-práce case
    "borderline-young": "(current_date - 300)",   # under a year — must survive a 365d gate
    "recent": "(current_date - 3)",
    "null": "null",                      # startupjobs/cocuma-shaped: no date, must stay active
}


@pytest.fixture(scope="module", autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None

    with store.cursor(commit=True) as cur:
        for suffix, posted_sql in POSTINGS.items():
            cur.execute(
                "insert into postings (posting_id, source, url, title, company, "
                "  role_category, dedup_key, posted_at, is_active) "
                f"values (%s,'mpsv',%s,'Data Engineer','Acme','data_engineering',%s,{posted_sql},true) "
                "on conflict (posting_id) do nothing",
                (f"{PREFIX}{suffix}", f"https://x.test/{PREFIX}{suffix}", f"{PREFIX}{suffix}"),
            )
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


def _active() -> dict[str, bool]:
    with store.cursor() as cur:
        cur.execute("select posting_id, is_active from postings where posting_id like %s",
                    (PREFIX + "%",))
        return {r["posting_id"].removeprefix(PREFIX): r["is_active"] for r in cur.fetchall()}


def test_deactivate_old_drops_only_the_ancient_dated_rows():
    changed = store.deactivate_old(days=365)
    assert changed == 1, "exactly the one row past a year should flip"
    state = _active()
    assert state["old"] is False
    assert state["borderline-young"] is True     # 300 days < 365, kept
    assert state["recent"] is True
    assert state["null"] is True                 # unknown date is never acted on
    assert set(state) == {"old", "borderline-young", "recent", "null"}, "nothing was deleted"


def test_deactivate_old_is_idempotent():
    # The old row is already inactive after the test above (module-scoped fixture), so a second
    # pass at the same threshold changes nothing.
    assert store.deactivate_old(days=365) == 0


def test_a_generous_threshold_touches_nothing():
    # A sign error (posted_at > now - interval) would deactivate everything; this pins the
    # direction. 10 000 days is older than any fixture row.
    assert store.deactivate_old(days=10_000) == 0
    assert _active()["borderline-young"] is True
