"""Remote in Europe — a curated EU-remote board, read from its own sitemap.

Permission
----------
**This source publishes no terms of use and no privacy policy.** Its `robots.txt` permits the
job pages (disallowing only `/search`, `/companies`, `/checkout`, `/post-a-job-2` and
`/internal/`) and it advertises `sitemap.xml`; nothing on the host refuses automated reading,
because there is no document that says anything at all. Wayback has never held a terms page
either — checked 2026-08-15.

That is *not* the standing default in this repo. `docs/sources.md` records "permission
unestablished is not permission" — the Bundesagentur error — and on that rule this source and
the four beside it were closed on 2026-08-15. **The owner reversed that specific call on
2026-08-15 and wired them deliberately**, and the reversal is recorded in `CLAUDE.md` and
`docs/sources.md` rather than only here. Two things follow, and they are the reason this
docstring is long:

  - **This is a decision, not a precedent.** It does not license reading a source that *does*
    refuse — nothing in groups 1–4 of that file moves — and it does not license ignoring a
    robots.txt. Every fetch here still goes through `politeness.robots_allows` and `throttle`.
  - **It can be reversed by deleting one line in `search_jobs._source_classes`.** The adapter is
    kept as working, tested code either way — the `linkedin` / `jobscz` shape.

`ingestion/tests/test_unestablished_permission.py` pins the set, so adding a *sixth* source on
this reasoning is a deliberate act rather than a drift.

Shape
-----
The pages carry no `application/ld+json` — a client-side script assembles one at render time
from hidden `<div id="schema-…">` elements. Those divs are **server-rendered**, so the
structured data is readable without a browser (the constraint that closes SuccessFactors and
the Italian regional portals). We read the divs, not the script.

`schema-loc` is the reason this source is worth having: a **JSON array of countries**, e.g.
France + Germany + Italy + Netherlands on one Wikimedia posting. That is the multi-country
remote inventory `postings.reach_countries` and the EU-International row exist to surface, and
almost no other adapter here emits it. It goes into `scope_raw` **joined with `;`**, never
into `location` — `geo.resolve_location` takes the first country n-gram it finds, so folding a
country list into `location` silently re-homes the job to whichever country sorts first. The
separator is `;` and not `,` because `geo.countries_in_scope` splits on `;` and resolves each
entry independently; a comma is already spoken for *inside* one entry ("London, Ontario,
Canada").

Expect heavy overlap, and do not read the row count as new inventory
--------------------------------------------------------------------
This is a curated **re-lister** over the same startup/ATS pool already read via
Remotive/WWR/Himalayas/Jobicy/Ashby/Greenhouse/Lever — its own front page lists Alpaca,
Wikimedia, Grafana, GitLab, Dataiku, Goodnotes and Storyblok, every one of them ATS-reachable
without anyone's permission. `digest.dedupe_key` collapses a normalised (company, title, city)
so a subscriber will not get two emails, but the marginal inventory is well below the ~410
pages in the sitemap. Judge it on postings that survive dedupe, not on rows loaded.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Iterator, Optional

import requests

from ingestion import politeness
from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://remoteineurope.com/sitemap.xml"

#: Hard ceiling per run. The sitemap holds ~410 job pages; this bounds a runaway.
MAX_POSTINGS = 1500

TIMEOUT = 25

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_DIV_RE = re.compile(r'<div id="(schema-[a-z-]+)"[^>]*>(.*?)</div>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'",
}

#: Country name → ISO-3166-1 alpha-2, for the single-country case only. Deliberately small:
#: `geo` is the one definition of geography and this map exists solely to answer "does this
#: posting name exactly one country". Anything absent falls through to None, which is correct —
#: `scope_raw` still carries the names and `geo.reach_countries` reads them properly.
_ONE_COUNTRY = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "estonia": "EE",
    "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU",
    "iceland": "IS", "ireland": "IE", "italy": "IT", "latvia": "LV", "liechtenstein": "LI",
    "lithuania": "LT", "luxembourg": "LU", "malta": "MT", "netherlands": "NL",
    "norway": "NO", "poland": "PL", "portugal": "PT", "romania": "RO", "slovakia": "SK",
    "slovenia": "SI", "spain": "ES", "sweden": "SE", "switzerland": "CH",
    "united kingdom": "GB", "united states": "US", "canada": "CA",
}


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    text = _unescape(_TAG_RE.sub(" ", html))
    text = re.sub(r"[ \t]+", " ", text)
    # Every tag becomes a space, so an inline `<strong>` leaves "things ." — collapse the gap
    # before punctuation, or the matcher reads a corpus of oddly spaced sentences.
    text = re.sub(r" +([.,;:!?%)])", r"\1", text)
    text = re.sub(r"([(¡¿]) +", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fields(html: str) -> dict[str, str]:
    """The hidden `schema-*` divs, raw.

    Their contents are double-escaped (the page stores HTML as entities so a script can inject
    it), so one `_unescape` here turns `&lt;p&gt;` back into markup that `_text` can strip.
    """
    out: dict[str, str] = {}
    for name, body in _DIV_RE.findall(html):
        out[name.lower()] = _unescape(body).strip()
    return out


def _countries(raw: Optional[str]) -> list[str]:
    """`schema-loc` → the country names it lists, in order, de-duplicated."""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names


def _parse_date(value: Optional[str]) -> Optional[date]:
    """`schema-date` is a display string: "May 22, 2026"."""
    if not value:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


class RemoteInEuropeSource(BaseSource):
    """Curated EU-remote board. Multi-country scopes; no terms published (see module docstring)."""

    @property
    def source_name(self) -> str:
        return "remoteineurope"

    def _sitemap_urls(self) -> list[str]:
        if not politeness.robots_allows(SITEMAP_URL):
            logger.warning("remoteineurope: sitemap disallowed by robots.txt — stopping")
            return []
        politeness.throttle(SITEMAP_URL)
        resp = requests.get(SITEMAP_URL, headers=politeness.HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        seen: set[str] = set()
        urls: list[str] = []
        for raw in _LOC_RE.findall(resp.text):
            url = _unescape(raw)
            if "/job/" in url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _pages(self, urls: list[str]) -> Iterator[tuple[str, str]]:
        for url in urls:
            if not politeness.robots_allows(url):
                logger.debug("remoteineurope: %s disallowed by robots.txt", url)
                continue
            politeness.throttle(url)
            try:
                resp = requests.get(url, headers=politeness.HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.debug("remoteineurope: %s failed (%s)", url, exc)
                continue
            yield url, resp.text

    def fetch(self) -> list[dict]:
        try:
            urls = self._sitemap_urls()
        except requests.RequestException:
            logger.exception("remoteineurope: sitemap fetch failed")
            return []
        if not urls:
            logger.warning("remoteineurope: sitemap held no job URLs")
            return []
        if len(urls) > MAX_POSTINGS:
            logger.warning("remoteineurope: %d job URLs, capping at %d",
                           len(urls), MAX_POSTINGS)
            urls = urls[:MAX_POSTINGS]

        raw: list[dict] = []
        for url, html in self._pages(urls):
            fields = _fields(html)
            if fields.get("schema-title"):
                fields["_url"] = url
                raw.append(fields)
        logger.info("remoteineurope: %d of %d pages parsed", len(raw), len(urls))
        return raw

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        multi = 0
        for item in raw_items:
            url = item.get("_url") or item.get("schema-url")
            title = (item.get("schema-title") or "").strip()
            if not url or not title:
                continue

            countries = _countries(item.get("schema-loc"))
            if len(countries) > 1:
                multi += 1
            # One country named → we can state it. Several → `country_code` cannot hold them
            # (it holds one by construction), so it stays empty and `scope_raw` carries the
            # list for `geo.reach_countries` to read. That is the whole point of this source.
            country_code = _ONE_COUNTRY.get(countries[0].lower()) if len(countries) == 1 else None

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=title,
                    company=(item.get("schema-company") or "").strip() or None,
                    url=url,
                    description=_text(item.get("schema-desc") or "") or None,
                    # No city is ever published here, and the country list must NOT be folded
                    # in: `resolve_location` would take the first country n-gram and re-home
                    # the posting. `scope_raw` is the field for it.
                    location=None,
                    country_code=country_code,
                    remote_signal=True,  # inherently remote source
                    salary_raw=None,
                    currency=None,
                    posted_at=_parse_date(item.get("schema-date")),
                    # `;` because `geo.countries_in_scope` splits on it and resolves each entry
                    # independently — a comma is already spoken for inside one entry.
                    scope_raw="; ".join(countries) or None,
                    source_category=None,
                )
            )
        logger.info("remoteineurope: normalised %d postings (%d naming several countries)",
                    len(postings), multi)
        return postings
