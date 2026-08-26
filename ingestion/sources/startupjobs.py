"""StartupJobs — Czech & Slovak startup jobs via the public JSON-LD API. **RETIRED.**

**NOT INGESTED since 2026-08-26, and not for a technical reason.** The API moved a second
time: `core.startupjobs.cz` is gone outright (404 on `/` as well as on the collection), and
the same API Platform now answers on **`back.startupjobs.com`** — whose robots.txt is
`User-agent: *` / `Disallow: /` in full. A refusing robots is dispositive on its own in this
repo (the 2026-08-11 SmartRecruiters finding), and `politeness.robots_allows()` would refuse
every request anyway, so repointing `API_BASE` ships a source that fetches nothing and reports
a clean zero. `www.startupjobs.com` still permits everything in robots but exposes no route to
the inventory — its listing HTML holds zero `/job/` links and `sitemap/offers.xml` lists ids
without the slug that `/job/{id}` requires. The module is kept as tested code so a published
grant is a decision rather than a rewrite: see `ingestion/tests/test_source_exclusions.py`,
docs/sources.md and `notes/2026-08-26-startupjobs-retired.md`. Everything below describes the
adapter as it last worked (final successful ingest 2026-08-24).


StartupJobs.cz (now `startupjobs.com`, after the Welcome to the Jungle acquisition) moved its
public API off `startupjobs.cz/api/offers` — which now returns a clean JSON **404** — onto a
Symfony **API Platform** (Hydra / JSON-LD) at `core.startupjobs.cz/api/search/offers`. The old
call `fetch` made swallowed the 404 as an empty list: a **silent zero**. Found 2026-08-06 when
this source's `last_seen_at` in production fell a day behind every other source while nothing
errored — the failure shape this repo keeps rediscovering (measure what a source *holds*, don't
trust a plausible number).

Two long-standing debts are paid off alongside the endpoint (both flagged in the 2026-08-06 note):

- **Country is resolved from the posting, never hardcoded.** The old adapter set
  `country_code="CZ"` for *every* offer. `geo.resolve_location` treats an explicit code as
  authoritative, so that constant **overrode the Slovak offers into Czechia** — hiding the 18
  Bratislava roles from Slovak subscribers and showing them to Czech ones (the Arbeitnow
  anti-pattern exactly). The API now carries a per-offer `locations[]`, so the city name is passed
  as `location` with `country_code=None`, and the single geo resolver maps Bratislava→SK,
  Praha→CZ. `resolve_location('Bratislava', 'CZ')` returns `('CZ', None)`; `('Bratislava', None)`
  returns `('SK', 'bratislava')` — the whole fix in one line.
- **It goes through `ingestion.politeness`** — the honest `JobDigest/1.0` agent and the per-host
  throttle — which the old adapter bypassed with a bare `requests.get`. `robots.txt` on
  `startupjobs.com` and `core.startupjobs.cz` is allow-all (checked 2026-08-06).

`remote_signal` is read from the API's `locationPreference` since 2026-08-12 — `True` iff the
employer ticked `remote`, `None` otherwise, so the downstream text classifier still decides the
rest (the Teamtailor precedent). It had been left `None` entirely, which surfaced as a real
mistagged job: offer 105209 (VIVANTIS, "Data Engineer - Snowflake/dbt") says *"Spolupráce je
možná v režimu full remote"* and is remote-tagged on the board itself, but its only location is
Zlín and the borrowed phrase "full remote" is not in `_REMOTE_STRONG` ("fully remote" is), so
the posting reached subscribers as an on-site Zlín job. `locationPreference` is first-party
structured data — the employer's own tick-boxes, the same trust class as Lever's
`workplaceType` — not a scraped substring like the jobs.cz flag that caused the 2026-07-28
false-remote bug. And the hybrid-Bratislava worry that justified the conservative default is
now handled where it belongs: `geo.work_mode` checks `_describes_hybrid` *before* trusting any
source signal, so a description naming an office schedule still demotes the claim. `['hybrid']`
and `['onsite']` map to `None`, never `False` — absence of the remote tick is not proof of an
office.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

API_BASE = "https://core.startupjobs.cz"
API_URL = f"{API_BASE}/api/search/offers"

#: The link a subscriber clicks. **The slug is required** — `/job/{id}` without it is a
#: clean 404, on both domains and to a browser agent as well as ours (checked 2026-08-07 on
#: every live offer tried, and on the ids in the site's own `sitemap/offers.xml`, which are
#: listed there id-only and 404 too). The 2026-08-06 rewrite emitted the id-only form, so
#: from that day's export **every startupjobs posting carried a dead link** — 450 rows, one
#: of them emailed to a real subscriber. Nothing detected it: the API answered, the count was
#: right, the country split was right, and a URL is never fetched after it is stored.
JOB_URL = "https://www.startupjobs.com/job/{display_id}/{slug}"

#: What `posting_id` hashes — **deliberately not `JOB_URL`, and deliberately unfetchable.**
#: `posting_id = md5(url)` makes the identity of a job whatever string the adapter puts in
#: `url`, so hashing a slug that the employer can edit would mint a *new posting* every time
#: someone retitles an ad. That is not hypothetical: offer 106499's slug moved from
#: `social-media-content-creator` to `social-media-specialist` within two days, and the site
#: 302s the old one to the new. Hashing the immutable `displayId` keeps one job to one row
#: across renames — and, because it is byte-identical to what the 2026-08-06 rewrite already
#: stored, correcting the link above churns **no** ids: the existing rows keep their identity
#: and have their `url` repaired in place on the next ingest (`upsert_postings` refreshes
#: `url` for exactly this case).
ID_URL = "https://www.startupjobs.com/job/{display_id}"

HEADERS = {**politeness.HEADERS, "Accept": "application/ld+json"}

#: Runaway guard. The board is ~450 offers at 20/page today (~23 pages); 80 is far above that
#: and only fires if the API's own `view.next` chain fails to terminate.
MAX_PAGES = 80
_TIMEOUT = 20

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(html: Optional[str]) -> Optional[str]:
    """Plain text from the HTML description field."""
    if not html:
        return None
    out = _WS.sub(" ", _TAG.sub(" ", html)).strip()
    return out or None


def _localised(value: Optional[dict]) -> Optional[str]:
    """`{"cs": .., "en": ..}` -> the Czech string, falling back to English."""
    if not isinstance(value, dict):
        return value if isinstance(value, str) else None
    out = value.get("cs") or value.get("en")
    return out.strip() if isinstance(out, str) and out.strip() else None


class StartupJobsSource(BaseSource):
    """StartupJobs public JSON-LD API — Czech and Slovak startup/tech jobs."""

    @property
    def source_name(self) -> str:
        return "startupjobs"

    def fetch(self) -> list[dict]:
        offers: list[dict] = []
        url: Optional[str] = f"{API_URL}?page=1"
        pages = 0
        while url and pages < MAX_PAGES:
            try:
                politeness.throttle(url)
                resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    # Loud, not silent: a non-200 here is exactly how this source died
                    # unnoticed once already.
                    logger.warning("StartupJobs: HTTP %s at %s", resp.status_code, url)
                    break
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                logger.warning("StartupJobs page fetch failed: %s", exc)
                break
            members = data.get("member") or data.get("hydra:member") or []
            offers.extend(members)
            pages += 1
            view = data.get("view") or data.get("hydra:view") or {}
            nxt = view.get("next") or view.get("hydra:next")
            url = f"{API_BASE}{nxt}" if nxt else None
        logger.info("StartupJobs: fetched %d offers over %d pages", len(offers), pages)
        return offers

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            display_id = item.get("displayId")
            title = _localised(item.get("title"))
            if not display_id or not title:
                continue
            id_url = ID_URL.format(display_id=display_id)
            slug = (item.get("slug") or "").strip("/")
            # No slug means no working link, so fall back to the id-only form rather than
            # emitting `/job/123/` — a trailing-slash 404 reads as a broken page, while the
            # id-only form at least lands on the site's own 404. Every live offer measured
            # carries a slug; this is for the day one does not.
            url = JOB_URL.format(display_id=display_id, slug=slug) if slug else id_url
            salary_raw, currency = self._salary(item)
            postings.append(
                JobPosting(
                    # Hashes the id, not the slug — see ID_URL.
                    posting_id=make_posting_id(id_url),
                    source=self.source_name,
                    title=title,
                    company=self._company(item),
                    url=url,
                    description=_text(_localised(item.get("description"))),
                    # City text only; country is resolved downstream from it so Bratislava
                    # becomes SK rather than being overridden to CZ. Never a country constant.
                    location=self._location(item),
                    country_code=None,
                    remote_signal=self._remote_signal(item),
                    salary_raw=salary_raw,
                    currency=currency,
                    posted_at=None,
                    source_category=self._field(item),
                )
            )
        logger.info("StartupJobs: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _company(item: dict) -> Optional[str]:
        company = item.get("company")
        if isinstance(company, dict):
            return company.get("name")
        return None

    @staticmethod
    def _remote_signal(item: dict) -> Optional[bool]:
        """`True` iff the employer's own `locationPreference` tick-boxes include `remote`.

        A list, not a scalar — `['onsite', 'remote', 'hybrid']` is a real value and means
        remote is genuinely on offer, which is all `remote_signal` claims (`geo.work_mode`
        still demotes it if the description names an office schedule). Everything else is
        `None`, never `False`: `['hybrid']` says nothing about whether remote was refused,
        and a `False` here would be a claim the text classifier could not overrule.
        """
        prefs = item.get("locationPreference")
        if isinstance(prefs, list) and "remote" in prefs:
            return True
        return None

    @staticmethod
    def _location(item: dict) -> Optional[str]:
        """The first physical location's city name, or None for a location-less (remote) ad."""
        for loc in item.get("locations") or []:
            name = _localised(loc.get("name"))
            if name:
                return name
        return None

    @staticmethod
    def _field(item: dict) -> Optional[str]:
        """A category hint for the role classifier — the field's English parent slug (the
        classifier's keywords are English), e.g. "sales", falling back to Czech then the leaf."""
        for field in item.get("fields") or []:
            if not isinstance(field, dict):
                continue
            for source in (field.get("parent"), field.get("name")):
                if isinstance(source, dict):
                    name = source.get("en") or source.get("cs")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
        return None

    @staticmethod
    def _salary(item: dict) -> tuple[Optional[str], Optional[str]]:
        salary = item.get("salary")
        if not isinstance(salary, dict):
            return None, None
        lo = (salary.get("minimum") or {}).get("amount")
        hi = (salary.get("maximum") or {}).get("amount")
        cur = ((salary.get("minimum") or {}).get("currency")
               or (salary.get("maximum") or {}).get("currency"))
        unit = salary.get("unit")
        if not (lo or hi):
            return None, None
        span = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi)
        raw = f"{span} {cur or ''}/{unit or ''}".strip("/ ").strip()
        return raw or None, cur
