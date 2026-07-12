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

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-market-intel/1.0)"}

# Verified-live Ashby boards (remote-first / data / AI + fintech / ecommerce).
ORGS = [
    "notion", "openai", "ramp", "cohere", "supabase", "elevenlabs",
    "baseten", "linear", "posthog", "langchain", "modal",
    # fintech / trading / ecommerce
    "plaid", "pleo", "wealthsimple", "mollie", "keyrock", "backmarket", "deliveroo",
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
