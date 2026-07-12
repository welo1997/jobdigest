"""Jobicy — worldwide remote jobs via the public JSON API (no key)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

JOBICY_API_URL = "https://jobicy.com/api/v2/remote-jobs"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-market-intel/1.0)"}


class JobicySource(BaseSource):
    """Jobicy public API — worldwide remote roles across functions."""

    @property
    def source_name(self) -> str:
        return "jobicy"

    def fetch(self) -> list[dict]:
        try:
            resp = requests.get(
                JOBICY_API_URL, params={"count": 100}, headers=HEADERS, timeout=30
            )
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except requests.RequestException as exc:
            logger.warning("Jobicy failed: %s", exc)
            return []
        logger.info("Jobicy: fetched %d postings", len(jobs))
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
                    title=item.get("jobTitle"),
                    company=item.get("companyName"),
                    url=url,
                    description=item.get("jobDescription") or item.get("jobExcerpt"),
                    location=item.get("jobGeo") or "Remote",
                    country_code=None,  # jobGeo is free text (e.g. "Anywhere", "Europe")
                    remote_signal=True,
                    salary_raw=self._salary(item),
                    currency=item.get("salaryCurrency"),
                    posted_at=self._parse(item.get("pubDate")),
                )
            )
        logger.info("Jobicy: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _salary(item: dict) -> Optional[str]:
        lo, hi = item.get("salaryMin"), item.get("salaryMax")
        if lo and hi:
            return f"{lo} - {hi}"
        return str(lo) if lo else (str(hi) if hi else None)

    @staticmethod
    def _parse(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19], fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
