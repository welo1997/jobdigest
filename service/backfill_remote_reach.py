"""Backfill `postings.remote_reach` and `postings.reach_areas` (migrations 021 and 022).

The migrations add the columns, but SQL cannot fill them: deciding that "Anywhere in the United
States" is a *US-only* role while "Anywhere in the World" is not needs the country tables and the
precedence rules in `service/geo.py`. The daily ingest fills them for everything still being
re-listed; without this, the new filters have nothing to act on for a full staleness window and
postings that stopped being re-listed would never get a value at all.

Both columns come from one `geo.classify_reach` call, which is what stops a posting being `region`
on the strength of its scope field and filed under the area named in its location field.

**Read the expected result before running it, or it will look broken.**

Run against the whole live corpus on 2026-08-14 — 170 299 postings scanned, 18 160 changed — the
distribution over the 16 296 **active fully-remote** rows came out:

    country   82.1%      region 5.4%      anywhere 0.5%      null 12.0%

(A stratified 300-per-source sample taken first said 71.6 / 5.3 / 0.75 / 22.3. It was not wrong,
it was differently weighted: 300 rows each from cocuma, recruitee and themuse against 300 from
ashby is not what the corpus looks like. **Quote the full-corpus numbers.** The sample's value was
that it made per-source auditing cheap, not that it estimated the total.)

`anywhere` really is a fraction of a percent — 83 postings — and that is the finding rather than a
bug: "remote" in this corpus almost always means work from home in one named country, because that
is where the employer runs payroll. A run that reports a few hundred `anywhere` rows out of tens of
thousands is working correctly. A run that reports many thousands is a false-positive bug, and the
first thing to check is whether a wide signal has started outranking a named country — all three
false positives found while writing this classifier were exactly that (see
`service/tests/test_remote_reach.py`).

**Only fully-remote rows get a value.** The reach of an on-site Berlin job is a category error,
and setting one anyway would fill the column with trivially-`country` rows and make coverage look
far better than it is. Rows that stop being remote are cleared back to null, so a re-run after a
`work_mode` change repairs them.

It scans **all** postings rather than only active ones (as `backfill_education` does), so
`scanned` is the whole table — 170 299 against 16 296 active remote rows. Do not read `changed` as
a count of remote postings.

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
update postings p set remote_reach = v.remote_reach, reach_areas = v.reach_areas,
                      reach_countries = v.reach_countries
from (values %s) as v(posting_id, remote_reach, reach_areas, reach_countries)
where p.posting_id = v.posting_id
"""
_TEMPLATE = "(%s, %s::text, %s::text[], %s::text[])"


def run(batch: int = 2000, dry_run: bool = False) -> tuple[int, int]:
    """Returns (scanned, changed)."""
    scanned = changed = 0
    cursor_id = ""
    while True:
        with store.cursor() as cur:
            cur.execute(
                "select posting_id, location, description, scope_raw, remote_signal, "
                "       work_mode, remote_reach, reach_areas, reach_countries "
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
            reach, areas = (geo.classify_reach(r["scope_raw"], r["location"], r["description"])
                            if is_remote else (None, []))
            # `reach_countries` is **not** gated on `is_remote`, and that asymmetry is the point
            # of the column: a two-office on-site job is holdable in two countries even though
            # asking where its holder may *live* is a category error. See `geo.reach_countries`.
            others = geo.reach_countries(r["scope_raw"], r["location"])
            # Compared against the stored form, not the computed one: `upsert_postings` and the
            # update below both store an empty list as NULL, so comparing `[] != None` would mark
            # every non-multi-country row as changed on every run and the idempotency test — the
            # one that proves this agrees with `ingest.build_row` — would never be able to pass.
            if (reach != r["remote_reach"]
                    or (areas or None) != (r["reach_areas"] or None)
                    or (others or None) != (r["reach_countries"] or None)):
                updates.append((r["posting_id"], reach, areas or None, others or None))

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

    if args.dry_run:
        # The summary below reads what is *stored*, so after a dry run it describes the column as
        # it was before — on a first run, "100% unknown". Printing that under a dry run invites
        # exactly the wrong conclusion ("the classifier found nothing"), which is the plausible-
        # looking-number failure this repo keeps paying for. Say so instead of showing it.
        logger.info("dry run: %d rows would change. No distribution printed — the summary reads "
                    "stored values, which a dry run has not written, so it would report the "
                    "column's *previous* state and look like a classifier that found nothing.",
                    changed)
        return

    with store.cursor() as cur:
        cur.execute(
            "select count(*) as remote, "
            "       count(*) filter (where remote_reach is null)       as unknown, "
            "       count(*) filter (where remote_reach = 'anywhere')  as anywhere, "
            "       count(*) filter (where remote_reach = 'region')    as region, "
            "       count(*) filter (where remote_reach = 'country')   as country, "
            "       count(*) filter (where scope_raw is not null)      as with_scope, "
            "       count(*) filter (where reach_areas @> array['eea']) as eea, "
            "       count(*) filter (where reach_areas @> array['na'])  as na, "
            "       count(*) filter (where reach_areas @> array['eea','na']) as both, "
            "       count(*) filter (where remote_reach = 'region' "
            "                          and coalesce(cardinality(reach_areas), 0) = 0) as no_area "
            "from postings p "
            f"where p.is_active and {store.REMOTE_SQL}")
        stats = dict(cur.fetchone())
        # Per source, because coverage is a property of the *board*, not the corpus: a source
        # publishing a scope field lands near 100% and one that publishes none near 30%, and the
        # average of the two says nothing useful about either. This is also where an adapter that
        # has silently stopped sending `scope_raw` shows up.
        cur.execute(
            "select source, count(*) as n, "
            "       count(*) filter (where remote_reach is null) as unknown, "
            "       count(*) filter (where scope_raw is not null) as with_scope "
            "from postings p "
            f"where p.is_active and {store.REMOTE_SQL} "
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
    # What the two /jobs international rows will show. The overlap is not an error to be removed:
    # it is the only set in which someone in the EEA can hold a US-facing role.
    logger.info("reach areas: %d EU-International, %d North America-International "
                "(%d in both), %d region rows whose areas could not be read",
                stats["eea"], stats["na"], stats["both"], stats["no_area"])
    # `anywhere` under a couple of percent is the expected shape — see the module docstring.
    if stats["anywhere"] > total * 0.15:
        logger.error("anywhere is %.1f%% of remote postings, which is far above the measured "
                     "0.75%% — suspect a wide signal outranking a named country, not a windfall",
                     100 * stats["anywhere"] / total)
    # `reach_countries` is reported over the WHOLE active corpus, not the remote slice above,
    # because unlike the two reach columns it is not gated on being remote — a two-office on-site
    # job belongs to two countries. Reporting it against the remote denominator would understate
    # it and quietly imply a gate that is not there.
    with store.cursor() as cur:
        cur.execute(
            "select count(*) as active, "
            "       count(*) filter (where reach_countries is not null) as multi, "
            "       coalesce(sum(cardinality(reach_countries)) "
            "                filter (where reach_countries is not null), 0) as memberships "
            "from postings p where p.is_active")
        rc = dict(cur.fetchone())
    logger.info("reach_countries: %d of %d active postings name more than one country, "
                "adding %d country-filter memberships that `country_code` alone could not reach",
                rc["multi"], rc["active"], max(rc["memberships"] - rc["multi"], 0))
    logger.info("by source (n / unknown / with scope_raw):")
    for r in by_source:
        logger.info("  %-16s %6d  %5d unknown (%3d%%)  %6d scope_raw",
                    r["source"], r["n"], r["unknown"],
                    100 * r["unknown"] // max(r["n"], 1), r["with_scope"])


if __name__ == "__main__":
    main()
