"""Who the export serves first, and who we notice it missed — against a real Postgres.

Two queries, one concern: **what happens when a matcher run cannot finish everybody.**

`sendable_profiles` decides the order profiles appear in `shortlists.json`, so it decides who
is at the end of the file — and the end of the file is what a finite matcher budget (a context
window, a subscription's usage cap, a batch that dies half way) silently drops. `nulls first`
is what keeps a subscriber waiting on their *first* digest out of that position. It reads like
cosmetic SQL and it is a priority policy.

`exported_profile_ids` is the other half: the denominator that lets `matcher.coverage_gap`
say a profile was asked about and never answered for. `picks_n` cannot say it — migration 011
declares the column `not null default 0`, so "absent from picks.json" and "the model returned
nothing" are the same stored value, and `service/watchdog.py` reads the second meaning.

Same harness and the same reason as `test_geo_sql.py` — see its docstring for the throwaway
database recipe. Skipped when TEST_DATABASE_URL is unset; CI fails if these go back to
skipping, because a suite that silently drops its most expensive tests still shows green.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "covtest-"


@pytest.fixture(autouse=True)
def db():
    previous_dsn = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    _cleanup()
    try:
        yield
    finally:
        _cleanup()
        store._POOL = None
        if previous_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_dsn


def _cleanup() -> None:
    with store.cursor(commit=True) as cur:
        # digest_runs cascades on profile delete, so this clears both tables.
        cur.execute("delete from profiles where email like %s", (PREFIX + "%",))
        cur.execute("delete from suppression where email like %s", (PREFIX + "%",))


def _profile(name: str, last_digest_at: datetime | None, status: str = "active") -> str:
    with store.cursor(commit=True) as cur:
        cur.execute(
            "insert into profiles (email, label, status, last_digest_at) "
            "values (%s, %s, %s, %s) returning id::text as id",
            (f"{PREFIX}{name}@example.test", f"cov {name}", status, last_digest_at),
        )
        return cur.fetchone()["id"]


def _order(ids: list[str]) -> list[str]:
    """Positions of our own profiles within the real query's output, in its order.

    Filtered to `ids` rather than asserted as the whole result: the query returns every
    sendable profile in the database, and a leftover row from another test must not decide
    whether this one passes.
    """
    mine = set(ids)
    return [str(p["id"]) for p in store.sendable_profiles() if str(p["id"]) in mine]


# ------------------------------------------------ the truncation priority order ---

def test_a_subscriber_awaiting_their_first_digest_is_served_before_anyone_else():
    """`nulls first` is the whole point: whatever cuts a run short takes the tail of the
    file, so a never-yet-sent subscriber must never be in the tail. Sorting by `created_at`
    — the natural-looking "cleanup" — puts the newest signup last, which is exactly the
    person whose first impression is at stake."""
    now = datetime.now(timezone.utc)
    veteran = _profile("veteran", now - timedelta(hours=2))
    newcomer = _profile("newcomer", None)

    assert _order([veteran, newcomer]) == [newcomer, veteran]


def test_among_subscribers_who_have_been_sent_to_the_longest_wait_goes_first():
    now = datetime.now(timezone.utc)
    recent = _profile("recent", now - timedelta(hours=1))
    stale = _profile("stale", now - timedelta(days=4))

    assert _order([recent, stale]) == [stale, recent]


def test_never_sent_beats_every_stale_subscriber_however_stale():
    """Guards the specific regression of swapping `nulls first` for `nulls last` while
    keeping the ascending sort — which reads as harmless and inverts the policy."""
    ancient = _profile("ancient", datetime(2020, 1, 1, tzinfo=timezone.utc))
    newcomer = _profile("newcomer2", None)

    assert _order([ancient, newcomer]) == [newcomer, ancient]


def test_paused_and_unsubscribed_profiles_are_not_served_at_all():
    """Not about ordering, but it shares the query: a filter lost here would email someone
    who asked us to stop."""
    active = _profile("active", None)
    unsubbed = _profile("gone", None, status="unsubscribed")
    paused = _profile("paused", None)
    with store.cursor(commit=True) as cur:
        cur.execute("update profiles set paused_until = now() + interval '1 day' "
                    "where id = %s", (paused,))

    assert _order([active, unsubbed, paused]) == [active]


def test_a_suppressed_address_is_not_served():
    active = _profile("ok", None)
    blocked = _profile("blocked", None)
    with store.cursor(commit=True) as cur:
        cur.execute("select email from profiles where id = %s", (blocked,))
        store.add_suppression(cur.fetchone()["email"], "test")

    assert _order([active, blocked]) == [active]


# --------------------------------------------- the coverage-check denominator ----

def _record_export(profile_id: str, shortlist_n: int, day_offset: int = 0) -> None:
    if day_offset == 0:
        store.record_digest_run(profile_id, shortlist_n=shortlist_n)
        return
    with store.cursor(commit=True) as cur:
        cur.execute("insert into digest_runs (day, profile_id, shortlist_n) "
                    "values (current_date - %s::int, %s, %s)",
                    (day_offset, profile_id, shortlist_n))


def test_exported_ids_are_the_profiles_the_matcher_was_asked_about():
    served = _profile("served", None)
    _record_export(served, shortlist_n=120)

    assert store.exported_profile_ids() >= {served}


def test_a_profile_with_an_empty_shortlist_is_not_a_coverage_gap():
    """`export_shortlists` records the run and *then* skips the profile, so it is legitimately
    absent from picks.json. Counting it as uncovered would fire the alert every single day for
    someone whose real problem is retrieval — which the watchdog already reports as RETRIEVAL.
    That is how an alert channel gets filtered into a folder nobody opens."""
    starved = _profile("starved", None)
    _record_export(starved, shortlist_n=0)

    assert starved not in store.exported_profile_ids()


def test_yesterdays_export_is_not_todays_denominator():
    """Scoped to `current_date`. Without this a profile that unsubscribed or paused after
    yesterday's run would be reported missing from today's file for ever."""
    old = _profile("old", None)
    _record_export(old, shortlist_n=120, day_offset=1)

    assert old not in store.exported_profile_ids()


def test_a_profile_exported_today_and_yesterday_is_counted_once():
    both = _profile("both", None)
    _record_export(both, shortlist_n=120, day_offset=1)
    _record_export(both, shortlist_n=120)

    ids = store.exported_profile_ids()
    assert both in ids
    assert len([i for i in ids if i == both]) == 1      # it is a set; pin the contract
