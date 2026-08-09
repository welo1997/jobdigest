"""Platsbanken — the Swedish public employment service's vacancy register, as open data.

`https://jobsearch.api.jobtechdev.se/search` is the JobSearch API published by **JobTech
Dev**, a unit of Arbetsförmedlingen (the Swedish Public Employment Service). It serves the
ads on Platsbanken, Sweden's national job bank. This is the MPSV pattern in a second country:
a public body publishing its own register for reuse, rather than a board we hope is fine.

**Why this one is permitted where Germany's is not.** Arbetsförmedlingen states its open data
and open APIs are "free for anyone to use", publishes them under the `data.jobtechdev.se`
open-data portal, requires no key, and serves no `robots.txt` (404, i.e. allow-all). Compare
the Bundesagentur für Arbeit, whose Jobbörse holds 820 599 vacancies and whose terms of use
§2a(3) forbid exactly this: *"use robots, web spiders or similar technologies, or to use
existing communication or programming interfaces contrary to the BA's intended purpose, and
thus to read out content from the portal or apps for the purpose of data collection and
evaluation"*. Germany is the largest single source in Europe and it is **excluded on the same
grounds as Jobs.cz** — see the README. Do not add it because the digest looks thin.

**It carries personal data, and the adapter is built not to take it.** Every ad may name a
recruiter: `application_contacts` is populated on 29% of them, and `employer` has its own
`email` and `phone_number`. **None of those fields is ever read**, and `_scrub` strips loose
emails and Swedish phone numbers out of the free text as well, because employers paste them
there. Storing either would add a personal-data category the privacy policy does not cover
(security rule 4) — the same decision, for the same reason, as `mpsv.prvniKontaktSe-
Zamestnavatelem`.

Three things about the API shape drive the rest:

**`limit` caps at 100 and `offset` at 2 000**, so any single query reaches at most 2 100 rows
— measured, because a 400 at offset 2 100 is the only way the ceiling announces itself. Five
of the seven occupation fields hold more than that, so a plain query per field would silently
truncate the largest ones.

The way past it is **keyset paging on publication date**, not a second filter. `sort` accepts
`pubdate-desc` and `published-before` narrows by timestamp, so each field is read newest
first until the ceiling, then re-opened with `published-before` set to the oldest ad seen.
Two alternatives were measured and rejected:

* Slicing by **occupation group** looks natural and is wrong twice over. Sending
  `occupation-field` and `occupation-group` together does not narrow at all — the API treats
  two filters of the same family as OR, so the pair returns the whole field (2 710 for
  Data/IT against 1 183 for the group alone) and every slice re-fetches the same field. Even
  with the group sent alone, `stats.limit` hard-caps at 30, and four of these fields have
  more than 30 groups, so ~300 ads sat in groups the facet would not name. That run captured
  15 440 of 15 736.
* Slicing by **region** does AND correctly (different filter family), but regions are not a
  complete partition: 152 ads in the sales field alone carry no region at all, being remote
  or abroad — and those are exactly the non-Swedish ones worth having.

Keyset paging has neither problem, needs no facet call, and costs fewer requests than either.
A window that returns no ad not already seen ends the field, which is what stops ties on a
single timestamp from looping forever.

**Scope is SSYK occupation field, the same call MPSV's ISCO 1–3 filter makes.** The register
is the whole Swedish labour market — 36 992 ads, the largest single field being healthcare
(5 012), then hotel and restaurant, transport and warehousing, construction. Loading all of
it would put manual and care work into a corpus whose matcher knows nine mostly-technical
categories, and `query_shortlist_meta`'s widened path drops the recall predicate entirely, so
anyone tripping `SHORTLIST_FLOOR` would get a shortlist of jobs they never asked about. The
seven fields kept are the managers-professionals-technicians analogue: Data/IT, technical,
administration/economics/law, sales/purchasing/marketing, managers, culture/media/design and
natural science — **15 736 of 36 992**.

**Country comes from the posting and is written in Swedish.** `workplace_address.country` is
`"Sverige"` for almost everything but not all — `"Schweiz"` turned up in a 100-ad sample — so
a source-level `country_code="SE"` would be the Arbetsnow mistake, and the ISO code is not in
the payload either (`country_code` is Arbetsförmedlingen's own `"199"`). Hence `_COUNTRIES`,
which maps the Swedish name; anything unrecognised resolves to `None` and is kept, per the
repo's "unknown country is kept" rule.

There is **no remote field anywhere in the payload**, so `remote_signal` is never set from
this source. That is deliberate: the flag exempts a posting from the location gate, and
inventing it from Swedish prose is exactly the substring-mining that produced the 2026-07-28
false-remote incident.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = {**politeness.HEADERS, "Accept": "application/json"}

BASE_URL = "https://jobsearch.api.jobtechdev.se/search"

#: SSYK occupation fields to ingest, by Arbetsförmedlingen concept id. Counts measured
#: 2026-08-09 from the `occupation-field` facet (the whole register is 40 367 ads).
#:
#: **Broadened 2026-08-09 from 7 to 15 fields.** The original 7 were the
#: managers-professionals-technicians analogue, chosen when the taxonomy knew nine
#: mostly-technical categories and could not rank care or manual work. The 2026-08-09 taxonomy
#: broadening added healthcare, education, hospitality, skilled_trades, construction,
#: logistics_transport and manufacturing_production, and `SSYK_FIELD_CATEGORIES` already maps
#: every field to one of them — so eight more fields are now *rankable* and are added here.
#:
#: **Six fields are still excluded, and the reason is the same invariant as before.** They map
#: to no current category (`social work`, `sanitation`, `security`, `agriculture`, `beauty`,
#: `military`), so ingesting them would land ~6 100 ads in `uncategorised` — and the widened
#: shortlist path drops the recall predicate under `SHORTLIST_FLOOR`, so a subscriber tripping
#: it would be shown jobs no category could have matched. Adding rankable inventory is the win;
#: adding unrankable inventory to the widened path is the harm the first exclusion guarded
#: against. Do not add these without a category to rank them (this repo's "no category without
#: inventory, no inventory without a category" rule).
OCCUPATION_FIELDS: list[tuple[str, str]] = [
    # The original seven (managers / professionals / technicians).
    ("RPTn_bxG_ExZ", "Försäljning, inköp, marknadsföring"),   # 3 747
    ("X82t_awd_Qyc", "Administration, ekonomi, juridik"),     # 3 747
    ("apaJ_2ja_LuF", "Data/IT"),                              # 2 704
    ("6Hq3_tKo_V57", "Yrken med teknisk inriktning"),         # 2 495
    ("bh3H_Y3h_5eD", "Chefer och verksamhetsledare"),         # 2 488
    ("9puE_nYg_crq", "Kultur, media, design"),                #   388
    ("kJeN_wmw_9wX", "Naturvetenskap"),                       #   358
    # Added 2026-08-09 — now rankable by the broadened taxonomy (label → SSYK_FIELD_CATEGORIES).
    ("NYW6_mP6_vwf", "Hälso- och sjukvård"),                  # 5 406  -> healthcare
    ("ASGV_zcE_bWf", "Transport, distribution, lager"),       # 4 003  -> logistics_transport
    ("MVqp_eS8_kDZ", "Pedagogik"),                            # 2 462  -> education
    ("ScKy_FHB_7wT", "Hotell, restaurang, storhushåll"),      # 2 355  -> hospitality
    ("yhCP_AqT_tns", "Installation, drift, underhåll"),       # 1 906  -> skilled_trades
    ("wTEr_CBC_bqh", "Industriell tillverkning"),             # 1 877  -> manufacturing_production
    ("j7Cq_ZJe_GkT", "Bygg och anläggning"),                  # 1 873  -> construction
    ("PaxQ_o1G_wWH", "Hantverk"),                             #   180  -> skilled_trades
]

#: The API's own maximum. Asking for more is a 400, not a silent truncation — but page by the
#: count received anyway, because that is the rule that survives the API changing its mind.
#: SSYK occupation *field* → `role_category`, for fields that mean exactly one thing.
#: Added 2026-08-09. Until then `source_category` carried `occupation.label` — the finest SSYK
#: leaf, 954 distinct Swedish strings — which `taxonomy.classify` discards because it is not a
#: canonical category (the 2026-08-08 hint guard). So the register's own answer was fetched
#: every run and thrown away, and 74% of this source sat in `uncategorised`.
#:
#: **Deliberately covers all 21 fields, not the 7 in `OCCUPATION_FIELDS`.** The exclusion of
#: healthcare, pedagogy, restaurant, transport, construction and manufacturing was made when
#: the taxonomy had no category for them; it now does. Mapping them here costs nothing and
#: means no second change is needed if that exclusion is lifted.
SSYK_FIELD_CATEGORIES: dict[str, str] = {
    # The whole technical field is engineering (mechanical / electrical / civil / process),
    # mapped 2026-08-09 when the `engineering` category was added. 2 495 ads — a title still
    # wins, so a software-adjacent title inside it is unaffected.
    "Yrken med teknisk inriktning": "engineering",
    "Hälso- och sjukvård": "healthcare",
    "Pedagogik": "education",
    "Pedagogiskt arbete": "education",
    "Hotell, restaurang, storhushåll": "hospitality",
    "Industriell tillverkning": "manufacturing_production",
    "Transport, distribution, lager": "logistics_transport",
    "Bygg och anläggning": "construction",
    "Installation, drift, underhåll": "skilled_trades",
    "Hantverk": "skilled_trades",
    "Data/IT": "software_engineering",
}

#: SSYK occupation *group* → category, inside fields that span several categories. Checked
#: first, so "Data/IT" resolves to support or platform work rather than the field's default.
SSYK_GROUP_CATEGORIES: dict[str, str] = {
    "Supporttekniker, IT": "customer_support",
    "Systemförvaltare m.fl.": "devops_platform",
    "Nätverks- och systemtekniker m.fl.": "devops_platform",
    "Systemadministratörer": "devops_platform",
    "Drifttekniker, IT": "devops_platform",
    "Företagssäljare": "sales",
    "Butikssäljare, fackhandel": "sales",
    "Butikssäljare, dagligvaror": "sales",
    "Telefonförsäljare m.fl.": "sales",
    "Torg- och marknadsförsäljare m.fl.": "sales",
    "Eventsäljare och butiksdemonstratörer m.fl.": "sales",
    "Säljande butikschefer och avdelningschefer i butik": "sales",
    "Inköpare och upphandlare": "operations",
    "Marknadsanalytiker och marknadsförare m.fl.": "marketing",
    "Informatörer, kommunikatörer och PR-specialister": "marketing",
    "Kundtjänstpersonal": "customer_support",
    "Personal- och HR-specialister": "hr_recruiting",
    "Redovisningsekonomer": "finance_accounting",
    "Löne- och personaladministratörer": "finance_accounting",
    "Revisorer m.fl.": "finance_accounting",
    "Controller": "finance_accounting",
    "Jurister m.fl.": "legal",
    "Grafiska formgivare m.fl.": "design",
    "Restaurang- och kökschefer": "hospitality",
    "Produktionschefer inom tillverkning": "manufacturing_production",
    "Driftchefer inom bygg, anläggning och gruva": "construction",
}

#: Groups whose field default is *wrong* and for which no category is right either — the hint
#: declines rather than guessing. "Yrken med teknisk inriktning" maps to `engineering` as a
#: field, but it also carries property managers and urban planners, and hinting those as
#: engineering puts a `Fastighetsförvaltare` in the digest of everyone who asked for mechanical
#: work. The rule is the taxonomy's own: a lookup in front of the matcher must be able to say
#: "I don't know" and hand off, never name a category that is not true. Mirrored by
#: `scripts/categorization_score.OUT_OF_SCOPE_GROUPS`, which excludes them from the answer key
#: for the same reason.
SSYK_GROUP_UNMAPPED: frozenset[str] = frozenset({
    "Fastighetsförvaltare", "Planeringsarkitekter m.fl.", "Arkitekter m.fl.",
})


def _ssyk_category(ad: dict) -> Optional[str]:
    """The register's own occupation classification, as a `role_category`.

    Group before field: "Data/IT" defaults to software, but an IT support technician inside
    it is `customer_support`, and filing every one of them as a developer would put them in
    the wrong subscriber's digest.

    A group may also *veto* its field's answer without offering one (`SSYK_GROUP_UNMAPPED`) —
    a property manager inside the technical field is not an engineer, and "no hint" is the
    honest answer where "the field's category" would be a wrong one.
    """
    group = ((ad.get("occupation_group") or {}).get("label") or "").strip()
    if group in SSYK_GROUP_CATEGORIES:
        return SSYK_GROUP_CATEGORIES[group]
    if group in SSYK_GROUP_UNMAPPED:
        return None
    field = ((ad.get("occupation_field") or {}).get("label") or "").strip()
    return SSYK_FIELD_CATEGORIES.get(field)


PAGE_SIZE = 100

#: `offset` may not exceed 2 000, so one query reaches 2 100 rows at most. Slices are sized to
#: stay under it; this constant exists so `fetch` can say when one no longer does.
MAX_OFFSET = 2000
QUERY_CEILING = MAX_OFFSET + PAGE_SIZE

#: Newest first, so `published-before` can walk backwards through a field.
SORT_NEWEST = "pubdate-desc"

#: Pages inside one date window. `offset` alone is not a bound on request count: a server
#: answering with one hit per page would advance the offset by one each time and issue 2 000
#: requests before the window ever ended. Hitting this cap is treated exactly like hitting the
#: offset ceiling — the window re-opens from the oldest ad seen — so it bounds the work
#: without dropping the tail.
MAX_PAGES_PER_WINDOW = MAX_OFFSET // PAGE_SIZE + 1

#: Date windows per field before giving up. Each window yields up to 2 100 ads, so this is
#: ~21 000 for one field — far above the largest here (3 750) and low enough that a paging
#: bug ends the run instead of hammering the API.
MAX_WINDOWS = 10

_TIMEOUT = 40

#: Slices in flight. The per-host throttle still spaces every request a second apart, so
#: this overlaps network latency rather than raising the request rate.
_MAX_WORKERS = 8

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
#: Swedish numbers as employers actually write them. Matched by *digit count* rather than by
#: fixed groups, because the grouping is not fixed: `+46701234567`, `+46 70 123 45 67`,
#: `070-123 45 67` and the Stockholm landline `08-123 456 78` all differ, and a
#: three-group pattern that covers the mobile forms silently misses the landline.
#: Over-matching is the safe direction here — a false positive only redacts.
_PHONE = re.compile(r"(?:\+46[\s-]?|\b0)(?:\d[\s-]?){6,11}\d\b")
_WS = re.compile(r"[ \t\r\f\v]+")

#: Swedish country names → ISO 3166-1 alpha-2. `workplace_address.country` is a Swedish
#: display name and the payload's own `country_code` is Arbetsförmedlingen's internal number
#: ("199" for Sweden), so this is the only route to an ISO code. Unlisted → None, and unknown
#: is kept rather than guessed.
_COUNTRIES: dict[str, str] = {
    "sverige": "SE", "norge": "NO", "danmark": "DK", "finland": "FI", "island": "IS",
    "tyskland": "DE", "frankrike": "FR", "spanien": "ES", "italien": "IT",
    "nederländerna": "NL", "holland": "NL", "belgien": "BE", "luxemburg": "LU",
    "polen": "PL", "estland": "EE", "lettland": "LV", "litauen": "LT",
    "tjeckien": "CZ", "slovakien": "SK", "ungern": "HU", "rumänien": "RO",
    "bulgarien": "BG", "grekland": "GR", "portugal": "PT", "kroatien": "HR",
    "slovenien": "SI", "malta": "MT", "cypern": "CY", "irland": "IE", "österrike": "AT",
    "schweiz": "CH", "storbritannien": "GB", "usa": "US", "kanada": "CA",
    "indien": "IN", "kina": "CN", "japan": "JP", "australien": "AU",
}


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove direct contact details from free text.

    The structured contact blocks are simply never read; this covers the other half, where an
    employer has typed a recruiter's mobile or address into the description. Replaced with a
    marker rather than deleted, so the surrounding sentence still reads as Swedish.
    """
    if not text:
        return None
    text = _EMAIL.sub("[kontaktuppgift borttagen]", text)
    text = _PHONE.sub("[kontaktuppgift borttagen]", text)
    # Newlines are meaningful here — these are long ads (median 4 634 characters) and the
    # paragraph breaks are what make them readable — so only horizontal runs are collapsed.
    return _WS.sub(" ", text).strip() or None


def _country(address: dict) -> Optional[str]:
    name = (address.get("country") or "").strip().lower()
    return _COUNTRIES.get(name)


def _location(address: dict) -> Optional[str]:
    """City and region as one human string, skipping the parts the ad left blank."""
    parts = [address.get("city") or address.get("municipality"), address.get("region")]
    seen: list[str] = []
    for part in parts:
        part = (part or "").strip()
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


def _posted(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None


class PlatsbankenSource(BaseSource):
    """Swedish Platsbanken ads via Arbetsförmedlingen's open JobSearch API."""

    def __init__(self, fields: Optional[list[tuple[str, str]]] = None):
        self._fields = fields if fields is not None else OCCUPATION_FIELDS

    @property
    def source_name(self) -> str:
        return "platsbanken"

    # ------------------------------------------------------------------ fetch --
    def _get(self, params: dict) -> Optional[dict]:
        try:
            politeness.throttle(BASE_URL)
            resp = requests.get(BASE_URL, headers=HEADERS, timeout=_TIMEOUT, params=params)
            if resp.status_code != 200:
                logger.warning("Platsbanken %s: HTTP %s", params, resp.status_code)
                return None
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Platsbanken %s failed: %s", params, exc)
            return None

    def _field(self, field_id: str, label: str) -> list[dict]:
        """Every ad in one occupation field, newest first, past the offset ceiling.

        Reads the field in date-descending windows. Each window pages until it either runs
        out of ads or hits `MAX_OFFSET`; if it hit the ceiling, the next window re-opens the
        same field with `published-before` set to the oldest ad seen so far.
        """
        found: dict[str, dict] = {}
        before: Optional[str] = None
        windows = 0
        while windows < MAX_WINDOWS:
            windows += 1
            offset = 0
            oldest: Optional[str] = None
            new_in_window = 0
            hit_ceiling = False
            pages = 0
            while offset <= MAX_OFFSET:
                if pages >= MAX_PAGES_PER_WINDOW:
                    hit_ceiling = True
                    break
                pages += 1
                params: dict = {"limit": PAGE_SIZE, "offset": offset,
                                "occupation-field": field_id, "sort": SORT_NEWEST}
                if before:
                    params["published-before"] = before
                data = self._get(params)
                if not data:
                    return list(found.values())
                hits = data.get("hits") or []
                if not hits:
                    break
                total = ((data.get("total") or {}).get("value")) or 0
                for hit in hits:
                    ad_id = hit.get("id")
                    if not ad_id:
                        continue
                    if str(ad_id) not in found:
                        new_in_window += 1
                    found[str(ad_id)] = hit
                    published = hit.get("publication_date")
                    if published and (oldest is None or published < oldest):
                        oldest = published
                # Stride by what arrived, not by PAGE_SIZE.
                offset += len(hits)
                if offset >= total:
                    break
                if offset > MAX_OFFSET:
                    hit_ceiling = True
            if not hit_ceiling and offset <= MAX_OFFSET:
                break
            if not oldest or not new_in_window:
                # A window that surfaced nothing new cannot make progress — this is what
                # stops several ads sharing one timestamp from looping forever.
                break
            before = oldest
        else:
            logger.warning("Platsbanken %s: stopped at %d date windows with %d ads; the "
                           "oldest tail may be unread", label, MAX_WINDOWS, len(found))
        logger.info("Platsbanken %s: %d ads in %d window(s)", label, len(found), windows)
        return list(found.values())

    def fetch(self) -> list[dict]:
        # `politeness.throttle` spaces every request a second apart on a single host, and
        # this is ~160 pages, so run sequentially it is dominated by round-trips rather than
        # by the throttle. Pooling the seven fields lets that latency overlap. Paging stays
        # sequential *within* a field, because each page decides whether there is another and
        # each window depends on the one before. Same shape as Workday's list stage.
        found: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            for hits in pool.map(lambda f: self._field(*f), self._fields):
                for hit in hits:
                    # Windows overlap on their boundary timestamp by design; keyed, so a
                    # repeat cannot become two rows.
                    found.setdefault(str(hit["id"]), hit)
        logger.info("Platsbanken: %d unique ads across %d fields", len(found), len(self._fields))
        return list(found.values())

    # -------------------------------------------------------------- normalize --
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        removed = 0
        for ad in raw_items:
            url = ad.get("webpage_url")
            title = (ad.get("headline") or "").strip()
            if not url or not title:
                continue
            if ad.get("removed"):
                # The search API should not serve these, but a withdrawn ad reaching a
                # subscriber's inbox is worse than a smaller number.
                removed += 1
                continue
            address = ad.get("workplace_address") or {}
            employer = ad.get("employer") or {}
            occupation = ad.get("occupation") or {}
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                # `name` is the legal entity, `workplace` the trading name; prefer the one a
                # subscriber would recognise. Never `employer.email` / `phone_number`.
                company=(employer.get("name") or employer.get("workplace") or "").strip() or None,
                url=url,
                description=_scrub((ad.get("description") or {}).get("text")),
                location=_location(address),
                country_code=_country(address),
                # No remote field exists in this payload. Never claim one — `remote_signal`
                # skips the location gate, and guessing it from prose is the 2026-07-28 bug.
                remote_signal=None,
                salary_raw=(ad.get("salary_description") or "").strip() or None,
                currency=None,
                posted_at=_posted(ad.get("publication_date")),
                # The register's own classification, mapped. `classify` prefers
                # title patterns and falls back to this.
                source_category=_ssyk_category(ad),
            ))
        if removed:
            logger.info("Platsbanken: skipped %d ads flagged removed", removed)
        return out
