"""Categorisation watchdog — fail loudly when the corpus quietly stops being classified.

    DATABASE_URL=... python -m service.categorization_watchdog [--jump 5] [--quiet]

`role_category` decides what enters a subscriber's shortlist. When it degrades nothing raises:
no exception, no failed timer, no changed row count. The subscriber's shortlist just gets
thinner, which from outside is indistinguishable from a quiet week — this repo's recurring
failure shape, and the reason `service/watchdog.py` and `service/source_watchdog.py` both
exist one stage apart.

Two failures worth catching, and they need different tests:

  DRIFT          The classifier's behaviour changes for the worse — a pattern edit that stops
                 matching, or sources gradually rewording titles. Shows up as the *whole
                 corpus* becoming less classified.

  UNMAPPED       A source arrives, or changes vocabulary, and nothing covers it. Shows up as
                 that one source sitting near 100% uncategorised while the corpus looks fine.

**The two obvious metrics were measured against production before this was written, and both
are unusable.** Recording that here because the next person will reach for them too:

  *Per-source, day over day.* Natural 8-day swing reaches **47 points** — recruitee moved
  21→68 on 57–114 rows a day, remoteok 29, smartrecruiters 28, lever 23. Any threshold under
  ~50 fires on healthy sources (the day-one false alarms `source_watchdog.py` documents) and
  any threshold over it catches nothing.

  *Corpus-wide over new rows only.* Swings **14.9% → 59.7%** across eight days, because it
  measures whichever sources happened to churn. One source rebuild on 2026-08-05 produced
  40 507 of that day's rows and moved the number 27 points by itself.

So DRIFT is measured over the **whole active corpus**, which is mix-independent and moves only
when classifier behaviour changes, and it needs stored history — hence
`categorization_daily` (migration 017). UNMAPPED needs no history: a source above
`UNMAPPED_PCT` with real volume is unmapped regardless of what yesterday looked like, and it
returns zero on today's production, so it will not cry wolf on day one.

Exit code is 1 when anything fires, so systemd's existing `OnFailure=jobdigest-alert@%n`
mails the report — the same wiring as the other two watchdogs.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys

from service import store

logger = logging.getLogger("service.categorization_watchdog")

ALL = "__all__"

#: Corpus-wide jump, in percentage points against the trailing median, that counts as drift.
#: The whole active corpus turns over slowly, so a real move here is a classifier change
#: rather than noise. Deliberately not tuned on one day of data — see MIN_HISTORY.
DRIFT_POINTS = 5.0

#: A source at or above this, with real volume, is not "hard to classify" — it is unmapped.
#: Measured 2026-08-09: zero sources on production reach it, so this cannot fire spuriously
#: on the day it ships.
UNMAPPED_PCT = 90.0

#: Below this many active rows a source's share is noise; recruitee swings 47 points on
#: ~80 rows a day.
MIN_ROWS = 200

#: Days of stored history before DRIFT can be judged. Fewer than this and the "trailing
#: median" is one or two numbers, which is a guess wearing a threshold.
MIN_HISTORY = 4


def snapshot() -> dict[str, tuple[int, int]]:
    """(total, uncategorised) per source across the whole active corpus, plus `__all__`."""
    with store.cursor() as cur:
        cur.execute(
            "select source, count(*) as total, "
            "       count(*) filter (where role_category = 'uncategorised') as uncat "
            "from postings where is_active group by source"
        )
        rows = {r["source"]: (r["total"], r["uncat"]) for r in cur.fetchall()}
    rows[ALL] = (sum(t for t, _ in rows.values()), sum(u for _, u in rows.values()))
    return rows


def record(day_rows: dict[str, tuple[int, int]]) -> None:
    """Upsert today's snapshot. Idempotent, so a re-run does not create a second day."""
    with store.cursor(commit=True) as cur:
        for source, (total, uncat) in day_rows.items():
            cur.execute(
                "insert into categorization_daily (day, source, total, uncategorised) "
                "values (current_date, %s, %s, %s) "
                "on conflict (day, source) do update set "
                "  total = excluded.total, uncategorised = excluded.uncategorised, "
                "  recorded_at = now()",
                (source, total, uncat),
            )


def history(source: str, days: int = 14) -> list[float]:
    """Prior days' uncategorised share for one source, newest first, excluding today."""
    with store.cursor() as cur:
        cur.execute(
            "select total, uncategorised from categorization_daily "
            "where source = %s and day < current_date and total > 0 "
            "order by day desc limit %s",
            (source, days),
        )
        return [100.0 * r["uncategorised"] / r["total"] for r in cur.fetchall()]


def check(day_rows: dict[str, tuple[int, int]], jump: float = DRIFT_POINTS) -> list[str]:
    problems: list[str] = []

    total, uncat = day_rows.get(ALL, (0, 0))
    if total:
        today = 100.0 * uncat / total
        prior = history(ALL)
        if len(prior) < MIN_HISTORY:
            logger.info("DRIFT: %d day(s) of history, need %d - not judging yet (corpus at "
                        "%.1f%% uncategorised)", len(prior), MIN_HISTORY, today)
        else:
            median = statistics.median(prior)
            if today - median >= jump:
                problems.append(
                    f"DRIFT: the corpus is {today:.1f}% uncategorised against a "
                    f"{median:.1f}% median over {len(prior)} prior days "
                    f"(+{today - median:.1f} points). A classifier change reaches the whole "
                    f"corpus in one ingest cycle, so this is behaviour, not sampling."
                )

    for source, (n, u) in sorted(day_rows.items()):
        if source == ALL or n < MIN_ROWS:
            continue
        pct = 100.0 * u / n
        if pct >= UNMAPPED_PCT:
            problems.append(
                f"UNMAPPED: {source} is {pct:.0f}% uncategorised across {n:,} active rows. "
                f"That is not a hard-to-classify source, it is one nothing covers — a new "
                f"adapter, or vocabulary that changed."
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jump", type=float, default=DRIFT_POINTS,
                    help="corpus-wide points above the trailing median that count as drift")
    ap.add_argument("--quiet", action="store_true", help="only log problems")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")

    rows = snapshot()
    problems = check(rows, jump=args.jump)
    # Recorded *after* the check, so today's own number cannot enter its own baseline.
    record(rows)

    total, uncat = rows.get(ALL, (0, 0))
    if total:
        logger.info("corpus: %s active, %.1f%% uncategorised, %d sources",
                    f"{total:,}", 100.0 * uncat / total, len(rows) - 1)
    for p in problems:
        logger.warning("%s", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
