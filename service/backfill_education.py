"""Backfill `postings.education_min` for existing postings (migration 014).

Migration 014 adds the column, but SQL cannot fill it: deciding whether "Bachelor's degree in
Computer Science" is a requirement or a line in an employer's self-description needs the
pattern tables in `service/education.py`. The daily ingest fills it for everything still being
re-listed, but that leaves a window where the new education filter has nothing to act on, and
postings that stopped being re-listed would never get a value at all.

**Read the expected result before running it, or it will look broken.** Around 97% of rows will
come out null, and that is correct — only ~3.2% of the corpus states a binding requirement, and
the sources that carry no description text at all (`jobscz`, `profesia`, `cocuma`, together
about 69% of active postings) can never produce anything but null. The per-level summary this
prints at the end is the thing worth reading: it is what tells you whether a change to the
patterns has quietly stopped detecting a whole level.

Idempotent: re-classifying an already-classified posting yields the same value, so this is
safe to re-run — and it **must** be re-run after any change to `service/education.py`, or
stored rows keep the old answer while new ingests use the new one, and the column silently
means two different things at once.

    DATABASE_URL=... python -m service.backfill_education [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import education, store  # noqa: E402

logger = logging.getLogger("service.backfill_education")

_UPDATE = """
update postings p set education_min = v.education_min
from (values %s) as v(posting_id, education_min)
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
                "select posting_id, title, description, education_min "
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
            level = education.classify_requirement(r["description"], r["title"])
            if level != r["education_min"]:
                updates.append((r["posting_id"], level))

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
            "       count(*) filter (where education_min is null) as unknown, "
            "       count(*) filter (where education_min = 'secondary') as secondary, "
            "       count(*) filter (where education_min = 'vocational') as vocational, "
            "       count(*) filter (where education_min = 'bachelor') as bachelor, "
            "       count(*) filter (where education_min = 'master') as master, "
            "       count(*) filter (where education_min = 'doctorate') as doctorate, "
            "       count(*) filter (where coalesce(description,'') = '') as no_description "
            "from postings where is_active")
        stats = dict(cur.fetchone())
    logger.info("done: scanned %d, changed %d", scanned, changed)
    # Unknown is expected to dominate overwhelmingly — see the module docstring. The number to
    # watch is not `unknown` but any *level* collapsing to zero between runs, which means a
    # pattern stopped matching and the filter silently narrowed for whoever selected it.
    logger.info("education_min: %d secondary, %d vocational, %d bachelor, %d master, "
                "%d doctorate, %d unknown (of %d active)",
                stats["secondary"], stats["vocational"], stats["bachelor"], stats["master"],
                stats["doctorate"], stats["unknown"], stats["total"])
    logger.info("of which %d active postings carry no description at all — those can never "
                "be anything but unknown", stats["no_description"])


if __name__ == "__main__":
    main()
