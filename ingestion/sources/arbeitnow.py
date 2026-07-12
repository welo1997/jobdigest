"""Arbeitnow — EU / German-market job board via the public API (no key)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

ARBEITNOW_API_URL = "https://www.arbeitnow.com/api/job-board-api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-market-intel/1.0)"}
MAX_PAGES = 3


class ArbeitnowSource(BaseSource):
    """Arbeitnow public API — EU-focused board, includes remote flag."""

    @property
    def source_name(self) -> str:
        return "arbeitnow"

    def fetch(self) -> list[dict]:
        all_jobs: list[dict] = []
        url: Optional[str] = ARBEITNOW_API_URL
        for _ in range(MAX_PAGES):
            if not url:
                break
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
            except requests.RequestException as exc:
                logger.warning("Arbeitnow failed: %s", exc)
                break
            all_jobs.extend(payload.get("data", []))
            url = (payload.get("links") or {}).get("next")
        logger.info("Arbeitnow: fetched %d postings", len(all_jobs))
        return all_jobs

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
                    country_code="DE",  # Arbeitnow is a German/DACH board
                    remote_signal=bool(item.get("remote")),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_epoch(item.get("created_at")),
                )
            )
        logger.info("Arbeitnow: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_epoch(epoch: Optional[int]) -> Optional[date]:
        if not epoch:
            return None
        try:
            return datetime.utcfromtimestamp(int(epoch)).date()
        except (ValueError, OSError, OverflowError):
            return None
