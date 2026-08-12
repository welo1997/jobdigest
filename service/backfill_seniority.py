"""Backfill `postings.seniority` after the 2026-08-12 six-level rewrite.

No migration accompanies this one — the column was already `text` and already nullable. What
changed is what the values *mean*, and that is exactly the kind of change a schema cannot
carry: every stored row still holds a legal value, so nothing errors, and the column quietly
means two things at once until this runs.

**Read the expected result before running it, or it will look catastrophic.** Roughly 71% of
active rows will come out NULL, against 0% before, because the old classifier had no way to
say "this title named no level" and fell through to `mid` instead. Measured on the live corpus
the day this shipped: 128 080 active postings, 79 719 of them naming no level at all. The
`mid` bucket is expected to collapse from ~82 000 to ~1 000 and that is the fix, not a
regression — see `notes/2026-08-12-seniority-ladder.md`.

The other large movement is `senior` shedding ~11 000 rows whose only level signal was the
word "manager" (Account Manager, Territory Sales Manager, *Assistant* Manager). Those become
NULL or `lead` depending on the rest of the title, and until now they were hidden from every
junior/mid filter on /jobs and /matches.

Idempotent: re-classifying an already-classified posting yields the same value, so this is
safe to re-run — and it **must** be re-run after any change to `search_jobs.seniority()`, or
stored rows keep the old answer while new ingests use the new one.

    DATABASE_URL=... python -m service.backfill_seniority [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_jobs import SENIORITY_LEVELS, seniority  # noqa: E402
from service import store  # noqa: E402

logger = logging.getLogger("service.backfill_seniority")

_UPDATE = """
update postings p set seniority = v.seniority
from (values %s) as v(posting_id, seniority)
where p.posting_id = v.posting_id
"""
_TEMPLATE = "(%s, %s::text)"


def run(batch: int = 2000, dry_run: bool = False) -> tuple[int, int]:
    """Returns (scanned, changed)."""
    scanned = changed = 0
    cursor_id = ""
    while True:
        with store.cursor() as cur:
            cur.execute(
                "select posting_id, title, seniority "
                "from postings where posting_id > %s order by posting_id limit %s",
                (cursor_id, batch),
            )
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            break
        cursor_id = rows[-1]["posting_id"]
        scanned += len(rows)

        updates = [(r["posting_id"], seniority(r["title"]))
                   for r in rows if seniority(r["title"]) != r["seniority"]]
        changed += len(updates)
        if updates and not dry_run:
            with store.cursor(commit=True) as cur:
                psycopg2.extras.execute_values(cur, _UPDATE, updates, template=_TEMPLATE,
                                               page_size=500)
        logger.info("scanned %d, changed %d%s", scanned, changed, " (dry run)" if dry_run else "")
    return scanned, changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    scanned, changed = run(batch=args.batch, dry_run=args.dry_run)

    # Per-level summary, which is the thing actually worth reading: it is what shows whether a
    # vocabulary change has quietly stopped detecting a whole rung. A level collapsing to zero
    # between two runs is the failure mode, not a high null count.
    counts = ", ".join(
        f"count(*) filter (where seniority = '{lvl}') as {lvl}" for lvl in SENIORITY_LEVELS)
    with store.cursor() as cur:
        cur.execute(f"select count(*) as total, "
                    f"count(*) filter (where seniority is null) as unstated, {counts} "
                    f"from postings where is_active")
        stats = dict(cur.fetchone())
    logger.info("done: scanned %d, changed %d", scanned, changed)
    logger.info("seniority: %s, %d unstated (of %d active)",
                ", ".join(f"{stats[lvl]} {lvl}" for lvl in SENIORITY_LEVELS),
                stats["unstated"], stats["total"])
    logger.info("~71%% unstated is the expected result, not a failure — the old scheme stored "
                "those as 'mid'. See this module's docstring.")


if __name__ == "__main__":
    main()
