"""StartupJobs.cz — fetch Czech startup jobs via public JSON API."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

STARTUPJOBS_API_URL = "https://www.startupjobs.cz/api/offers"
MAX_PAGES = 25  # API has 25 pages max


class StartupJobsSource(BaseSource):
    """StartupJobs.cz JSON API — Czech startup/tech jobs."""

    @property
    def source_name(self) -> str:
        return "startupjobs"

    def fetch(self) -> list[dict]:
        all_offers: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = requests.get(
                    STARTUPJOBS_API_URL, params={"page": page}, timeout=15
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("resultSet", [])
                if not results:
                    break
                all_offers.extend(results)
                max_page = data.get("paginator", {}).get("max", 1)
                if page >= max_page:
                    break
            except requests.RequestException as exc:
                logger.warning("StartupJobs page %d failed: %s", page, exc)
                break

        logger.info("StartupJobs: fetched %d offers", len(all_offers))
        return all_offers

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            slug = item.get("slug", "")
            offer_id = item.get("id", "")
            url = f"https://www.startupjobs.cz/nabidka/{offer_id}/{slug}" if offer_id else ""
            if not url:
                continue

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("name"),
                    company=self._extract_company(item),
                    url=url,
                    description=item.get("description"),
                    location=None,
                    country_code="CZ",
                    remote_signal=None,
                    salary_raw=self._build_salary(item),
                    currency="CZK",
                    posted_at=None,
                )
            )
        logger.info("StartupJobs: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _extract_company(item: dict) -> Optional[str]:
        company = item.get("company")
        if isinstance(company, dict):
            return company.get("name")
        if isinstance(company, str):
            return company
        return None

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        salary = item.get("salary")
        if isinstance(salary, dict):
            sal_min = salary.get("min")
            sal_max = salary.get("max")
            if sal_min and sal_max:
                return f"{sal_min} - {sal_max}"
            if sal_min:
                return str(sal_min)
            if sal_max:
                return str(sal_max)
        if isinstance(salary, str) and salary:
            return salary
        return None
