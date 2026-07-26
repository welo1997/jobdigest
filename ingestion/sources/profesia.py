"""Profesia.cz / Profesia.sk -- scrape Czech & Slovak jobs via HTML.

Profesia retired its per-category RSS feeds (they now 404 / redirect to HTML), so
this scrapes the HTML listing pages instead. Profesia is the dominant Slovak board
(profesia.sk) and a sizeable Czech one (profesia.cz); both share the same markup:
one job per ``li.list-row`` with ``h2 a span.title`` / ``.employer`` / ``.job-location``.

We iterate a curated set of tech/IT/business/creative profession areas per country
(not just IT) and de-duplicate by URL, so the digest can serve any role. Pagination
is ``?page_num=N``. Manual/industrial areas are intentionally excluded.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

# Per-country config. `path` is the localised /jobs/ segment; `professions` are
# profession-area slugs verified to return listings. `veda-a-vyskum` etc. omitted
# (empty). SK carries far more volume than CZ on profesia.
# `professions` are (slug, role_category hint) pairs. The hint is a coarse fallback the
# ingest classifier uses only when a (usually Czech/Slovak) title can't be categorised
# from its text. `None` = no confident mapping (management/telecom/electro are too mixed).
SITES = [
    {
        "country": "CZ", "currency": "CZK",
        "root": "https://www.profesia.cz", "path": "/prace/",
        "professions": [
            ("informacni-technologie", "software_engineering"),
            ("elektrotechnika-a-energetika", None),
            ("marketing-reklama-a-pr", "other_tech_function"),
            ("media-a-zurnalistika", "other_tech_function"),
            ("design", "design"),
            ("obchod", "other_tech_function"),
            ("nakup", "other_tech_function"),
            ("ekonomika-finance-ucetnictvi", "other_tech_function"),
            ("management", None),
            ("telekomunikace", None),
            ("bankovnictvi", "other_tech_function"),
            ("pojistovnictvi", "other_tech_function"),
            ("pravo-a-legislativa", "other_tech_function"),
            ("personalistika-a-lidske-zdroje", "other_tech_function"),
            # Clerical office admin is not a tech function -- see jobscz.py for the
            # shortlist flooding this caused. Falls through to `uncategorised`.
            ("administrativa", None),
        ],
    },
    {
        "country": "SK", "currency": "EUR",
        "root": "https://www.profesia.sk", "path": "/praca/",
        "professions": [
            ("informacne-technologie", "software_engineering"),
            ("elektrotechnika-a-energetika", None),
            ("marketing-reklama-a-pr", "other_tech_function"),
            ("media-a-zurnalistika", "other_tech_function"),
            ("design", "design"),
            ("obchod", "other_tech_function"),
            ("ekonomika-financie-uctovnictvo", "other_tech_function"),
            ("manazment", None),
            ("telekomunikacie", None),
            ("bankovnictvo", "other_tech_function"),
            ("poistovnictvo", "other_tech_function"),
            ("pravo-a-legislativa", "other_tech_function"),
            ("personalistika-a-ludske-zdroje", "other_tech_function"),
            # Clerical office admin is not a tech function -- see jobscz.py for the
            # shortlist flooding this caused. Falls through to `uncategorised`.
            ("administrativa", None),
        ],
    },
]
MAX_PAGES = 15  # per profession; stops early when a page yields no new listings
_REMOTE_HINTS = ("z domu", "z domova", "domácej práce", "domaci prace",
                 "remote", "home office")


class ProfesiaSource(BaseSource):
    """Profesia.cz + Profesia.sk HTML scraper across curated profession areas."""

    @property
    def source_name(self) -> str:
        return "profesia"

    def fetch(self) -> list[dict]:
        all_cards: list[dict] = []
        for site in SITES:
            seen: set[str] = set()  # dedup by URL within a country
            before = len(all_cards)
            for slug, hint in site["professions"]:
                self._fetch_profession(site, slug, hint, seen, all_cards)
            logger.info("Profesia %s: %d listings across %d areas",
                        site["country"], len(all_cards) - before,
                        len(site["professions"]))
        logger.info("Profesia: fetched %d listings total", len(all_cards))
        return all_cards

    def _fetch_profession(self, site: dict, slug: str, hint: str | None,
                          seen: set[str], sink: list[dict]) -> None:
        base = f"{site['root']}{site['path']}{slug}/"
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = requests.get(
                    base,
                    params={"page_num": page} if page > 1 else None,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning("Profesia %s/%s page %d failed: %s",
                               site["country"], slug, page, exc)
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("li.list-row")
            if not rows:
                break

            new_on_page = 0
            for row in rows:
                card = self._parse_row(row, site)
                if card:
                    card["source_category"] = hint
                if card and card["url"] not in seen:
                    seen.add(card["url"])
                    sink.append(card)
                    new_on_page += 1
            if new_on_page == 0:
                break

    def _parse_row(self, row: BeautifulSoup, site: dict) -> Optional[dict]:
        link = row.select_one("h2 a")
        if not link:
            return None
        href = link.get("href", "").split("?")[0]
        if not href:
            return None
        url = href if href.startswith("http") else site["root"] + href

        title_el = link.select_one("span.title")
        title = (title_el or link).get_text(strip=True)

        emp = row.select_one(".employer")
        company = emp.get_text(strip=True) if emp else None

        loc_el = row.select_one(".job-location")
        location = loc_el.get_text(" ", strip=True) if loc_el else None

        remote = bool(location and any(h in location.lower() for h in _REMOTE_HINTS))

        return {
            "url": url, "title": title, "company": company,
            "location": location, "remote_signal": remote,
            "country_code": site["country"], "currency": site["currency"],
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
                    description=None,
                    location=item.get("location"),
                    country_code=item.get("country_code"),
                    remote_signal=item.get("remote_signal", False),
                    salary_raw=None,
                    currency=item.get("currency"),
                    posted_at=None,
                    source_category=item.get("source_category"),
                )
            )
        logger.info("Profesia: normalised %d postings", len(postings))
        return postings
