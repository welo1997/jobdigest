"""Jobs.cz -- scrape Czech job listings via HTML across all tech/business fields.

Jobs.cz filters by a numeric profession id (``field[]``). We iterate a curated set
of tech/IT/business/creative fields (not just IT) so the digest can serve any role
at a tech company -- engineering, product, design, marketing, sales, finance, HR,
legal. Manual/industrial fields (gastronomy, manufacturing, crafts, security,
public admin) are intentionally excluded. Listings are de-duplicated by URL across
fields, since a posting often appears under several professions.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jobs.cz/prace/"

# Curated profession fields, discovered from the jobs.cz taxonomy: label -> (field id,
# role_category hint). Tech/IT/business/creative only; manual & non-tech industries left
# out on purpose. The hint is a coarse fallback used by the ingest classifier only when a
# (often Czech) title can't be categorised from its text. `None` = no confident mapping.
FIELD_CATEGORIES: dict[str, tuple[str, str | None]] = {
    "IS/IT: vyvoj":            ("200900013", "software_engineering"),
    "IS/IT: sprava systemu":   ("200900012", "devops_platform"),
    "IS/IT: konzultace/PM":    ("200900011", "software_engineering"),
    "Elektrotechnika/energ.":  ("200900008", None),
    "Marketing":               ("200900017", "other_tech_function"),
    "Media, reklama, PR":      ("200900018", "other_tech_function"),
    "Prodej a obchod":         ("200900026", "other_tech_function"),
    "Nakup":                   ("200900019", "other_tech_function"),
    "Bankovnictvi/fin. sluzby":("200900002", "other_tech_function"),
    "Ekonomika/podnik. finance":("200900007", "other_tech_function"),
    "Pojistovnictvi":          ("200900022", "other_tech_function"),
    "Pravni sluzby":           ("200900025", "other_tech_function"),
    "Personalistika a HR":     ("200900021", "other_tech_function"),
    # Clerical/office admin is not a tech function. Mapping it to other_tech_function put
    # ~7k "Administrativní pracovník" rows into the same bucket as marketing and sales, and
    # since the bucket is selected by anyone who ticks "Marketing", they drowned every
    # narrower category in the shortlist. Titles that *are* categorisable still classify
    # from their own text ("Marketing Administrator" still hits the marketing pattern);
    # the rest become `uncategorised`, which is a first-class value, not a dropped row.
    "Administrativa":          ("200900001", None),
    "Kultura/umeni/tvurci":    ("200900014", "design"),
}
MAX_PAGES = 30  # per field; the "no new listings" guard usually stops earlier


class JobsCzSource(BaseSource):
    """Jobs.cz HTML scraper -- Czech listings across curated tech/business fields."""

    @property
    def source_name(self) -> str:
        return "jobscz"

    def fetch(self) -> list[dict]:
        all_cards: list[dict] = []
        seen_urls: set[str] = set()  # dedup across fields (a job spans several)
        for label, (field_id, hint) in FIELD_CATEGORIES.items():
            got = self._fetch_field(label, field_id, hint, seen_urls, all_cards)
            logger.info("Jobs.cz [%s]: +%d new (%d total so far)",
                        label, got, len(all_cards))
        logger.info("Jobs.cz: fetched %d unique listings across %d fields",
                    len(all_cards), len(FIELD_CATEGORIES))
        return all_cards

    def _fetch_field(self, label: str, field_id: str, hint: str | None,
                     seen_urls: set[str], sink: list[dict]) -> int:
        added = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = requests.get(
                    BASE_URL,
                    params={"field[]": field_id, "page": page},
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
                    if card:
                        card["source_category"] = hint
                    if card and card["url"] not in seen_urls:
                        seen_urls.add(card["url"])
                        sink.append(card)
                        new_on_page += 1
                        added += 1
                # Past the last page jobs.cz repeats results -> stop on all-dupes.
                if new_on_page == 0:
                    break
            except requests.RequestException as exc:
                logger.warning("Jobs.cz [%s] page %d failed: %s", label, page, exc)
                break
        return added

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
                    source_category=item.get("source_category"),
                )
            )
        logger.info("Jobs.cz: normalised %d postings", len(postings))
        return postings
