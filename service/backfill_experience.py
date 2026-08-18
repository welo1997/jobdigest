"""Backfill `postings.experience_min` for existing postings (migration 025).

Migration 025 adds the column, but SQL cannot fill it: deciding whether "25 years of
experience" is a requirement on the applicant or the employer's anniversary needs the pattern
tables in `service/experience.py`. The daily ingest fills it for everything still being
re-listed; this covers the window before that, and the postings that stopped being re-listed.

**Read the expected result before running it, or it will look broken.** Roughly three quarters
of rows will come out null and that is correct — most ads state no tenure requirement, and the
sources that carry no description text can never produce anything but null. The distribution
this prints at the end is the thing worth reading: the mode should sit at 3 and 5 years
(measured on a 400-row production sample, 2026-08-18), and a mode that has moved means a
pattern change did more than intended.

Idempotent, and **must be re-run after any change to `service/experience.py`** — same contract
as every other backfill here.

    DATABASE_URL=... python -m service.backfill_experience [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import experience, store  # noqa: E402

logger = logging.getLogger("service.backfill_experience")

_UPDATE = """
update postings p set experience_min = v.experience_min
from (values %s) as v(posting_id, experience_min)
where p.posting_id = v.posting_id
"""
_TEMPLATE = "(%s, %s::smallint)"


def run(batch: int = 2000, dry_run: bool = False) -> tuple[int, int]:
    """Returns (scanned, changed)."""
    scanned = changed = 0
    cursor_id = ""
    while True:
        with store.cursor() as cur:
            cur.execute(
                "select posting_id, title, description, experience_min "
                "from postings where posting_id > %s order by posting_id limit %s",
                (cursor_id, batch),
            )
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            break
        cursor_id = rows[-1]["posting_id"]
        scanned += len(rows)

        updates = []
        for r in rows:
            years = experience.classify_requirement(r["description"], r["title"])
            if years != r["experience_min"]:
                updates.append((r["posting_id"], years))

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
    with store.cursor() as cur:
        cur.execute(
            "select count(*) as total, "
            "       count(*) filter (where experience_min is null) as unknown, "
            "       count(*) filter (where coalesce(description,'') = '') as no_description "
            "from postings where is_active")
        stats = dict(cur.fetchone())
        cur.execute(
            "select experience_min as years, count(*) as n from postings "
            "where is_active and experience_min is not null "
            "group by experience_min order by experience_min")
        dist = {r["years"]: r["n"] for r in cur.fetchall()}
    logger.info("done: scanned %d, changed %d", scanned, changed)
    # Unknown is expected to dominate — see the module docstring. The number to watch is the
    # distribution: a mode away from 3/5 years, or a pile-up at MAX_YEARS, means the guards
    # have started letting employer anniversaries through.
    logger.info("experience_min distribution (active): %s; %d unknown of %d "
                "(%d with no description at all)",
                dist, stats["unknown"], stats["total"], stats["no_description"])


if __name__ == "__main__":
    main()
