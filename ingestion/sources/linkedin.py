"""LinkedIn — fetch remote jobs via public RSS feed."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import feedparser

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

# LinkedIn public RSS with f_WT=2 for remote jobs
# Multiple keyword feeds to broaden coverage
LINKEDIN_FEEDS = [
    "https://www.linkedin.com/jobs/search/?f_WT=2&keywords=data+engineer&sortBy=DD",
    "https://www.linkedin.com/jobs/search/?f_WT=2&keywords=software+engineer&sortBy=DD",
    "https://www.linkedin.com/jobs/search/?f_WT=2&keywords=product+manager&sortBy=DD",
]

# LinkedIn RSS endpoint format
LINKEDIN_RSS_BASE = "https://www.linkedin.com/jobs/searchJobPostings.rss"


class LinkedInSource(BaseSource):
    """LinkedIn public RSS — remote jobs only, no Playwright."""

    @property
    def source_name(self) -> str:
        return "linkedin"

    def fetch(self) -> list[dict]:
        all_entries: list[dict] = []
        keywords = ["data+engineer", "software+engineer", "product+manager",
                     "data+analyst", "devops"]
        for kw in keywords:
            url = f"{LINKEDIN_RSS_BASE}?keywords={kw}&f_WT=2&sortBy=DD"
            feed = feedparser.parse(url)
            all_entries.extend(dict(e) for e in feed.entries)

        logger.info("LinkedIn: fetched %d entries", len(all_entries))
        return all_entries

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        seen: set[str] = set()
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("link", "")
            if not url:
                continue

            pid = make_posting_id(url)
            if pid in seen:
                continue
            seen.add(pid)

            postings.append(
                JobPosting(
                    posting_id=pid,
                    source=self.source_name,
                    title=item.get("title"),
                    company=None,  # LinkedIn RSS doesn't reliably include company
                    url=url,
                    description=item.get("summary") or item.get("description"),
                    location=None,
                    country_code=None,
                    remote_signal=True,  # f_WT=2 filter = remote
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item),
                )
            )
        logger.info("LinkedIn: normalised %d postings", len(postings))
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
