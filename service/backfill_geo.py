"""Backfill the resolved geography of existing postings (migration 010).

Migration 010 adds `postings.city`, but SQL cannot fill it: resolving "Praha 4 – Chodov" or
"München, Bayern" needs the alias table in `service/geo.py`. The daily ingest fills the column
for everything still being re-listed, but that leaves a window where the new city filter has
nothing to filter on, and postings that stopped being re-listed would never get a city at all.

Also re-derives the columns that resolution improves, for the same reason ingest does:
`country_code` (null for whole sources until now), `remote_signal` (means *fully* remote —
hybrid is excluded, which is the point of the feature), `work_mode` (migration 012: the same
judgement, kept at full resolution — remote | hybrid | onsite | null) and, downstream of those,
`region`/`eligibility`.

**Migration 012 needs this run**: SQL can set `work_mode = 'remote'` from the existing flag but
cannot tell hybrid from on-site from unknown, so until this runs every non-remote posting is
null and the new work-setup filter has nothing to filter on.

Idempotent: re-resolving an already-resolved posting yields the same values, so this is safe
to re-run — and worth re-running after adding cities or aliases to `service/geo.py`.

    DATABASE_URL=... python -m service.backfill_geo [--batch 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_jobs import eligibility, work_region  # noqa: E402
from service import geo, store  # noqa: E402

logger = logging.getLogger("service.backfill_geo")

_UPDATE = """
update postings p set
    country_code  = v.country_code,
    city          = v.city,
    remote_signal = v.remote_signal,
    work_mode     = v.work_mode,
    region        = v.region,
    eligibility   = v.eligibility
from (values %s) as v(posting_id, country_code, city, remote_signal, work_mode,
                      region, eligibility)
where p.posting_id = v.posting_id
"""
_TEMPLATE = "(%s, %s::varchar(2), %s::text, %s::boolean, %s::text, %s::text, %s::text)"


def run(batch: int = 2000, dry_run: bool = False) -> tuple[int, int]:
    """Returns (scanned, changed)."""
    scanned = changed = 0
    cursor_id = ""
    while True:
        with store.cursor() as cur:
            cur.execute(
                "select posting_id, title, location, country_code, city, remote_signal, "
                "       work_mode, region, eligibility, description "
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
            country, city = geo.resolve_location(r["location"], r["country_code"])
            # `remote_signal` is re-derived from `work_mode`, not alongside it — one
            # classification, so a row can never claim to be fully remote and hybrid at once.
            # Note the stored flag is passed in as the *source signal*: `geo.work_mode` treats
            # it as a claim and overrules it from the posting's own text, which is what lets
            # this repair rows a bad scraper already wrote.
            mode = geo.work_mode(r["location"], r["description"], r["remote_signal"])
            remote = mode == "remote"
            region = work_region(r["location"], country)
            elig = eligibility(
                region, f"{r['title'] or ''} {r['location'] or ''} {r['description'] or ''}")
            before = (r["country_code"], r["city"], r["remote_signal"], r["work_mode"],
                      r["region"], r["eligibility"])
            after = (country, city, remote, mode, region, elig)
            if before != after:
                updates.append((r["posting_id"], country, city, remote, mode, region, elig))

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
        cur.execute("select count(*) filter (where city is not null) as with_city, "
                    "       count(*) filter (where country_code is not null) as with_country, "
                    "       count(*) filter (where work_mode = 'remote') as remote, "
                    "       count(*) filter (where work_mode = 'hybrid') as hybrid, "
                    "       count(*) filter (where work_mode = 'onsite') as onsite, "
                    "       count(*) filter (where work_mode is null) as mode_unknown, "
                    "       count(*) as total from postings where is_active")
        stats = dict(cur.fetchone())
    logger.info("done: scanned %d, changed %d", scanned, changed)
    logger.info("active postings: %d/%d have a city, %d/%d have a country",
                stats["with_city"], stats["total"], stats["with_country"], stats["total"])
    # Unknown is expected to dominate — most postings never state an arrangement, and the
    # filter keeps them. Worth reading anyway: a sudden collapse in `hybrid` means a detection
    # change, and that is exactly the bug class of 2026-07-28.
    logger.info("work_mode: %d remote, %d hybrid, %d onsite, %d unknown",
                stats["remote"], stats["hybrid"], stats["onsite"], stats["mode_unknown"])


if __name__ == "__main__":
    main()
