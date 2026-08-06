"""StartupJobs — Czech & Slovak startup jobs via the public JSON-LD API.

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

`remote_signal` is left `None` and the downstream text `work_mode` classifier decides — the
Teamtailor precedent. The API's `locationPreference` (`remote`/`hybrid`/`onsite`) is a structured
signal that could set it later, but a hybrid Bratislava role must stay gated to Bratislava, so the
conservative default avoids re-opening the 2026-07-28 false-remote class of bug.
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
JOB_URL = "https://www.startupjobs.com/job/{display_id}"
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
            url = JOB_URL.format(display_id=display_id)
            salary_raw, currency = self._salary(item)
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=title,
                    company=self._company(item),
                    url=url,
                    description=_text(_localised(item.get("description"))),
                    # City text only; country is resolved downstream from it so Bratislava
                    # becomes SK rather than being overridden to CZ. Never a country constant.
                    location=self._location(item),
                    country_code=None,
                    remote_signal=None,
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
