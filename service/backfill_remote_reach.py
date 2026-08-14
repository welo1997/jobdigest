"""Backfill `postings.remote_reach` for existing postings (migration 021).

Migration 021 adds the column, but SQL cannot fill it: deciding that "Anywhere in the United
States" is a *US-only* role while "Anywhere in the World" is not needs the country tables and the
precedence rules in `service/geo.py`. The daily ingest fills it for everything still being
re-listed; without this, the new filter has nothing to act on for a full staleness window and
postings that stopped being re-listed would never get a value at all.

**Read the expected result before running it, or it will look broken.**

Measured against a stratified sample of the live corpus on 2026-08-14 (3 593 active fully-remote
rows), the distribution comes out:

    country   71.6%      region 5.3%      anywhere 0.75%      null 22.3%

`anywhere` really is under one percent, and that is the finding rather than a bug: "remote" in
this corpus almost always means work from home in one named country, because that is where the
employer runs payroll. A run that reports a few hundred `anywhere` rows out of tens of thousands
is working correctly. A run that reports many thousands is a false-positive bug, and the first
thing to check is whether a wide signal has started outranking a named country — all three
false positives found while writing this classifier were exactly that (see
`service/tests/test_remote_reach.py`).

**Only fully-remote rows get a value.** The reach of an on-site Berlin job is a category error,
and setting one anyway would fill the column with trivially-`country` rows and make coverage look
far better than it is. Rows that stop being remote are cleared back to null, so a re-run after a
`work_mode` change repairs them.

`scope_raw` is deliberately **not** backfilled — it can only come from a source payload, so it
fills in over the staleness window as each adapter re-ingests (the same shape as
`source_category`). Until then this reads `location` and `description` only, which is what
recovers 77% of the corpus; WeWorkRemotely in particular stays near-blind until its next ingest,
because its stored `location` is empty for every row that predates the adapter fix.

Idempotent, so safe to re-run — and it **must** be re-run after any change to `geo.remote_reach`,
or stored rows keep the old answer while new ingests use the new one and the column silently means
two different things at once.

    DATABASE_URL=... python -m service.backfill_remote_reach [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import geo, store  # noqa: E402

logger = logging.getLogger("service.backfill_remote_reach")

_UPDATE = """
update postings p set remote_reach = v.remote_reach
from (values %s) as v(posting_id, remote_reach)
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
                "select posting_id, location, description, scope_raw, remote_signal, "
                "       work_mode, remote_reach "
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
            # Mirrors `ingest.build_row`: only a fully-remote posting has a reach, and one that
            # has stopped being remote is cleared rather than left stale.
            is_remote = r["remote_signal"] is True or r["work_mode"] == "remote"
            reach = geo.remote_reach(
                r["scope_raw"], r["location"], r["description"]) if is_remote else None
            if reach != r["remote_reach"]:
                updates.append((r["posting_id"], reach))

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
    logger.info("done: scanned %d, changed %d", scanned, changed)

    with store.cursor() as cur:
        cur.execute(
            "select count(*) as remote, "
            "       count(*) filter (where remote_reach is null)       as unknown, "
            "       count(*) filter (where remote_reach = 'anywhere')  as anywhere, "
            "       count(*) filter (where remote_reach = 'region')    as region, "
            "       count(*) filter (where remote_reach = 'country')   as country, "
            "       count(*) filter (where scope_raw is not null)      as with_scope "
            "from postings "
            "where is_active and (remote_signal is true or work_mode = 'remote')")
        stats = dict(cur.fetchone())
        # Per source, because coverage is a property of the *board*, not the corpus: a source
        # publishing a scope field lands near 100% and one that publishes none near 30%, and the
        # average of the two says nothing useful about either. This is also where an adapter that
        # has silently stopped sending `scope_raw` shows up.
        cur.execute(
            "select source, count(*) as n, "
            "       count(*) filter (where remote_reach is null) as unknown, "
            "       count(*) filter (where scope_raw is not null) as with_scope "
            "from postings "
            "where is_active and (remote_signal is true or work_mode = 'remote') "
            "group by source order by count(*) desc")
        by_source = [dict(r) for r in cur.fetchall()]

    total = max(stats["remote"], 1)
    logger.info("remote_reach over %d active remote postings: "
                "%d anywhere (%.1f%%), %d region (%.1f%%), %d country (%.1f%%), "
                "%d unknown (%.1f%%)",
                stats["remote"],
                stats["anywhere"], 100 * stats["anywhere"] / total,
                stats["region"], 100 * stats["region"] / total,
                stats["country"], 100 * stats["country"] / total,
                stats["unknown"], 100 * stats["unknown"] / total)
    logger.info("%d of those carry a board-published scope field (`scope_raw`); the rest were "
                "read from location text and description", stats["with_scope"])
    # `anywhere` under a couple of percent is the expected shape — see the module docstring.
    if stats["anywhere"] > total * 0.15:
        logger.error("anywhere is %.1f%% of remote postings, which is far above the measured "
                     "0.75%% — suspect a wide signal outranking a named country, not a windfall",
                     100 * stats["anywhere"] / total)
    logger.info("by source (n / unknown / with scope_raw):")
    for r in by_source:
        logger.info("  %-16s %6d  %5d unknown (%3d%%)  %6d scope_raw",
                    r["source"], r["n"], r["unknown"],
                    100 * r["unknown"] // max(r["n"], 1), r["with_scope"])


if __name__ == "__main__":
    main()
