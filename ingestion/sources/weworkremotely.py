"""WeWorkRemotely — fetch all remote jobs via RSS feed."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import feedparser

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

WWR_FEED_URL = "https://weworkremotely.com/remote-jobs.rss"


class WeWorkRemotelySource(BaseSource):
    """WeWorkRemotely RSS — all categories."""

    @property
    def source_name(self) -> str:
        return "weworkremotely"

    def fetch(self) -> list[dict]:
        feed = feedparser.parse(WWR_FEED_URL)
        logger.info("WeWorkRemotely: fetched %d entries", len(feed.entries))
        return [dict(e) for e in feed.entries]

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("link", "")
            if not url:
                continue

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=self._extract_company(item.get("title", "")),
                    url=url,
                    description=item.get("summary") or item.get("description"),
                    location=None,
                    country_code=None,
                    remote_signal=True,
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item),
                )
            )
        logger.info("WeWorkRemotely: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _extract_company(title: str) -> Optional[str]:
        """WWR titles are often 'Company: Job Title'."""
        if ":" in title:
            return title.split(":")[0].strip()
        return None

    @staticmethod
    def _parse_date(item: dict) -> Optional[date]:
        published = item.get("published_parsed")
        if published:
            try:
                return date(*published[:3])
            except (TypeError, ValueError):
                pass
        return None
