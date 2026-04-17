"""Profesia.cz/sk — fetch Czech/Slovak jobs via RSS feed."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import feedparser

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

PROFESIA_FEEDS = [
    ("https://www.profesia.cz/rss/nabidky-prace/informacni-technologie/", "CZ"),
    ("https://www.profesia.sk/rss/ponuky-prace/informacne-technologie/", "SK"),
]


class ProfesiaSource(BaseSource):
    """Profesia RSS — CZ and SK IT jobs."""

    @property
    def source_name(self) -> str:
        return "profesia"

    def fetch(self) -> list[dict]:
        all_entries: list[dict] = []
        for feed_url, country in PROFESIA_FEEDS:
            feed = feedparser.parse(feed_url)
            for e in feed.entries:
                entry = dict(e)
                entry["_country_code"] = country
                all_entries.append(entry)
            logger.info("Profesia %s: %d entries", country, len(feed.entries))
        return all_entries

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
                    company=item.get("author"),
                    url=url,
                    description=item.get("summary") or item.get("description"),
                    location=None,
                    country_code=item.get("_country_code"),
                    remote_signal=None,
                    salary_raw=None,
                    currency="CZK" if item.get("_country_code") == "CZ" else "EUR",
                    posted_at=self._parse_date(item),
                )
            )
        logger.info("Profesia: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_date(item: dict) -> Optional[date]:
        published = item.get("published_parsed")
        if published:
            try:
                return date(*published[:3])
            except (TypeError, ValueError):
                pass
        return None
