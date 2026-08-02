"""Remotive.com — fetch all remote tech jobs via their public API."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

# No `category=data`. That parameter was here since this repo served one person hunting data
# roles, and as of 2026-08-01 Remotive's public API **ignores it**: the filtered and unfiltered
# calls return the same 34 jobs, spread across 13 categories with exactly one in "Data and
# Analytics". So it narrowed nothing and merely misdescribed what this source returns —
# a reader would reasonably assume the other eight taxonomy categories were being excluded
# here, and act on that. Dropped for honesty; the volume is unchanged either way.
REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveSource(BaseSource):
    """Remotive public JSON API — returns all remote tech jobs."""

    @property
    def source_name(self) -> str:
        return "remotive"

    def fetch(self) -> list[dict]:
        resp = requests.get(REMOTIVE_API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", [])
        logger.info("Remotive: fetched %d postings", len(jobs))
        return jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("url", "")
            if not url:
                continue

            posted_at = self._parse_date(item.get("publication_date"))
            salary_raw = self._build_salary(item)

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("company_name"),
                    url=url,
                    description=item.get("description"),
                    location=item.get("candidate_required_location"),
                    country_code=None,  # Remotive gives free-text location
                    remote_signal=True,  # inherently remote source
                    salary_raw=salary_raw,
                    currency=None,
                    posted_at=posted_at,
                )
            )
        logger.info("Remotive: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        parts = []
        if item.get("salary"):
            return item["salary"]
        return None
