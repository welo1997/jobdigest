"""Cocuma.cz -- scrape Czech tech-culture job listings via HTML."""

from __future__ import annotations

import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cocuma.cz/jobs/"
MAX_PAGES = 15


class CocumaSource(BaseSource):
    """Cocuma.cz HTML scraper -- Czech tech/culture-focused jobs."""

    @property
    def source_name(self) -> str:
        return "cocuma"

    def fetch(self) -> list[dict]:
        all_cards: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            try:
                url = BASE_URL if page == 1 else f"{BASE_URL}page/{page}/"
                if not politeness.robots_allows(url):
                    logger.warning("Cocuma: %s is disallowed by robots.txt — stopping", url)
                    break
                politeness.throttle(url)
                resp = requests.get(
                    url,
                    headers=politeness.HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("a", class_="job-thumbnail")
                if not cards:
                    break

                for card in cards:
                    parsed = self._parse_card(card)
                    if parsed:
                        all_cards.append(parsed)

                logger.info(
                    "Cocuma page %d: %d listings", page, len(cards)
                )
            except requests.RequestException as exc:
                logger.warning("Cocuma page %d failed: %s", page, exc)
                break

        logger.info("Cocuma: fetched %d listings total", len(all_cards))
        return all_cards

    def _parse_card(self, card: BeautifulSoup) -> Optional[dict]:
        href = card.get("href", "")
        if not href:
            return None

        # Build absolute URL
        url = f"https://www.cocuma.cz{href}" if href.startswith("/") else href

        title_el = card.find("p", class_="job-thumbnail-title")
        company_el = card.find("p", class_="job-thumbnail-company")
        city_el = card.find("p", class_="job-thumbnail-city")
        schedule_el = card.find("p", class_="job-thumbnail-work-shedule")

        title = title_el.get_text(strip=True) if title_el else None
        company = company_el.get_text(strip=True) if company_el else None
        city = city_el.get_text(strip=True) if city_el else None
        schedule = schedule_el.get_text(strip=True) if schedule_el else None

        return {
            "title": title,
            "url": url,
            "company": company,
            "city": city,
            "schedule": schedule,
        }

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("url", "")
            if not url:
                continue

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("company"),
                    url=url,
                    description=item.get("schedule"),
                    location=item.get("city"),
                    country_code="CZ",
                    remote_signal=None,
                    salary_raw=None,
                    currency="CZK",
                    posted_at=None,
                )
            )
        logger.info("Cocuma: normalised %d postings", len(postings))
        return postings
