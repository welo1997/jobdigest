"""Abstract base class for all job posting sources."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class JobPosting:
    """Normalised job posting ready for loading into Snowflake."""

    posting_id: str
    source: str
    title: Optional[str] = None
    company: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    country_code: Optional[str] = None
    remote_signal: Optional[bool] = None
    salary_raw: Optional[str] = None
    currency: Optional[str] = None
    posted_at: Optional[date] = None

    def as_tuple(self) -> tuple:
        """Return values in column order for Snowflake INSERT."""
        return (
            self.posting_id,
            self.source,
            self.title,
            self.company,
            self.url,
            self.description,
            self.location,
            self.country_code,
            self.remote_signal,
            self.salary_raw,
            self.currency,
            self.posted_at.isoformat() if self.posted_at else None,
        )


def make_posting_id(url: str) -> str:
    """Generate a deterministic posting_id from a URL."""
    return hashlib.md5(url.encode()).hexdigest()


class BaseSource(ABC):
    """Abstract source — every scraper/fetcher subclasses this."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short lowercase identifier, e.g. 'remotive', 'adzuna'."""

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Fetch raw data from the external source. Returns list of raw dicts."""

    @abstractmethod
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        """Convert raw dicts into JobPosting dataclasses."""

    def run(self) -> list[JobPosting]:
        """Fetch + normalize in one call."""
        raw = self.fetch()
        return self.normalize(raw)
