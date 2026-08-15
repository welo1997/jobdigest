"""Golden Careers (Cyprus) — a recruitment agency's own vacancy board.

Permission
----------
**No terms of use and no privacy policy on the host.** `robots.txt` is a Yoast block with a
bare `Disallow:` — allow-all — and it advertises `sitemap_index.xml`, which carries
`post-type-job-sitemap.xml`. Nothing permits and nothing refuses.

That is "permission unestablished", historically an automatic skip here. **The owner reversed
it for a named set on 2026-08-15**; this is a member. See
`ingestion/tests/test_unestablished_permission.py`, which enumerates the set, and
`docs/sources.md`. It does not reach any source that *refuses*, and every request still goes
through `politeness.robots_allows` and `throttle`.

Note the contrast with **`cyprusjobs.com`**, the other Cypriot board looked at the same day and
deliberately NOT built: it serves a Cloudflare Content-Signal block reserving Art. 4 EU DSM TDM
rights, which is a machine-readable *reservation* rather than silence — a different class, and
one the 2026-08-15 decision does not cover.

What it actually holds, and the honest caveat
---------------------------------------------
**63 sitemap entries, ~62 real vacancies.** Small even by this file's standards, and Cyprus was
already ATS-only, so this is the first non-ATS Cypriot inventory in the stack.

**The employer is anonymised.** Golden Careers is an agency and its ads read *"Our client is a
well established company in Cyprus…"*, so `company` is the **agency**, not the hirer. Two
consequences worth knowing before trusting a count:

  - `digest.dedupe_key` normalises `(company, title, city)`. If the same role is also on the
    employer's own ATS board under the employer's real name, the keys differ and **dedupe will
    not collapse them** — a subscriber could see both. That is the standing "one employer on
    two registers" risk in CLAUDE.md, here by construction rather than by accident.
  - A subscriber cannot tell who they would be working for until they apply. That is a property
    of agency listings, not a bug to fix here.

There is no JSON-LD, so the fields come off stable server-rendered markup: `<h1>`, a
`job-category` span, a `job-location` span, and `job-section` blocks. That is a bespoke parser
and it will break at a redesign — the failure would be a silent zero, which `source_watchdog`
reports as SILENT ZERO. Small enough that the cost of it breaking is small too.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

import requests

from ingestion import politeness
from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://www.goldencareers.com.cy/post-type-job-sitemap.xml"

#: The agency is the publisher and the named party on every ad; the hiring employer is not
#: disclosed. See the module docstring on what that does to `dedupe_key`.
AGENCY = "Golden Careers Recruitment"

MAX_POSTINGS = 500
TIMEOUT = 25

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_CATEGORY_RE = re.compile(r'<span class="job-category"[^>]*>(.*?)</span>', re.I | re.S)
_LOCATION_RE = re.compile(r'<span class="job-location"[^>]*>(.*?)</span>', re.I | re.S)
_SECTION_RE = re.compile(
    r'<h4 class="job-section-title"[^>]*>(.*?)</h4>\s*'
    r'<div class="job-section-content"[^>]*>(.*?)</div>',
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&#8217;": "’", "&#8216;": "‘",
    "&#8220;": "“", "&#8221;": "”", "&#8211;": "–", "&#8212;": "—",
}

#: `job-category` → `role_category`. Must be a real `taxonomy.CATEGORIES` member or None —
#: `classify` discards anything else, and a title pattern still wins over this hint.
CATEGORY_HINTS = {
    "information technology": "software_engineering",
    "marketing": "marketing",
    "human resources": "hr_recruiting",
    "accounting": "finance_accounting",
    "legal": "legal",
    "sales": "sales",
}


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _text(html: Optional[str]) -> str:
    if not html:
        return ""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    text = _unescape(_TAG_RE.sub(" ", html))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?%)])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _description(html: str) -> Optional[str]:
    """Join the `job-section` blocks, keeping their headings.

    Everything on these pages is the ad itself — there is no contact block, unlike `mpsv` and
    `nva`. Verified across the sampled pages; the agency's own address sits in the site footer,
    outside `job-section`, and is therefore never read.
    """
    parts: list[str] = []
    for heading, body in _SECTION_RE.findall(html):
        title = _text(heading)
        content = _text(body)
        if content:
            parts.append(f"{title}\n{content}" if title else content)
    return "\n\n".join(parts) or None


class GoldenCareersSource(BaseSource):
    """Cypriot recruitment-agency board; no terms published (see module docstring)."""

    @property
    def source_name(self) -> str:
        return "goldencareers"

    def _sitemap_urls(self) -> list[str]:
        if not politeness.robots_allows(SITEMAP_URL):
            logger.warning("goldencareers: sitemap disallowed by robots.txt — stopping")
            return []
        politeness.throttle(SITEMAP_URL)
        resp = requests.get(SITEMAP_URL, headers=politeness.HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        seen: set[str] = set()
        urls: list[str] = []
        for raw in _LOC_RE.findall(resp.text):
            url = _unescape(raw)
            # `/jobs/` alone is the index page, not a vacancy.
            if "/jobs/" in url and not url.rstrip("/").endswith("/jobs") and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _pages(self, urls: list[str]) -> Iterator[tuple[str, str]]:
        for url in urls:
            if not politeness.robots_allows(url):
                logger.debug("goldencareers: %s disallowed by robots.txt", url)
                continue
            politeness.throttle(url)
            try:
                resp = requests.get(url, headers=politeness.HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.debug("goldencareers: %s failed (%s)", url, exc)
                continue
            yield url, resp.text

    def fetch(self) -> list[dict]:
        try:
            urls = self._sitemap_urls()
        except requests.RequestException:
            logger.exception("goldencareers: sitemap fetch failed")
            return []
        if not urls:
            logger.warning("goldencareers: sitemap held no vacancy URLs")
            return []
        if len(urls) > MAX_POSTINGS:
            logger.warning("goldencareers: %d URLs, capping at %d", len(urls), MAX_POSTINGS)
            urls = urls[:MAX_POSTINGS]

        raw: list[dict] = []
        for url, html in self._pages(urls):
            title_match = _H1_RE.search(html)
            if not title_match:
                continue
            raw.append({
                "url": url,
                "title": _text(title_match.group(1)),
                "category": _text(_CATEGORY_RE.search(html).group(1))
                if _CATEGORY_RE.search(html) else "",
                "location": _text(_LOCATION_RE.search(html).group(1))
                if _LOCATION_RE.search(html) else "",
                "description": _description(html),
            })
        logger.info("goldencareers: %d of %d pages parsed", len(raw), len(urls))
        return raw

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url, title = item.get("url"), (item.get("title") or "").strip()
            if not url or not title:
                continue
            category = (item.get("category") or "").strip()
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=title,
                    # The agency, not the hirer — the ads say "our client". See the docstring
                    # on what this does to `dedupe_key`.
                    company=AGENCY,
                    url=url,
                    description=item.get("description"),
                    location=(item.get("location") or "").strip() or None,
                    # Every vacancy on this board is in Cyprus and the agency is Cypriot, but
                    # the city still comes from the page and `geo.resolve_location` does the
                    # work; the constant is only the country, which the board's own scope
                    # settles — the `mpsv` case, not the Arbeitnow one.
                    country_code="CY",
                    remote_signal=None,
                    salary_raw=None,
                    currency=None,
                    # The board publishes no date, anywhere on the page or in the sitemap's
                    # `lastmod`-per-URL. Null is the honest answer; `first_seen_at` carries
                    # freshness for this source.
                    posted_at=None,
                    source_category=CATEGORY_HINTS.get(category.lower()),
                )
            )
        return postings
