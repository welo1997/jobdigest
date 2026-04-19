"""Jobs.cz -- scrape Czech IT job listings via HTML."""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jobs.cz/prace/"
# field 200900013 = "Informacni technologie" (IT category)
SEARCH_PARAMS = {"field[]": "200900013"}
MAX_PAGES = 40


class JobsCzSource(BaseSource):
    """Jobs.cz HTML scraper -- Czech IT job listings."""

    @property
    def source_name(self) -> str:
        return "jobscz"

    def fetch(self) -> list[dict]:
        all_cards: list[dict] = []
        seen_urls: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            try:
                params = {**SEARCH_PARAMS, "page": page}
                resp = requests.get(
                    BASE_URL,
                    params=params,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                articles = soup.find_all("article", class_="SearchResultCard")
                if not articles:
                    break

                new_on_page = 0
                for article in articles:
                    card = self._parse_card(article)
                    if card and card["url"] not in seen_urls:
                        seen_urls.add(card["url"])
                        all_cards.append(card)
                        new_on_page += 1

                logger.info(
                    "Jobs.cz page %d: %d listings (%d new)",
                    page, len(articles), new_on_page,
                )
                # Stop if page returned only duplicates (past last page)
                if new_on_page == 0:
                    break
            except requests.RequestException as exc:
                logger.warning("Jobs.cz page %d failed: %s", page, exc)
                break

        logger.info("Jobs.cz: fetched %d listings total", len(all_cards))
        return all_cards

    def _parse_card(self, article: BeautifulSoup) -> Optional[dict]:
        title_link = article.find("a", class_="SearchResultCard__titleLink")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        # Strip tracking query params, keep clean URL
        url = re.sub(r"\?.*$", "", href)

        footer_items = article.find_all(
            "li", class_="SearchResultCard__footerItem"
        )
        company = (
            footer_items[0].get_text(strip=True)
            if len(footer_items) > 0
            else None
        )
        location = (
            footer_items[1].get_text(strip=True)
            if len(footer_items) > 1
            else None
        )

        # Body tags contain info like "remote work possible"
        body = article.find("div", class_="SearchResultCard__body")
        body_tags: list[str] = []
        if body:
            for span in body.find_all("span"):
                body_tags.append(span.get_text(strip=True))
        body_text = "; ".join(body_tags) if body_tags else None

        remote_signal = False
        if body_text:
            remote_keywords = ["z domova", "remote", "home office"]
            remote_signal = any(kw in body_text.lower() for kw in remote_keywords)

        return {
            "title": title,
            "url": url,
            "company": company,
            "location": location,
            "body_text": body_text,
            "remote_signal": remote_signal,
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
                    description=item.get("body_text"),
                    location=item.get("location"),
                    country_code="CZ",
                    remote_signal=item.get("remote_signal", False),
                    salary_raw=None,
                    currency="CZK",
                    posted_at=None,
                )
            )
        logger.info("Jobs.cz: normalised %d postings", len(postings))
        return postings
