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
                sent, and only `digest.dedupe_key` stands between that and their inbox. The
                fingerprint is unmistakable and nothing was looking for it: a source whose
                new-today count is essentially its whole active inventory.

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

#: Share of the rows a run *returned* that were first seen today, above which the pull looks
#: like an id re-mint rather than an ordinary day. Measured 2026-08-07: real sources turn over
#: 1–16% (greenhouse 528/16 786, workday 1 479/9 179), the churned one was 450 of 450. 0.8
#: sits far above the normal band and just under the only value a re-mint can produce.
CHURN_SHARE = float(os.environ.get("SOURCE_WATCHDOG_CHURN_SHARE", "0.8"))

#: Below this many rows returned, the share is noise — a small board is legitimately 100% new
#: the day it is added, and any brand-new source is 100% new on its first run.
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
            table.append(f"  {name:<16} {'-':>7} {'-':>7} {'-':>7}  never")
            continue

        # `active > 0` implies a row exists implies `max(last_seen_at)` is not null, so this
        # cannot normally be None — but it is formatted with `.1f` below, and a crash inside
        # the monitor is a monitor that stops monitoring.
        age = float(row["age_days"] if row["age_days"] is not None else 999.0)
        active = row["active"]
        seen_today, new_today = row["seen_today"], row["new_today"]
        # Share of *what this run returned* that was new, not of the whole stored corpus —
        # see the note in `store.source_freshness`. Measured on the real incident, `active`
        # as the denominator reads 48% and stays under any sane threshold; `seen_today`
        # reads 100%.
        share = new_today / seen_today if seen_today else 0.0
        table.append(f"  {name:<16} {active:>7} {seen_today:>7} {new_today:>7}  "
                     f"{age:.1f}d old  {share:.0%} new")

        if age > stale_days:
            problems.append(
                f"SILENT ZERO  {name}: freshest row is {age:.1f} days old while the export "
                f"runs daily - the source is answering with nothing, or not being called")
        elif seen_today >= CHURN_FLOOR and share >= CHURN_SHARE:
            problems.append(
                f"ID CHURN     {name}: {new_today} of the {seen_today} rows this run "
                f"returned ({share:.0%}) were first seen today - a URL-scheme change "
                f"re-mints posting_id and duplicates the whole source; check the stored "
                f"`url` against the live site before trusting the count")

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
    lines.append(f"  {'source':<16} {'active':>7} {'seen':>7} {'new':>7}  freshness")
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
