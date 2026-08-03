"""Ashby ATS — fetch jobs from company job-board APIs (no key).

Public endpoint: https://api.ashbyhq.com/posting-api/job-board/{org}
Org tokens below are verified-live remote-first / data / AI companies; grow freely.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = politeness.HEADERS

# Verified-live Ashby boards (remote-first / data / AI + fintech / ecommerce). Whole list
# re-probed 2026-08-01: all 18 originals still serve jobs, and the additions below were
# confirmed the same way. A board that goes dark is a silent zero — `fetch` skips a non-200
# and logs nothing at error level — so re-probe rather than assume when volume drops.
#
# Deliberately NOT here despite being live on Ashby: `clickhouse` (already a Greenhouse board
# in dbt/seeds/target_companies.csv) and `qonto` (already a Lever org). Both serve the same
# postings under a second source, and while `digest.dedupe_key` would collapse them in the
# email, they would still occupy two rows and two shortlist slots each.
ORGS = [
    "notion", "openai", "ramp", "cohere", "supabase", "elevenlabs",
    "baseten", "linear", "posthog", "langchain", "modal",
    # fintech / trading / ecommerce
    "plaid", "pleo", "wealthsimple", "mollie", "keyrock", "backmarket", "deliveroo",
    # AI / applied ML, added 2026-08-01
    "harvey", "sierra", "cursor", "synthesia", "lovable", "writer", "abridge",
    "runway", "photoroom", "langfuse", "n8n",
    # data / infra / devtools
    "airbyte", "prefect", "motherduck", "neon", "render", "railway", "resend",
    "vanta", "attio", "tldraw", "dune",
    # EU-based (FR/DE/NL/PT), which the rest of this list is thin on
    "alan", "swan", "tacto", "forto", "choco", "oyster",
    # second wave, verified 2026-08-01 — AI labs and applied-AI, the densest hiring segment
    "perplexity", "cognition", "decagon", "mercor", "suno", "pika", "listenlabs",
    "physicalintelligence", "poolside", "rogo", "legora", "granola",
    # fintech / infra / devtools
    "airwallex", "column", "primer", "unit", "vivid", "warp", "zed", "chromatic",
    # GB-based, which this list had almost none of
    "wayve", "applied",
]

# Map Ashby address country names to ISO-3166 alpha-2.
_COUNTRY = {
    "united states": "US", "united kingdom": "GB", "germany": "DE",
    "netherlands": "NL", "france": "FR", "spain": "ES", "ireland": "IE",
    "poland": "PL", "portugal": "PT", "canada": "CA", "czechia": "CZ",
}


class AshbySource(BaseSource):
    """Ashby public job-board API across a curated list of orgs."""

    def __init__(self, orgs: Optional[list[str]] = None):
        self._orgs = orgs or ORGS

    @property
    def source_name(self) -> str:
        return "ashby"

    def fetch(self) -> list[dict]:
        all_jobs: list[dict] = []
        for org in self._orgs:
            try:
                resp = requests.get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                    headers=HEADERS, timeout=15,
                )
                if resp.status_code != 200:
                    continue
                for j in resp.json().get("jobs", []):
                    j["_org"] = org
                    all_jobs.append(j)
            except requests.RequestException as exc:
                logger.warning("Ashby %s failed: %s", org, exc)
        logger.info("Ashby: fetched %d jobs across %d orgs", len(all_jobs), len(self._orgs))
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            if item.get("isListed") is False:
                continue
            url = item.get("jobUrl") or item.get("applyUrl") or ""
            if not url:
                continue
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("_org"),
                    url=url,
                    description=item.get("descriptionHtml") or item.get("descriptionPlain"),
                    location=item.get("location"),
                    country_code=self._country(item),
                    remote_signal=bool(item.get("isRemote")),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse(item.get("publishedAt")),
                )
            )
        logger.info("Ashby: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _country(item: dict) -> Optional[str]:
        try:
            name = item["address"]["postalAddress"]["addressCountry"]
            return _COUNTRY.get(str(name).strip().lower())
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _parse(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
