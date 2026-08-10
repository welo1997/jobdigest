"""Backfill `postings.skills` for existing postings (migration 020).

Migration 020 adds the column, but SQL cannot fill it: extracting skills needs the gazetteer
and the ambiguous-token guards in `service/skills.py`, which SQL cannot see (same reason 012
could not backfill `work_mode` and 014 could not backfill `education_min`). The daily ingest
fills it for everything still being re-listed, but that leaves a window where the new facet has
nothing to act on, and postings that stopped being re-listed would never get a value at all.

**Read the expected result before running it.** Coverage is high on tech/data/business
postings and low on trades/healthcare (whose "skills" are certifications and licences — a
later gazetteer chapter). Many postings carry no description text at all (the whole CZ/SK
inventory), so they can only ever be tagged from their title, and most will come out empty ->
null. The per-skill summary this prints is the thing worth reading: it is what tells you
whether a change to the gazetteer has quietly stopped detecting a common tool.

Empty results are stored as **null**, not `'{}'` — null and empty are equivalent for the
facet (a facet only narrows on a positive selection), and null keeps the GIN index and the
storage smaller. So the comparison below treats stored null and stored `'{}'` and a computed
`[]` as the same "no skills".

Idempotent: re-extracting an already-tagged posting yields the same set, so this is safe to
re-run — and it **must** be re-run after any change to `service/skills.py`, or stored rows
keep the old answer while new ingests use the new one, and the column silently means two
different things at once.

    DATABASE_URL=... python -m service.backfill_skills [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import skills, store

logger = logging.getLogger("service.backfill_skills")

# `%s::text[]` casts the array parameter so an empty list becomes a valid `'{}'::text[]` rather
# than an untyped empty array — but we pass null for empties anyway (see module docstring), so
# the cast mainly guards the non-empty case's element typing.
_UPDATE = """
update postings p set skills = v.skills
from (values %s) as v(posting_id, skills)
where p.posting_id = v.posting_id
"""
_TEMPLATE = "(%s, %s::text[])"


def run(batch: int = 2000, dry_run: bool = False) -> tuple[int, int]:
    """Returns (scanned, changed)."""
    scanned = changed = 0
    cursor_id = ""
    while True:
        with store.cursor() as cur:
            cur.execute(
                "select posting_id, title, description, skills "
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
            found = skills.extract_skills(r["title"], r["description"])
            stored = sorted(r["skills"] or [])
            if found != stored:  # extract_skills already returns a sorted list
                updates.append((r["posting_id"], found or None))

        changed += len(updates)
        if updates and not dry_run:
            with store.cursor(commit=True) as cur:
                psycopg2.extras.execute_values(
                    cur, _UPDATE, updates, template=_TEMPLATE, page_size=500
                )
        logger.info(
            "scanned %d, changed %d%s",
            scanned,
            changed,
            " (dry run)" if dry_run else "",
        )
    return scanned, changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    scanned, changed = run(batch=args.batch, dry_run=args.dry_run)

    with store.cursor() as cur:
        cur.execute(
            "select count(*) as total, "
            "       count(*) filter (where skills is not null "
            "                        and cardinality(skills) > 0) as with_skills "
            "from postings where is_active"
        )
        stats = dict(cur.fetchone())
        # Top skills across active postings — the shape that shows whether a common tool
        # stopped being detected between runs.
        cur.execute(
            "select s as skill, count(*) as n "
            "from postings, unnest(skills) as s "
            "where is_active group by s order by n desc limit 25"
        )
        top = Counter({r["skill"]: r["n"] for r in cur.fetchall()})

    total = stats["total"] or 1
    logger.info("done: scanned %d, changed %d", scanned, changed)
    logger.info(
        "skills: %d of %d active postings carry >=1 (%.1f%% coverage)",
        stats["with_skills"],
        stats["total"],
        100.0 * stats["with_skills"] / total,
    )
    logger.info("top skills: %s", dict(top.most_common(25)))


if __name__ == "__main__":
    main()
