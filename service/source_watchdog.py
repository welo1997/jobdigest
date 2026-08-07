"""Ingestion-side watchdog — fail loudly when a source quietly stops or churns.

    DATABASE_URL=... python -m service.source_watchdog [--stale-days 2] [--quiet]

`service.watchdog` asks "did each subscriber get a digest". This asks the question one
stage earlier: **is each source still delivering what it did yesterday.** Both exist because
the pipeline's own health and the thing it is supposed to produce are different facts, and
systemd can only see the former.

Two failure shapes, both of which have happened in production and neither of which raised:

  SILENT ZERO   The source answers, returns nothing, and the run reports success. StartupJobs
                moved its API on 2026-08-06; the old endpoint became a clean JSON 404 that
                `fetch` swallowed into `[]`. It was caught by *luck* — someone happened to
                notice `last_seen_at` for that source sitting a day behind the others while
                looking at something else. Everything downstream was green: no exception, no
                failed timer, and 450 Czech postings simply stopped existing.

  ID CHURN      The source delivers a full, plausible count of rows that are all *new*,
                because its URL scheme changed and `posting_id = md5(url)`. On 2026-08-07
                startupjobs re-created its entire inventory this way: 450 "new" postings that
                were the same 450 jobs, 442 confirmed same-job-two-ids pairs, both copies
                active for a full staleness window. `digest_sends` keys on `posting_id` and
                cannot see it, so a subscriber's whole digest becomes jobs they were already
                sent, and only `digest.dedupe_key` stands between that and their inbox.

**The churn measure took two wrong answers to reach, and both were found only by running it
against production rather than reasoning about it.**

1. *"How much of the run was new"* fires permanently on `arbeitnow` and `himalayas`, rolling
   "newest N" feeds whose older jobs stop being served — ~100% new every day by design. Day
   one produced three alerts, all healthy sources.
2. *"How much of today is a job we already had"* is the right quantity but wrong as a level:
   `adzuna` is an aggregator whose corpus genuinely carries one job under many ad ids (a
   single role had 122) and sits at 69–86% **every day**.

So the alert is on a **jump against the source's own prior days**. startupjobs went
0.0 → 3.8 → 96.7 when its URLs changed; adzuna's biggest move was 4.4 points. Both
thresholds sit in gaps measured from real data, not chosen to look reasonable.

A related trap, also measured: the key must **not** include `city`. Adding it drops the real
incident from 96.7% to 3.3%, because the old adapter hardcoded `country_code='CZ'` (leaving
`city` null) while the new one resolves `prague` — so the fix being detected would itself
hide the detection.

**Expected sources are read from `search_jobs.source_classes`, never a list kept here.** A
source that is absent from `gather()` is retired, not broken — jobscz and profesia are
excluded on Alma Career's terms and must never alert, and a monitor that cries about a
deliberate decision every day is a monitor that gets muted. The same env-conditional logic
decides Adzuna and USAJOBS there and here, so a box without those keys expects neither.

Like `service.watchdog`, the alert *is* the non-zero exit: the systemd unit carries
`OnFailure=jobdigest-alert@%n.service`, which mails the journal lines this prints. No
second notification path. Nothing here is personal data, so nothing is masked.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import store  # noqa: E402

logger = logging.getLogger("service.source_watchdog")

#: Days a source may go without a fresh row before it counts as silently zero. Two rather
#: than one: the export is a single daily run, so a source is legitimately ~24h stale for
#: most of the day, and one skipped run (a deploy, a reboot, a slow board) is not an outage.
STALE_DAYS = int(os.environ.get("SOURCE_WATCHDOG_STALE_DAYS", "2"))

#: Share of *today's new rows that are jobs we already had* — above this, the source re-minted
#: its ids rather than delivering new inventory. Measured against production 2026-08-07:
#: startupjobs (the real re-mint) 96.7%, workday 24.5%, arbeitnow 12.5%, himalayas 1.0%.
#: 0.7 sits in the wide gap between the incident and the busiest honest source.
#:
#: **Not a threshold on newness.** The first version alerted when nearly every row a run
#: returned was new, and would have fired on arbeitnow, himalayas and teamtailor on its very
#: first day — all three legitimately. `arbeitnow` and `himalayas` are rolling "newest N"
#: feeds whose older jobs stop being returned, so they are ~100% new *every day, for ever*.
#: A monitor that cries daily is a monitor that gets muted, which this module's own docstring
#: warned about; it was caught only by running it against real data before trusting it.
CHURN_SHARE = float(os.environ.get("SOURCE_WATCHDOG_CHURN_SHARE", "0.7"))

#: How far today must exceed the source's OWN prior days. The level alone is not enough:
#: `adzuna` is an aggregator carrying the same job under many ad ids (one role had 122) and
#: sits at 69–86% *every day*, which would alert for ever. A re-mint is a jump — startupjobs
#: went 0.0 → 3.8 → 96.7 (a 93-point move) while adzuna's largest day-on-day move was 4.4.
#: 0.3 sits in that gap with room on both sides.
CHURN_JUMP = float(os.environ.get("SOURCE_WATCHDOG_CHURN_JUMP", "0.3"))

#: Below this many new rows the share is noise — a handful of genuine re-posts at a small
#: board would otherwise read as a re-mint. Also the minimum for a prior day to count
#: towards the baseline, so one quiet day cannot drag it down and manufacture a jump.
CHURN_FLOOR = int(os.environ.get("SOURCE_WATCHDOG_CHURN_FLOOR", "50"))


def expected_sources() -> set[str]:
    """Source names `gather()` will run today, read from the list it actually uses.

    `include_cz=True` because that is what the deployed export runs (see
    `deploy/jobdigest-match.sh`). Importing `search_jobs` only builds the class list — no
    adapter fetches anything.
    """
    import search_jobs

    return {cls().source_name for cls in search_jobs.source_classes(include_cz=True)}


def findings(stale_days: int = STALE_DAYS) -> tuple[list[str], list[str]]:
    """(problems, table rows). `problems` empty means healthy."""
    rows = {r["source"]: r for r in store.source_freshness()}
    expected = expected_sources()
    problems: list[str] = []
    table: list[str] = []

    for name in sorted(expected):
        row = rows.get(name)
        if row is None or not row["active"]:
            problems.append(
                f"SILENT ZERO  {name}: no active postings at all - the adapter ran and "
                f"stored nothing, or has never run on this box")
            table.append(f"  {name:<16} {'-':>7} {'-':>7} {'-':>7} {'-':>7} {'-':>8}"
                         f"  never")
            continue

        # `active > 0` implies a row exists implies `max(last_seen_at)` is not null, so this
        # cannot normally be None — but it is formatted with `.1f` below, and a crash inside
        # the monitor is a monitor that stops monitoring.
        age = float(row["age_days"] if row["age_days"] is not None else 999.0)
        active = row["active"]
        seen_today, new_today = row["seen_today"], row["new_today"]
        keyed_today, repeat_today = row["keyed_today"], row["repeat_today"]
        baseline = float(row["baseline_share"] or 0.0)
        # Of the postings created today, how many are a job already stored under another id?
        # Compared against the source's own prior days, because the level alone is normal for
        # an aggregator — see CHURN_SHARE / CHURN_JUMP.
        share = repeat_today / keyed_today if keyed_today else 0.0
        table.append(f"  {name:<16} {active:>7} {seen_today:>7} {new_today:>7} "
                     f"{share:>7.0%} {baseline:>8.0%}  {age:.1f}d old")

        if age > stale_days:
            problems.append(
                f"SILENT ZERO  {name}: freshest row is {age:.1f} days old while the export "
                f"runs daily - the source is answering with nothing, or not being called")
        elif (keyed_today >= CHURN_FLOOR and share >= CHURN_SHARE
                and share - baseline >= CHURN_JUMP):
            problems.append(
                f"ID CHURN     {name}: {repeat_today} of the {keyed_today} postings created "
                f"today ({share:.0%}) are jobs already stored under another id, against "
                f"{baseline:.0%} on this source's own prior days - its URLs changed and "
                f"posting_id = md5(url), so the whole source is duplicated; check the "
                f"stored `url` against the live site")

    unexpected = sorted(set(rows) - expected)
    for name in unexpected:
        if rows[name]["active"]:
            table.append(f"  {name:<16} {rows[name]['active']:>7} "
                         f"{rows[name]['seen_today']:>7} {rows[name]['new_today']:>7}"
                         f"  (retired, ageing out)")

    return problems, table


def report(stale_days: int = STALE_DAYS) -> tuple[str, int]:
    problems, table = findings(stale_days)
    lines: list[str] = []
    if problems:
        lines.append(f"{len(problems)} source problem(s):\n")
        lines.extend(f"  {p}" for p in problems)
        lines.append("")
        lines.append("Measure what the source actually holds before believing a number:")
        lines.append("  python -c \"from ingestion.sources.<name> import <Cls>; "
                     "s=<Cls>(); print(len(s.normalize(s.fetch())))\"")
        lines.append("")
    else:
        lines.append(f"OK - every source in gather() has rows fresher than {stale_days} "
                     f"days and none re-minted its ids.\n")
    lines.append(f"  {'source':<16} {'active':>7} {'seen':>7} {'new':>7} "
                 f"{'repeat':>7} {'base':>8}  freshness")
    lines.extend(table)
    return "\n".join(lines), len(problems)


def main() -> int:
    ap = argparse.ArgumentParser(description="Alert when a source silently stops or churns.")
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS,
                    help=f"days without a fresh row before alerting (default {STALE_DAYS})")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when healthy (still exits non-zero when not)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")

    text, n = report(stale_days=args.stale_days)
    if n or not args.quiet:
        print(text)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
