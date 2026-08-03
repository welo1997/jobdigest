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
from ingestion import politeness

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jobs.cz/prace/"

# Jobs.cz expresses work setup as a body tag, and its vocabulary is graded rather than
# binary: "Možnost občasné práce z domova" (occasional), "Práce převážně z domova"
# (predominantly). Both contain "z domova" and NEITHER is a fully remote role.
#
# This matters far beyond a mislabelled field, because `remote_signal` is what exempts a
# posting from the location gate — `geo.location_predicate` matches fully-remote roles on
# `remote_scope` instead of city, by design. So every one of these was reaching subscribers
# who had ruled that place out. Measured on live inventory 2026-07-28: 3 744 of 3 744
# jobs.cz postings carrying the flag were qualified phrasings, i.e. the flag was wrong every
# single time it was set on this source, and 40 of 50 evaluation personas had on-site Czech
# work in their shortlist as a result — including subscribers who had picked Germany,
# the Netherlands or Slovakia and nothing else.
#
# "Hybrid is not remote" is the rule (see CLAUDE.md); these qualifiers are precisely how
# hybrid is spelled in Czech, so they must lose to the remote keywords, not merely coexist.
# Note this does not hide such a posting from anyone: it stays fully retrievable for a
# subscriber who chose that city. It only stops it bypassing the location filter.
_HYBRID_QUALIFIERS = (
    "občasn", "obcasn",          # Možnost OBČASNÉ práce z domova
    "převážně", "prevazne",      # Práce PŘEVÁŽNĚ z domova — still expects on-site presence
    "částečně", "castecne",
    "příležitostn", "prilezitostn",
    "možnost",                   # "Možnost práce z domova" is an option, not the arrangement
)
_REMOTE_KEYWORDS = ("z domova", "remote", "home office")


def _remote_from_body(body_text: str | None) -> bool:
    """True only for unqualified remote work. Empty/absent tags mean "not stated" = False."""
    if not body_text:
        return False
    text = body_text.lower()
    if any(q in text for q in _HYBRID_QUALIFIERS):
        return False
    return any(kw in text for kw in _REMOTE_KEYWORDS)

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
        if not politeness.robots_allows(BASE_URL):
            logger.warning("Jobs.cz [%s]: %s is disallowed by robots.txt — skipping",
                           label, BASE_URL)
            return 0
        for page in range(1, MAX_PAGES + 1):
            try:
                politeness.throttle(BASE_URL)
                resp = requests.get(
                    BASE_URL,
                    params={"field[]": field_id, "page": page},
                    headers=politeness.HEADERS,
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

        remote_signal = _remote_from_body(body_text)

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
