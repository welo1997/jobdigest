"""Adzuna — fetch tech jobs via REST API across multiple countries."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

# Country config: (country_code, pages_to_fetch). Focused on the largest
# remote-hiring markets rather than broad local coverage — the personal focus is
# international remote, and Adzuna has no remote flag so we lean on the search
# phrase. Total requests = sum(pages) * len(SEARCH_TERMS), well under 250/day.
COUNTRY_CONFIG = [
    ("gb", 2),   # 40 results
    ("us", 3),   # 60 results
    ("de", 1),   # 20 results
    ("nl", 1),   # 20 results
]

# Remote-focused data-role search phrases (Adzuna `what` matches all words), so
# results skew heavily toward remote data/analytics/ML roles instead of local jobs.
SEARCH_TERMS = [
    "remote data engineer",
    "remote data analyst",
    "remote analytics engineer",
    "remote data scientist",
    "remote machine learning engineer",
]

RESULTS_PER_PAGE = 20

# Substrings that mark a posting as remote (used to set remote_signal).
_REMOTE_HINTS = ("remote", "work from home", "anywhere", "distributed")


class AdzunaSource(BaseSource):
    """Adzuna REST API — loops countries and search terms."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._app_id = app_id or os.environ["ADZUNA_APP_ID"]
        self._api_key = api_key or os.environ["ADZUNA_API_KEY"]

    @property
    def source_name(self) -> str:
        return "adzuna"

    def fetch(self) -> list[dict]:
        all_results: list[dict] = []
        seen_ids: set[str] = set()

        for country, max_pages in COUNTRY_CONFIG:
            for term in SEARCH_TERMS:
                for page in range(1, max_pages + 1):
                    items = self._fetch_page(country, term, page)
                    for item in items:
                        adzuna_id = str(item.get("id", ""))
                        if adzuna_id and adzuna_id not in seen_ids:
                            seen_ids.add(adzuna_id)
                            item["_country"] = country
                            all_results.append(item)

        logger.info("Adzuna: fetched %d unique postings", len(all_results))
        return all_results

    def _fetch_page(self, country: str, what: str, page: int) -> list[dict]:
        url = f"{ADZUNA_BASE}/{country}/search/{page}"
        params = {
            "app_id": self._app_id,
            "app_key": self._api_key,
            "what": what,
            "results_per_page": RESULTS_PER_PAGE,
            "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            logger.warning("Adzuna %s/%s p%d failed: %s", country, what, page, exc)
            return []

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            redirect_url = item.get("redirect_url", "")
            if not redirect_url:
                continue

            salary_raw = self._build_salary(item)
            currency = self._currency_for_country(item.get("_country", ""))
            location = item.get("location", {}).get("display_name")
            blob = f"{item.get('title', '')} {location or ''} {item.get('description', '')}".lower()
            is_remote = any(hint in blob for hint in _REMOTE_HINTS) or None

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(redirect_url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("company", {}).get("display_name"),
                    url=redirect_url,
                    description=item.get("description"),
                    location=location,
                    country_code=item.get("_country", "").upper() or None,
                    remote_signal=is_remote,  # inferred from the posting text
                    salary_raw=salary_raw,
                    currency=currency,
                    posted_at=self._parse_date(item.get("created")),
                )
            )
        logger.info("Adzuna: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        if sal_min and sal_max:
            return f"{sal_min} - {sal_max}"
        if sal_min:
            return str(sal_min)
        if sal_max:
            return str(sal_max)
        return None

    @staticmethod
    def _currency_for_country(country: str) -> Optional[str]:
        return {
            "pl": "PLN",
            "at": "EUR",
            "de": "EUR",
            "nl": "EUR",
            "gb": "GBP",
            "us": "USD",
        }.get(country.lower())

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
