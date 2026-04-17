"""Lever ATS — fetch jobs from company board APIs."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)


class LeverSource(BaseSource):
    """Lever ATS postings API — fetches from target companies only."""

    def __init__(self, company_slugs: list[str] | None = None):
        self._company_slugs = company_slugs or self._load_target_slugs()

    @property
    def source_name(self) -> str:
        return "lever"

    @staticmethod
    def _load_target_slugs() -> list[str]:
        """Load Lever company slugs from target_companies.csv."""
        import csv
        import os

        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dbt", "seeds", "target_companies.csv"
        )
        slugs = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("ats") == "lever" and row.get("board_token"):
                        slugs.append(row["board_token"])
        except FileNotFoundError:
            logger.warning("target_companies.csv not found, no Lever boards.")
        return slugs

    def fetch(self) -> list[dict]:
        all_postings: list[dict] = []
        for slug in self._company_slugs:
            url = f"https://api.lever.co/v0/postings/{slug}"
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                postings = resp.json()
                if isinstance(postings, list):
                    for p in postings:
                        p["_company_slug"] = slug
                    all_postings.extend(postings)
                    logger.info("Lever %s: %d postings", slug, len(postings))
            except requests.RequestException as exc:
                logger.warning("Lever %s failed: %s", slug, exc)
        logger.info("Lever: fetched %d total postings", len(all_postings))
        return all_postings

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("hostedUrl", "")
            if not url:
                continue

            location = item.get("categories", {}).get("location")

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("text"),
                    company=item.get("_company_slug"),
                    url=url,
                    description=item.get("descriptionPlain"),
                    location=location,
                    country_code=None,
                    remote_signal=None,
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_timestamp(item.get("createdAt")),
                )
            )
        logger.info("Lever: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_timestamp(ts: Optional[int]) -> Optional[date]:
        """Lever uses epoch milliseconds."""
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(ts / 1000).date()
        except (ValueError, OSError):
            return None
