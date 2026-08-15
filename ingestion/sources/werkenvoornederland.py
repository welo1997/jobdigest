"""Werken voor Nederland — the Dutch central government's own vacancy site.

The fourth public-register source after `mpsv` (CZ), `platsbanken` (SE) and `nav` (NO), and
**the best-licensed of the four**. Where MPSV merely disclaims the sui generis database right,
this one is a positive public-domain dedication:

    "Tenzij anders vermeld is op de inhoud van deze website de Creative Commons zero
     verklaring (CC0) van toepassing … hergebruik van de inhoud van deze site is toegestaan …
     Bij hergebruik van de inhoud van deze website is naamsvermelding niet verplicht."

CC0 with attribution *not even required*. Only photographs are carved out, and we take none.
`robots.txt` disallows exactly one path (`/login`) and carries `Request-rate: 10/1` — an
explicit invitation to go ten times faster than `politeness`'s 1 s/host. We do not take it up;
see "Cost" below.

Why it is worth having
----------------------
Every other NL route is closed or thin: `werk.nl` (UWV) renders 110 characters of visible text
and its sitemap holds no vacancies, and the ATS adapters reach Dutch *product* companies but
not the state. This is central-government professional work — senior business analysts, legal
advisers, policy advisers, PostgreSQL platform engineers — so unlike `mpsv` it needs **no ISCO
filter**: there is no long tail of kitchen and warehouse work to dilute a widened shortlist.

It contains personal data, and we drop the block at the door
------------------------------------------------------------
This is the `mpsv` discipline and it is load-bearing here, because the measurement is worse
than the one recorded in `docs/sources.md` on 2026-08-07 ("a phone number on 3 of 6").
**Re-measured 2026-08-15 over a 20-page random sample: 15 of 20 pages (75%) carry a phone
number, 33 numbers in all**, nearly all `06` mobiles — direct lines belonging to named
individuals, typically two per page:

    "Stel gerust je vraag … Rianne van Os, Coördinator cluster informatie 06-25698769"
    "Swen van der Endt, Corporate recruiter DJI … 06-21985529"

Those are natural persons, not the employer organisation. The earlier sample also reported
"0 email addresses", which is true only of the *rendered* text: the pages carry an obfuscated
`[email protected]` placeholder that a client-side script rehydrates, so a regex over the HTML
finds nothing while a browser would show an address. Do not read that as "no emails here".

So the description is assembled from an **allowlist of content sections** and the contact
section is never read at all — `_KEEP_SECTIONS` against the page's own `<section id=…>`
anchors, with `stel_gerust_je_vraag` absent from it by construction. `_scrub` then removes
loose emails and phone numbers from what survives, because an employer can always paste a
number into a body paragraph. Both layers are deliberate: the allowlist is the guarantee, the
scrub is the backstop, and adding a *new* section id to the page cannot leak contacts into the
corpus because an unknown id is dropped rather than kept.

Ingesting that block would add a new category of personal data to `postings`, which is exactly
the change CLAUDE.md security rule 4 says cannot ship without editing the privacy policy in the
same commit. Not collecting it is cheaper and safer than collecting it and explaining it.

Structure
---------
Two requests per posting is what this costs, and there is no way around it: the sitemap gives
URLs only, and while each page carries **schema.org/JobPosting JSON-LD** with title, employer,
`datePosted`, `validThrough`, `employmentType`, a real `addressLocality`/`addressCountry` and
`baseSalary` with min/max EUR per month, that JSON-LD's own `description` is a one-line
summary ("Senior Business Analist ERP in Den Haag voor 32-38 uur bij Ministerie van Defensie").
The real description is in the page body. So: JSON-LD for the structured fields, the section
allowlist for the text, from the same single fetch.

Cost
----
~1 260 vacancies × 1 s/host ≈ 21 minutes, which fits the export window but is not free. The
`Request-rate: 10/1` header would license 2 minutes instead, and that is the argument for a
per-host exception if the export ever gets tight — but `politeness`'s 1 s guarantee is one of
the few things in this repo that is uniform across every adapter, and it is not worth
special-casing for a source that already fits. `MAX_POSTINGS` bounds a runaway sitemap; if it
ever binds, the log says so rather than the corpus silently truncating.
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

SITEMAP_URL = "https://www.werkenvoornederland.nl/sitemap-vacatures.xml"

#: Hard ceiling on postings per run. The sitemap holds ~1 260; this exists so a change at the
#: publisher's end cannot turn one run into a six-hour crawl without anyone noticing.
MAX_POSTINGS = 2500

#: Per-request timeout. The pages are ~100 KB of server-rendered HTML.
TIMEOUT = 25

#: The page's own section anchors, in reading order — an **allowlist**.
#:
#: `stel_gerust_je_vraag` ("feel free to ask") is the contact block and is deliberately absent:
#: it holds named individuals and their direct mobile numbers. This is an allowlist rather than
#: a denylist so that a section the publisher adds later is dropped by default; a denylist would
#: admit it, and the failure would be silent and personal.
_KEEP_SECTIONS = (
    "dit_ga_je_doen_anchor",
    "dit_krijg_je_anchor",
    "dit_bieden_wij_nog_meer_anchor",
    "dit_vragen_wij_anchor",
    "hier_kom_je_te_werken_anchor",
    "bijzonderheden_anchor",
    "meer_over_de_functiegroep_anchor",
)

_SECTION_RE = re.compile(
    r'<section[^>]*\bid="(?P<id>[^"]+)"[^>]*>(?P<body>.*?)</section>', re.I | re.S
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
#: Dutch numbers as they actually appear here: `06-25698769`, `06 15196834`, `0646991700`,
#: `070-4266323`, `+31 6 51450753`, `+31618306523`.
_PHONE = re.compile(
    r"(?<!\d)(?:\+31[\s-]?\(?0?\)?[\s-]?|0)\d{1,3}[\s-]?\d{2,4}[\s-]?\d{2,6}(?!\d)"
)

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&euro;": "€",
}


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _text(html: str) -> str:
    """Strip tags to readable text, keeping paragraph breaks."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    text = _unescape(_TAG_RE.sub(" ", html))
    text = _WS_RE.sub(" ", text)
    # Every tag becomes a space, so an inline `<strong>` leaves "kwaliteit ." — collapse the
    # gap before punctuation, or the matcher reads a corpus of oddly spaced sentences.
    text = re.sub(r" +([.,;:!?%)])", r"\1", text)
    text = re.sub(r"([(¡¿]) +", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove direct contact details from free text.

    The contact *section* is never read (see `_KEEP_SECTIONS`); this is the backstop for the
    other half, where someone has typed a mobile number into a body paragraph. Replaced with a
    marker rather than deleted so the surrounding sentence still reads as Dutch.
    """
    if not text:
        return None
    text = _EMAIL.sub("[contact verwijderd]", text)
    text = _PHONE.sub("[contact verwijderd]", text)
    return text.strip() or None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _job_ld(html: str) -> Optional[dict]:
    """The page's schema.org/JobPosting block, if it has one.

    Tolerates a `@graph` wrapper and a list at the top level — neither shape is used today, and
    both are cheap to survive if the publisher's templating changes.
    """
    for block in _LD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            candidates = data["@graph"]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _description(html: str) -> Optional[str]:
    """Assemble the description from the content sections only.

    An allowlist, so an unrecognised section id is dropped. The contact block is the reason.
    """
    found = {m.group("id"): m.group("body") for m in _SECTION_RE.finditer(html)}
    parts = [_text(found[key]) for key in _KEEP_SECTIONS if key in found]
    joined = "\n\n".join(part for part in parts if part)
    return joined or None


def _salary(node: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """`baseSalary` → (`salary_raw`, `currency`)."""
    if not isinstance(node, dict):
        return None, None
    currency = node.get("currency") or node.get("salaryCurrency")
    value = node.get("value")
    if not isinstance(value, dict):
        return None, currency if isinstance(currency, str) else None
    low, high = value.get("minValue"), value.get("maxValue")
    unit = value.get("unitText")
    if low is None and high is None:
        return None, currency
    if low is not None and high is not None and low != high:
        raw = f"{low}-{high}"
    else:
        raw = str(low if low is not None else high)
    if isinstance(unit, str) and unit:
        raw = f"{raw} per {unit.lower()}"
    return raw, currency if isinstance(currency, str) else None


def _location(node) -> tuple[Optional[str], Optional[str]]:
    """`jobLocation` → (`location`, `country_code`).

    Takes the first place with an address. `resolve_location` picks one country anyway, and
    `geo.reach_countries` is what carries the rest — this adapter must not try to do that job.
    """
    places = node if isinstance(node, list) else [node]
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address")
        if not isinstance(address, dict):
            continue
        city = address.get("addressLocality")
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        city = city.strip() if isinstance(city, str) else None
        country = country.strip().upper() if isinstance(country, str) else None
        if city or country:
            return city or None, (country if country and len(country) == 2 else None)
    return None, None


class WerkenVoorNederlandSource(BaseSource):
    """Dutch central-government vacancies, CC0, read from the publisher's own sitemap."""

    @property
    def source_name(self) -> str:
        return "werkenvoornederland"

    # -- fetch ---------------------------------------------------------------------------

    def _sitemap_urls(self) -> list[str]:
        if not politeness.robots_allows(SITEMAP_URL):
            logger.warning("werkenvoornederland: sitemap disallowed by robots.txt — stopping")
            return []
        politeness.throttle(SITEMAP_URL)
        resp = requests.get(SITEMAP_URL, headers=politeness.HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        seen: set[str] = set()
        urls: list[str] = []
        for raw in _LOC_RE.findall(resp.text):
            url = _unescape(raw)
            if "/vacatures/" in url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _pages(self, urls: list[str]) -> Iterator[tuple[str, str]]:
        for url in urls:
            if not politeness.robots_allows(url):
                logger.debug("werkenvoornederland: %s disallowed by robots.txt", url)
                continue
            politeness.throttle(url)
            try:
                resp = requests.get(url, headers=politeness.HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.debug("werkenvoornederland: %s failed (%s)", url, exc)
                continue
            yield url, resp.text

    def fetch(self) -> list[dict]:
        try:
            urls = self._sitemap_urls()
        except requests.RequestException:
            logger.exception("werkenvoornederland: sitemap fetch failed")
            return []
        if not urls:
            logger.warning("werkenvoornederland: sitemap held no vacancy URLs")
            return []
        if len(urls) > MAX_POSTINGS:
            logger.warning(
                "werkenvoornederland: sitemap held %d vacancies, capping at %d — "
                "re-measure the export window before raising MAX_POSTINGS",
                len(urls), MAX_POSTINGS,
            )
            urls = urls[:MAX_POSTINGS]

        raw: list[dict] = []
        for url, html in self._pages(urls):
            posting = _job_ld(html)
            if posting is None:
                continue
            raw.append({"url": url, "ld": posting, "description": _description(html)})
        logger.info("werkenvoornederland: %d of %d pages parsed", len(raw), len(urls))
        return raw

    # -- normalize -----------------------------------------------------------------------

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("url")
            posting = item.get("ld") or {}
            title = posting.get("title")
            if not url or not isinstance(title, str) or not title.strip():
                continue

            org = posting.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else None
            company = company.strip() if isinstance(company, str) else None

            location, country = _location(posting.get("jobLocation"))
            salary_raw, currency = _salary(posting.get("baseSalary"))

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=title.strip(),
                    company=company or None,
                    url=url,
                    description=_scrub(item.get("description")),
                    location=location,
                    # The publisher is the Dutch state and every vacancy is a Dutch government
                    # post, but the country still comes from the posting's own
                    # `addressCountry` — a source-level constant is almost always wrong, and
                    # this one has no reason to be the exception.
                    country_code=country,
                    # Nothing on these pages states a remote policy, and `remote_signal` is a
                    # claim a source has to actually make. `is_fully_remote` reads the words.
                    remote_signal=None,
                    salary_raw=salary_raw,
                    currency=currency,
                    posted_at=_parse_date(posting.get("datePosted")),
                    # Central government hires across many disciplines and the site publishes
                    # no category field we could map into `taxonomy.CATEGORIES`. A raw string
                    # is not a hint — `classify` discards it — so None is the honest answer.
                    source_category=None,
                )
            )
        return postings
