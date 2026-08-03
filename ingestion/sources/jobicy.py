"""Jobicy — worldwide remote jobs via the public JSON API (no key)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

JOBICY_API_URL = "https://jobicy.com/api/v2/remote-jobs"
HEADERS = politeness.HEADERS

#: The slices to read, each capped at 100 by Jobicy. The bare query first (newest across the
#: whole board), then the industry and region cuts that reach past that ceiling. Measured
#: 2026-08-01: engineering 100, marketing 100, business 78, data-science 61, copywriting 10,
#: geo europe 100, geo usa 100. Values are exact strings — an unrecognised one returns an
#: empty list and no error.
QUERIES: tuple[dict[str, str], ...] = (
    {},
    {"industry": "engineering"},
    {"industry": "data-science"},
    {"industry": "marketing"},
    {"industry": "business"},
    {"industry": "copywriting"},
    {"geo": "europe"},
    {"geo": "usa"},
)


class JobicySource(BaseSource):
    """Jobicy public API — worldwide remote roles across functions."""

    @property
    def source_name(self) -> str:
        return "jobicy"

    def fetch(self) -> list[dict]:
        """One request per slice, deduped by URL.

        Jobicy hard-caps a response at 100 jobs — `count=200` returns 100 — and offers no
        offset, so a single call can only ever see the newest hundred across the whole board.
        The `industry` and `geo` parameters are the only way past that ceiling: each returns
        its own hundred. Verified live 2026-08-01; the industry values are exact, and a wrong
        one fails silently with an empty list rather than an error (`design` is not a value
        Jobicy knows, it returns 0), which is why these are pinned rather than guessed.
        """
        seen: dict[str, dict] = {}
        for params in QUERIES:
            try:
                resp = requests.get(
                    JOBICY_API_URL, params={"count": 100, **params},
                    headers=HEADERS, timeout=30,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Jobicy %s failed: %s", params, exc)
                continue
            for job in jobs:
                url = job.get("url")
                if url and url not in seen:
                    seen[url] = job
        logger.info("Jobicy: fetched %d unique postings across %d slices",
                    len(seen), len(QUERIES))
        return list(seen.values())

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
