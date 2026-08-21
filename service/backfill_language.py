"""Backfill `postings.language` for existing postings (migration 026).

Migration 026 adds the column, but SQL cannot fill it: it needs the offline detector in
`service/language.py`. The daily ingest fills it for everything still being re-listed; this
covers the window before that, and the postings that stopped being re-listed.

**Read the expected result before running it, or it will look broken.** A large share of rows
will come out null and that is correct — the CZ/SK title-only corpus carries no text long enough
to read (`language.MIN_CHARS`), and every genuinely bilingual ad falls below the confidence
floor. The distribution this prints at the end is the thing worth reading: English should
dominate, with a long multilingual tail; a *systemic* misread (e.g. a pile of Slovak where the
board is Czech, or every remote board reading as one language) means a threshold in
`service/language.py` needs revisiting, not that the backfill is wrong.

Idempotent, and **must be re-run after any change to `service/language.py`** (thresholds or the
language set) — same contract as every other backfill here.

    DATABASE_URL=... python -m service.backfill_language [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import language, store  # noqa: E402

logger = logging.getLogger("service.backfill_language")

_UPDATE = """
update postings p set language = v.language
from (values %s) as v(posting_id, language)
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
                "select posting_id, title, description, language "
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
            lang = language.detect(r["description"], r["title"])
            if lang != r["language"]:
                updates.append((r["posting_id"], lang))

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
            "       count(*) filter (where language is null) as unknown, "
            "       count(*) filter (where coalesce(description,'') = '') as no_description "
            "from postings where is_active")
        stats = dict(cur.fetchone())
        cur.execute(
            "select language, count(*) as n from postings "
            "where is_active and language is not null "
            "group by language order by count(*) desc")
        dist = {r["language"]: r["n"] for r in cur.fetchall()}
    logger.info("done: scanned %d, changed %d", scanned, changed)
    # Null is expected to dominate — see the module docstring. The number to watch is the
    # distribution: English-led with a multilingual tail is healthy; a pile-up on one confusable
    # neighbour (sk/pt/da) or a whole board reading as one language means a threshold is off.
    logger.info("language distribution (active, non-null): %s; %d unknown of %d "
                "(%d with no description at all)",
                dist, stats["unknown"], stats["total"], stats["no_description"])


if __name__ == "__main__":
    main()
