"""Greenhouse ATS — fetch jobs from company board APIs."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)


class GreenhouseSource(BaseSource):
    """Greenhouse ATS board API — fetches from target companies only."""

    def __init__(self, board_tokens: list[str] | None = None):
        self._board_tokens = board_tokens or self._load_target_tokens()

    @property
    def source_name(self) -> str:
        return "greenhouse"

    @staticmethod
    def _load_target_tokens() -> list[str]:
        """Load Greenhouse board tokens from target_companies.csv."""
        import csv
        import os

        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dbt", "seeds", "target_companies.csv"
        )
        tokens = []
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("ats") == "greenhouse" and row.get("board_token"):
                        tokens.append(row["board_token"])
        except FileNotFoundError:
            logger.warning("target_companies.csv not found, no Greenhouse boards.")
        return tokens

    def fetch(self) -> list[dict]:
        all_jobs: list[dict] = []
        for token in self._board_tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
                for j in jobs:
                    j["_board_token"] = token
                all_jobs.extend(jobs)
                logger.info("Greenhouse %s: %d jobs", token, len(jobs))
            except requests.RequestException as exc:
                logger.warning("Greenhouse %s failed: %s", token, exc)
        logger.info("Greenhouse: fetched %d total jobs", len(all_jobs))
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            abs_url = item.get("absolute_url", "")
            if not abs_url:
                continue

            location = self._extract_location(item)

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(abs_url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("_board_token"),  # will map via taxonomy
                    url=abs_url,
                    description=html.unescape(item["content"]) if item.get("content") else None,
                    location=location,
                    country_code=None,
                    remote_signal=None,
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item.get("updated_at")),
                )
            )
        logger.info("Greenhouse: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _extract_location(item: dict) -> Optional[str]:
        locations = item.get("location", {})
        if isinstance(locations, dict):
            return locations.get("name")
        return None

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
