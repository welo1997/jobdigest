"""On-demand remote-international data-job search — no Snowflake, no automation.

Fetches from free public job sources, keeps remote data roles that are plausibly
EU-eligible, dedups, and writes a ranked candidate list to JSON for scoring/curation
in chat. Because it fetches live each run, results are always current — no stale-row
or warehouse cost.

Usage:
    python search_jobs.py                 # fetch international remote data roles
    python search_jobs.py --check-live    # also HTTP-probe each URL (slower)
    python search_jobs.py --cz            # also include Czech sources
    python search_jobs.py --limit 60      # cap output

Adzuna is included only if ADZUNA_APP_ID / ADZUNA_API_KEY are set (1Password / env).
Output: candidates.json (+ a printed summary).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Optional

from ingestion.base import JobPosting
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.himalayas import HimalayasSource
from ingestion.sources.remoteok import RemoteOKSource
from ingestion.sources.remotive import RemotiveSource
from ingestion.sources.weworkremotely import WeWorkRemotelySource

logger = logging.getLogger(__name__)

# --- Data-role classification (Python mirror of stg_job_postings role_category) ---
_DATA_PATTERNS = (
    "data engineer", "analytics engineer", "dataops", " etl",
    "data analyst", "bi analyst", "business intelligence",
    "machine learning", "ml engineer", "ai engineer", "data scientist",
)


def is_data_role(title: Optional[str]) -> bool:
    t = (title or "").lower()
    return any(p in t for p in _DATA_PATTERNS)


# --- Region + EU-eligibility heuristics ---
_EU_CODES = {"DE", "NL", "FR", "ES", "PL", "AT", "IE", "PT", "SK", "IT", "BE", "SE", "DK", "FI", "CZ"}


def work_region(location: Optional[str], country_code: Optional[str]) -> str:
    loc = (location or "").lower()
    cc = (country_code or "").upper()
    if cc == "CZ" or re.search(r"czech|praha|prague|brno|ostrava", loc):
        return "cz"
    if re.search(r"worldwide|anywhere|global|fully remote", loc):
        return "worldwide"
    if cc == "GB" or re.search(r"united kingdom|england|london", loc):
        return "uk"
    if cc == "US" or re.search(r"united states|\busa\b|remote us", loc):
        return "us"
    if cc in _EU_CODES or re.search(r"europe|emea|\beu\b|germany|netherlands|poland|spain|france|ireland", loc):
        return "eu"
    return "other"


def eligibility(region: str, text: str) -> str:
    """Coarse EU-eligibility flag for a Czech-based candidate."""
    t = text.lower()
    if re.search(r"security clearance|ts/sci|public trust|must be a us citizen|us citizen", t):
        return "blocked (clearance/US-only)"
    if region in ("eu", "worldwide", "cz"):
        return "eligible"
    if region == "uk":
        return "verify UK right-to-work"
    if region == "us":
        return "likely needs US work auth"
    return "unknown"


# Preference order for grouping (best first).
_RANK = {"eligible": 0, "unknown": 1, "verify UK right-to-work": 2,
         "likely needs US work auth": 3, "blocked (clearance/US-only)": 4}


def dedup_key(p: JobPosting) -> str:
    title = re.sub(r"\b(senior|junior|medior|mid|lead|principal|staff|sr|jr)\b", "",
                   (p.title or "").lower().split("|")[0].split(" - ")[0])
    return re.sub(r"\s+", " ", title).strip() + "::" + (p.company or "").lower().strip()


def gather(include_cz: bool) -> list[JobPosting]:
    sources = [RemotiveSource, WeWorkRemotelySource, RemoteOKSource, HimalayasSource, GreenhouseSource]
    # Adzuna only if keys are present.
    try:
        from ingestion.sources.adzuna import AdzunaSource
        AdzunaSource()  # raises KeyError if creds missing
        sources.append(AdzunaSource)
    except Exception:
        logger.info("Adzuna skipped (no ADZUNA_APP_ID / ADZUNA_API_KEY in env).")
    if include_cz:
        from ingestion.sources.jobscz import JobsCzSource
        from ingestion.sources.startupjobs import StartupJobsSource
        from ingestion.sources.cocuma import CocumaSource
        sources += [StartupJobsSource, JobsCzSource, CocumaSource]

    postings: list[JobPosting] = []
    for cls in sources:
        try:
            src = cls()
            got = src.run()
            postings.extend(got)
            logger.info("%s: %d", src.source_name, len(got))
        except Exception:
            logger.exception("Source %s failed, skipping.", cls.__name__)
    return postings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-live", action="store_true", help="HTTP-probe each URL (slower)")
    ap.add_argument("--cz", action="store_true", help="also include Czech sources")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--out", default="candidates.json")
    args = ap.parse_args()

    postings = gather(include_cz=args.cz)
    logger.info("Collected %d total postings", len(postings))

    # Keep data roles, dedup.
    seen: dict[str, JobPosting] = {}
    for p in postings:
        if p.url and is_data_role(p.title):
            seen.setdefault(dedup_key(p), p)
    candidates = list(seen.values())
    logger.info("%d unique data-role candidates", len(candidates))

    rows = []
    for p in candidates:
        region = work_region(p.location, p.country_code)
        elig = eligibility(region, f"{p.title or ''} {p.location or ''} {p.description or ''}")
        rows.append({
            "title": p.title, "company": p.company, "source": p.source,
            "location": p.location, "region": region, "eligibility": elig,
            "url": p.url, "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "description": (p.description or "")[:600],
        })

    # Best-eligible first; drop the clearly-blocked ones from the top view.
    rows.sort(key=lambda r: (_RANK.get(r["eligibility"], 5), r["region"]))
    rows = rows[: args.limit]

    if args.check_live:
        from notify import check_url_liveness  # lazy: avoids the snowflake import unless needed
        live = []
        for r in rows:
            verdict, note = check_url_liveness(r["url"])
            r["liveness"] = verdict
            if verdict != "dead":
                live.append(r)
        logger.info("Liveness: kept %d of %d", len(live), len(rows))
        rows = live

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    from collections import Counter
    print(f"\n{len(rows)} candidates -> {args.out}")
    print("by eligibility:", dict(Counter(r["eligibility"] for r in rows)))
    print("by region:     ", dict(Counter(r["region"] for r in rows)))
    print("\nTop eligible:")
    for r in [x for x in rows if x["eligibility"] == "eligible"][:12]:
        print(f"  [{r['region']}] {(r['title'] or '?')[:46]:<46} | {(r['company'] or '?')[:24]:<24} | {r['source']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    main()
