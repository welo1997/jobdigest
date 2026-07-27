"""Ingestion runner: fetch all sources, classify, upsert into Postgres.

Reuses the existing `ingestion/` sources and the `search_jobs` classifiers so there
is a single source of truth for how a posting is normalised and labelled. Intended to
run on a schedule (cron / GitHub Action / Supabase cron) writing into the `postings`
table the API reads from.

    DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5433/jobmatch \\
        python -m service.ingest [--cz] [--stale-days 7]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter

# Make the repo root importable when run as `python -m service.ingest` or directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_jobs import (  # noqa: E402
    dedup_key, eligibility, gather, is_part_time,
    seniority, work_region, work_type,
)
from service import geo, store, taxonomy  # noqa: E402

logger = logging.getLogger("service.ingest")

# --- role_category ------------------------------------------------------------
# The taxonomy itself lives in service/taxonomy.py — it is consumed by the shortlist
# builder, the digest subject line and the CV parser too, and used to be copy-pasted into
# each of them. Re-exported under the old name so callers here read unchanged.
role_category = taxonomy.classify


def build_row(p) -> dict:
    # Resolve the free-text location into (country, city) before anything else: most boards
    # send no country_code at all (Greenhouse, Lever, Remotive, LinkedIn all pass None), so
    # the resolved value is what makes country- and city-level preferences possible — and it
    # sharpens `region`/`eligibility` too, which previously read "Berlin" as region 'other'.
    country_code, city = geo.resolve_location(p.location, p.country_code)
    remote = geo.is_fully_remote(p.location, p.description, p.remote_signal)
    region = work_region(p.location, country_code)
    text = f"{p.title or ''} {p.location or ''} {p.description or ''}"
    return {
        "posting_id": p.posting_id, "source": p.source, "title": p.title,
        "company": p.company, "url": p.url, "description": p.description,
        "location": p.location, "country_code": country_code, "city": city,
        "remote_signal": remote, "salary_raw": p.salary_raw,
        "currency": p.currency, "posted_at": p.posted_at,
        "role_category": role_category(p.title, getattr(p, "source_category", None)),
        "region": region,
        "eligibility": eligibility(region, text),
        "seniority": seniority(p.title),
        "work_type": work_type(p.title, p.description),
        "is_part_time": is_part_time(p.title, p.description),
        "dedup_key": dedup_key(p),
    }


def run(include_cz: bool, stale_days: int) -> None:
    postings = gather(include_cz=include_cz)
    logger.info("Fetched %d postings", len(postings))

    rows, seen = [], set()
    for p in postings:
        # Broad scope: keep every role at a tech company (all sources here are
        # already tech-focused boards). Only drop rows with no URL or no title.
        if not (p.url and (p.title or "").strip()):
            continue
        if p.posting_id in seen:
            continue
        seen.add(p.posting_id)
        rows.append(build_row(p))

    n = store.upsert_postings(rows)
    stale = store.deactivate_stale(days=stale_days)
    by_cat = Counter(r["role_category"] for r in rows)
    logger.info("Upserted %d postings; deactivated %d stale; %d active total",
                n, stale, store.count_active())
    logger.info("By role_category: %s", dict(by_cat.most_common()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cz", action="store_true", help="also include Czech sources")
    ap.add_argument("--stale-days", type=int, default=7)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    run(include_cz=args.cz, stale_days=args.stale_days)


if __name__ == "__main__":
    main()
