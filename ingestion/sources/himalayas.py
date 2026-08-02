"""Himalayas — fetch worldwide remote jobs via the public JSON API.

https://himalayas.app/jobs/api?limit=&offset= returns {"jobs": [...]}. Each job
carries `locationRestrictions` (list) and `timezoneRestrictions`, which we keep in
the location text for region tagging, and an `expiryDate` epoch used to skip
already-expired postings at ingestion time. Every listing is remote.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

HIMALAYAS_API_URL = "https://himalayas.app/jobs/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-market-intel/1.0)"}
#: What we ask for. Himalayas caps a response at 20 regardless, so this is an upper bound and
#: never a stride — see `fetch`, which advances by the count actually received.
PAGE_SIZE = 100
MAX_PAGES = 15

# Map common Himalayas location-restriction strings to ISO-3166 alpha-2.
_COUNTRY_MAP = {
    "united states": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB",
    "germany": "DE", "netherlands": "NL", "france": "FR", "spain": "ES",
    "poland": "PL", "austria": "AT", "ireland": "IE", "portugal": "PT",
    "czech republic": "CZ", "czechia": "CZ", "slovakia": "SK", "canada": "CA",
}


class HimalayasSource(BaseSource):
    """Himalayas public API — worldwide remote roles with region restrictions."""

    @property
    def source_name(self) -> str:
        return "himalayas"

    def fetch(self) -> list[dict]:
        """Page by what the API *returns*, not by what we asked for.

        Himalayas caps a response at 20 jobs however large a `limit` you send. The loop used
        to advance `offset` by the requested PAGE_SIZE (100) and stop on
        `len(jobs) < PAGE_SIZE` — so the first response, 20 jobs against a request for 100,
        satisfied the stop condition every single time. This source has been returning exactly
        one page since it was written; the other four pages were never fetched, and the 80
        jobs between each offset step would have been skipped even if they had been.

        Neither half is visible from the outside: 20 postings is a plausible number for a
        remote board, and nothing logs a short page. Advancing by the received count fixes
        both at once and needs no constant to stay in sync with their server.
        """
        all_jobs: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            try:
                resp = requests.get(
                    HIMALAYAS_API_URL,
                    params={"limit": PAGE_SIZE, "offset": offset},
                    headers=HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except requests.RequestException as exc:
                logger.warning("Himalayas offset %d failed: %s", offset, exc)
                break
            if not jobs:
                break
            all_jobs.extend(jobs)
            offset += len(jobs)
        logger.info("Himalayas: fetched %d postings over %d pages", len(all_jobs), MAX_PAGES)
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        now = time.time()
        postings: list[JobPosting] = []
        skipped_expired = 0
        for item in raw_items:
            url = item.get("applicationLink") or ""
            if not url:
                continue
            expiry = item.get("expiryDate")
            if expiry and float(expiry) < now:
                skipped_expired += 1
                continue

            locations = item.get("locationRestrictions") or []
            location = ", ".join(locations) if locations else "Remote"

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("companyName"),
                    url=url,
                    description=item.get("description") or item.get("excerpt"),
                    location=location,
                    country_code=self._country_from(locations),
                    remote_signal=True,  # inherently remote source
                    salary_raw=self._build_salary(item),
                    currency=item.get("currency"),
                    posted_at=self._parse_epoch(item.get("pubDate")),
                )
            )
        logger.info(
            "Himalayas: normalised %d postings (%d expired skipped)",
            len(postings), skipped_expired,
        )
        return postings

    @staticmethod
    def _country_from(locations: list) -> Optional[str]:
        for loc in locations:
            code = _COUNTRY_MAP.get(str(loc).strip().lower())
            if code:
                return code
        return None

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        lo, hi = item.get("minSalary"), item.get("maxSalary")
        if lo and hi:
            return f"{lo} - {hi}"
        return str(lo) if lo else (str(hi) if hi else None)

    @staticmethod
    def _parse_epoch(epoch: Optional[int]) -> Optional[date]:
        if not epoch:
            return None
        try:
            return datetime.utcfromtimestamp(int(epoch)).date()
        except (ValueError, OSError, OverflowError):
            return None
