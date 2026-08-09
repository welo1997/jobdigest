"""The categorisation watchdog must be quiet when things are fine and loud when they are not.

Categorisation degrades silently: no exception, no failed timer, no changed row count — a
subscriber's shortlist just gets thinner, which from outside looks like a quiet week. This is
the third watchdog in this repo for the same reason the other two exist, one stage apart.

The properties pinned here are the ones that were **measured against production before the
watchdog was written**, because both obvious designs turned out to be unusable and the next
person will reach for them again:

  - **Drift is judged on the whole active corpus, never on the day's new rows.** New-row
    share swung 14.9% → 59.7% over eight days purely because the mix of sources that churned
    changed; one source rebuild moved it 27 points alone. A watchdog on that number is a
    random-number generator with an alert attached.
  - **It refuses to judge without history.** A trailing median over one or two days is a
    guess, and a threshold tuned on a guess fires on healthy days — the exact day-one false
    alarms `source_watchdog.py` records.
  - **Today's snapshot is recorded after the check**, so a number can never enter its own
    baseline and quietly normalise a regression.
  - **The unmapped test needs no history and is volume-gated.** Per-source day-over-day swings
    reach 47 points on low-volume sources, so only a source sitting near 100% across real
    volume is evidence of anything.

Same harness and reason as `test_geo_sql.py` — see its docstring for the throwaway database
recipe. Skipped when TEST_DATABASE_URL is unset; CI fails if these go back to skipping.
"""

from __future__ import annotations

import os

import pytest

from service import categorization_watchdog as cw
from service import store

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture(autouse=True)
def db():
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DSN
    store._POOL = None
    _clear()
    try:
        yield
    finally:
        _clear()
        store._POOL = None
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _clear() -> None:
    with store.cursor(commit=True) as cur:
        cur.execute("delete from categorization_daily")


def _seed_history(source: str, pcts: list[float], total: int = 10_000) -> None:
    """N prior days at the given uncategorised share, oldest first."""
    with store.cursor(commit=True) as cur:
        for i, pct in enumerate(pcts, start=1):
            cur.execute(
                "insert into categorization_daily (day, source, total, uncategorised) "
                "values (current_date - %s, %s, %s, %s)",
                (i, source, total, int(round(total * pct / 100.0))),
            )


# --- drift needs history, and judges the whole corpus --------------------------------------


def test_it_refuses_to_judge_without_enough_history():
    """One or two stored days is not a baseline. Alerting off it would fire on ordinary days,
    which is how an alert channel gets muted."""
    _seed_history(cw.ALL, [30.0, 31.0])
    assert cw.check({cw.ALL: (10_000, 9_000)}) == []      # 90% and still silent


def test_drift_fires_when_the_corpus_gets_worse():
    _seed_history(cw.ALL, [30.0, 31.0, 30.0, 29.0, 30.0])
    problems = cw.check({cw.ALL: (10_000, 4_000)})        # 40% against a ~30% median
    assert len(problems) == 1 and problems[0].startswith("DRIFT")


def test_an_improvement_never_fires():
    """The gate is one-directional. Categorising *more* is the goal, and an alert on any
    movement would punish the work this watchdog exists to protect."""
    _seed_history(cw.ALL, [40.0, 41.0, 39.0, 40.0, 40.0])
    assert cw.check({cw.ALL: (10_000, 2_000)}) == []      # 20%, a big improvement


def test_ordinary_variation_stays_quiet():
    _seed_history(cw.ALL, [33.0, 34.0, 32.0, 33.0, 35.0])
    assert cw.check({cw.ALL: (10_000, 3_500)}) == []      # 35% vs 33% median


# --- unmapped sources need no history, but do need volume ----------------------------------


def test_an_unmapped_source_fires_with_no_history_at_all():
    problems = cw.check({cw.ALL: (10_000, 3_000), "newsource": (5_000, 4_900)})
    assert any(p.startswith("UNMAPPED") and "newsource" in p for p in problems)


def test_a_low_volume_source_is_not_judged():
    """Per-source day-over-day swings reach 47 points on ~80 rows a day (recruitee, measured
    2026-08-09). Below MIN_ROWS the share is noise."""
    assert cw.check({cw.ALL: (10_000, 3_000), "tiny": (50, 50)}) == []


# --- the snapshot must not become its own baseline -----------------------------------------


def test_today_is_excluded_from_its_own_history():
    """`record` runs after `check` in main(), and `history` reads `day < current_date`. If
    today leaked into the baseline, a regression would normalise itself the moment it was
    written and the second day of an outage would look healthy."""
    cw.record({cw.ALL: (10_000, 9_000)})                  # today, terrible
    _seed_history(cw.ALL, [30.0, 30.0, 30.0, 30.0])
    assert cw.history(cw.ALL) == pytest.approx([30.0] * 4)


def test_record_is_idempotent():
    cw.record({cw.ALL: (100, 10)})
    cw.record({cw.ALL: (100, 20)})
    with store.cursor() as cur:
        cur.execute("select total, uncategorised from categorization_daily "
                    "where source = %s and day = current_date", (cw.ALL,))
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["uncategorised"] == 20
