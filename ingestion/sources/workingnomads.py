"""Working Nomads — remote job board (freelance-network friendly) via public JSON.

The `/api/exposed_jobs/` endpoint returns the current featured remote listings with
no key. Volume is small (~30) but it surfaces freelance-marketplace ads
(Lemon.io, Toptal-style networks) and remote data roles that don't appear elsewhere.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

WORKINGNOMADS_API_URL = "https://www.workingnomads.com/api/exposed_jobs/"
HEADERS = politeness.HEADERS


class WorkingNomadsSource(BaseSource):
    """Working Nomads public JSON feed — remote, freelance-friendly."""

    @property
    def source_name(self) -> str:
        return "workingnomads"

    def fetch(self) -> list[dict]:
        try:
            resp = requests.get(WORKINGNOMADS_API_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            jobs = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Working Nomads failed: %s", exc)
            return []
        logger.info("Working Nomads: fetched %d postings", len(jobs))
        return jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("url") or ""
            if not url:
                continue
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("company_name"),
                    url=url,
                    description=item.get("description"),
                    location=item.get("location"),
                    country_code=None,  # free-text location
                    remote_signal=True,  # inherently remote board
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item.get("pub_date")),
                )
            )
        logger.info("Working Nomads: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str).date()
        except (ValueError, AttributeError):
            return None
