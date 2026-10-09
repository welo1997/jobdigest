"""Who the subscriber watchdog calls starved — against a real Postgres.

`store.starved_profiles` is the query behind the 09:00 alert. Its window has to follow each
subscriber's cadence: a weekly subscriber is emailed on Mondays only, so a flat three days
fired on every healthy weekly profile from Thursday on (2026-10-09, the only alert that day).
An alert that cries wolf weekly gets filtered into a folder nobody opens, which is the one
outcome the watchdog exists to prevent.

Same harness and the same reason as `test_geo_sql.py` — see its docstring for the throwaway
database recipe. Skipped when TEST_DATABASE_URL is unset; CI fails if these go back to
skipping.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")

PREFIX = "wdtest-"


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
        cur.execute("delete from profiles where email like %s", (PREFIX + "%",))


def _profile(name: str, frequency: str, last_digest_days_ago: float | None) -> str:
    now = datetime.now(timezone.utc)
    last = None if last_digest_days_ago is None else now - timedelta(days=last_digest_days_ago)
    with store.cursor(commit=True) as cur:
        cur.execute(
            "insert into profiles (email, label, status, frequency, created_at, last_digest_at) "
            "values (%s, %s, 'active', %s, %s, %s) returning id::text as id",
            (f"{PREFIX}{name}@example.test", f"wd {name}", frequency,
             now - timedelta(days=60), last),
        )
        return cur.fetchone()["id"]


def _starved(days: int = 3) -> set[str]:
    return {str(r["id"]) for r in store.starved_profiles(days)}


def test_weekly_subscriber_sent_four_days_ago_is_not_starved():
    """The 2026-10-09 false alarm: sent Monday, checked Friday."""
    pid = _profile("weekly-ok", "weekly", 4)
    assert pid not in _starved()


def test_weekly_subscriber_who_missed_a_monday_is_starved():
    pid = _profile("weekly-missed", "weekly", 11)
    assert pid in _starved()


def test_daily_subscriber_keeps_the_flat_window():
    quiet = _profile("daily-quiet", "daily", 4)
    fine = _profile("daily-fine", "daily", 1)
    starved = _starved()
    assert quiet in starved
    assert fine not in starved


def test_weekdays_subscriber_survives_a_weekend_but_not_a_week():
    weekend = _profile("weekdays-weekend", "weekdays", 4)
    week = _profile("weekdays-week", "weekdays", 6)
    starved = _starved()
    assert weekend not in starved
    assert week in starved


def test_never_sent_old_profile_is_starved_at_any_cadence():
    pid = _profile("weekly-never", "weekly", None)
    assert pid in _starved()
