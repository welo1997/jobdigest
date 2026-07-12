"""RemoteOK — fetch worldwide remote jobs via the public JSON API.

https://remoteok.com/api returns a flat JSON list. The first element is a legal
notice (no job fields) and is skipped. Every listing is inherently remote.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

REMOTEOK_API_URL = "https://remoteok.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-market-intel/1.0)"}
# Tag-filtered feeds concentrate data/analytics roles (the unfiltered feed is
# mostly non-tech). Results are deduped by id across tags.
TAGS = ["data", "analytics", "machine-learning"]


class RemoteOKSource(BaseSource):
    """RemoteOK public API — worldwide remote data/analytics roles."""

    @property
    def source_name(self) -> str:
        return "remoteok"

    def fetch(self) -> list[dict]:
        jobs: dict[int, dict] = {}
        for tag in TAGS:
            try:
                resp = requests.get(
                    REMOTEOK_API_URL, params={"tags": tag}, headers=HEADERS, timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                logger.warning("RemoteOK tag %s failed: %s", tag, exc)
                continue
            for d in data:
                # First element is a legal/licence notice, not a job.
                if isinstance(d, dict) and d.get("id") and d.get("position"):
                    jobs[d["id"]] = d
        logger.info("RemoteOK: fetched %d unique postings across %d tags", len(jobs), len(TAGS))
        return list(jobs.values())

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("url") or item.get("apply_url") or ""
            if not url:
                continue
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("position"),
                    company=item.get("company"),
                    url=url,
                    description=item.get("description"),
                    location=item.get("location") or "Remote",
                    country_code=None,  # free-text / worldwide
                    remote_signal=True,  # inherently remote source
                    salary_raw=self._build_salary(item),
                    currency="USD" if self._build_salary(item) else None,
                    posted_at=self._parse_date(item.get("date"), item.get("epoch")),
                )
            )
        logger.info("RemoteOK: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        lo, hi = item.get("salary_min") or 0, item.get("salary_max") or 0
        if lo and hi:
            return f"{lo} - {hi}"
        return str(lo) if lo else (str(hi) if hi else None)

    @staticmethod
    def _parse_date(date_str: Optional[str], epoch: Optional[int]) -> Optional[date]:
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                pass
        if epoch:
            try:
                return datetime.utcfromtimestamp(int(epoch)).date()
            except (ValueError, OSError, OverflowError):
                pass
        return None
