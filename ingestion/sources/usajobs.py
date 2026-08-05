"""USAJOBS — the US federal government's official jobs register, via its open REST API.

The US analogue of `mpsv` (CZ) and `platsbanken` (SE): a national public employment register
published as an open API, so it is a permitted source in the sense those two are — no scraping,
no terms problem, the government publishes it for reuse. It became worth building on 2026-08-05,
when the US became a *selectable* country: before that every US-located row was gated out, so a
register of exclusively-US federal jobs would have been pure gated-out inventory. Now a US
subscriber picking on-site US roles is exactly who this serves.

Access needs a free, instant, self-service API key (register an email at
https://developer.usajobs.gov/apirequest/ — no application, no signature, so the "we do not
correspond with publishers" skip rule does not apply, the same footing as an Adzuna key). Two
env vars: `USAJOBS_API_KEY` (the key) and `USAJOBS_EMAIL` (the registered email, sent as the
User-Agent, which the API requires). Missing either raises `KeyError` in the constructor and
`gather()` skips the source — the Adzuna pattern.

Two design choices carried straight from `mpsv`/`platsbanken`:

  - **`country_code="US"` is a source-level constant.** Normally that is wrong (see Arbeitnow),
    but USAJOBS registers United States federal vacancies by definition — there is no non-US
    row to mislabel. A handful are overseas federal posts (embassies, bases); those are the
    rare exception and still US-government employment, so the constant stands.
  - **Scope is bounded to professional roles.** The register is the whole federal workforce,
    including trades and wage-grade labour, and loading all of it would put custodial and
    food-service work into the *widened* retrieval path that drops the recall predicate. So it
    is queried by a `SEARCH_TERMS` matrix of professional/tech keywords rather than pulled
    wholesale — the Adzuna approach, and the reason `mpsv` keeps only ISCO 1–3.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

USAJOBS_BASE = "https://data.usajobs.gov/api/search"

#: Professional / white-collar keywords, spanning the taxonomy rather than data alone — the
#: shortlist recall predicate is `category OR keyword`, so a term list that only said "data"
#: could never contribute a design or product federal role to anyone. Keyword searches title
#: and description; results are de-duplicated across terms by the posting's own id.
SEARCH_TERMS = [
    "data scientist",
    "data analyst",
    "software engineer",
    "software developer",
    "information technology",
    "cybersecurity",
    "cloud engineer",
    "devops",
    "product manager",
    "ux designer",
]

#: USAJOBS caps a response at 500 whatever you ask; 100 keeps each page cheap and the paging
#: loop honest. Page by the count **received**, never the count requested — the Himalayas bug.
RESULTS_PER_PAGE = 100

#: Runaway guard, not a trimmer: a professional keyword rarely exceeds a few hundred federal
#: postings, so this is generous. `test_usajobs.py` pins the paging stop conditions.
MAX_PAGES_PER_TERM = 5

#: Freshness. USAJOBS listings carry an ApplicationCloseDate and do expire, but a 60-day window
#: (the API maximum) keeps the pull to recent openings rather than the long tail — the same
#: reason The Muse needs `MAX_AGE_DAYS`. The API takes this as a day count.
DATE_POSTED_DAYS = 30

_REMOTE_HINTS = ("remote", "telework", "work from home", "anywhere in the u.s")


class USAJobsSource(BaseSource):
    """USAJOBS Search API — loops a professional-keyword matrix, all rows United States."""

    def __init__(self, api_key: Optional[str] = None, email: Optional[str] = None):
        self._api_key = api_key or os.environ["USAJOBS_API_KEY"]
        # The API *requires* a User-Agent, and rejects the call without one — so the registered
        # email is not optional metadata, it is a second credential. Validate the shape here
        # rather than discovering it on the first 401, the Adzuna lesson.
        self._email = email or os.environ["USAJOBS_EMAIL"]
        for name, value in (("USAJOBS_API_KEY", self._api_key),
                            ("USAJOBS_EMAIL", self._email)):
            if not value.strip():
                raise KeyError(name)

    @property
    def source_name(self) -> str:
        return "usajobs"

    def _headers(self) -> dict:
        return {
            "Host": "data.usajobs.gov",
            "User-Agent": self._email,
            "Authorization-Key": self._api_key,
        }

    def fetch(self) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        for term in SEARCH_TERMS:
            for page in range(1, MAX_PAGES_PER_TERM + 1):
                items = self._fetch_page(term, page)
                new = 0
                for item in items:
                    mid = str((item.get("MatchedObjectId") or "")).strip()
                    if mid and mid not in seen:
                        seen.add(mid)
                        results.append(item)
                        new += 1
                # Stop on a short page (the real end) or a page that added nothing new —
                # federal listings repeat across keyword searches, so `new == 0` is the
                # common terminator and page count alone would over-run.
                if len(items) < RESULTS_PER_PAGE or new == 0:
                    break
        logger.info("USAJOBS: fetched %d unique postings", len(results))
        return results

    def _fetch_page(self, keyword: str, page: int) -> list[dict]:
        params = {
            "Keyword": keyword,
            "ResultsPerPage": RESULTS_PER_PAGE,
            "Page": page,
            "DatePosted": DATE_POSTED_DAYS,
        }
        try:
            resp = requests.get(USAJOBS_BASE, params=params, headers=self._headers(),
                                timeout=20)
            resp.raise_for_status()
            return (resp.json().get("SearchResult", {}) or {}).get("SearchResultItems", []) or []
        except requests.RequestException as exc:
            logger.warning("USAJOBS %s p%d failed: %s", keyword, page, exc)
            return []

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            d = item.get("MatchedObjectDescriptor", {}) or {}
            url = d.get("PositionURI") or d.get("ApplyURI", [None])[0]
            if not url:
                continue
            location = self._location(d)
            blob = f"{d.get('PositionTitle', '')} {location or ''}".lower()
            is_remote = any(h in blob for h in _REMOTE_HINTS) or None
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=d.get("PositionTitle"),
                    company=d.get("OrganizationName"),
                    url=url,
                    description=self._description(d),
                    location=location,
                    country_code="US",   # federal register — US by definition, see module docs
                    remote_signal=is_remote,
                    salary_raw=self._salary(d),
                    currency="USD",
                    posted_at=self._parse_date(d.get("PublicationStartDate")),
                )
            )
        logger.info("USAJOBS: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _location(d: dict) -> Optional[str]:
        disp = d.get("PositionLocationDisplay")
        if disp:
            return disp
        locs = d.get("PositionLocation") or []
        names = [l.get("LocationName") for l in locs if l.get("LocationName")]
        return "; ".join(names) if names else None

    @staticmethod
    def _description(d: dict) -> Optional[str]:
        details = d.get("UserArea", {}).get("Details", {}) or {}
        summary = d.get("QualificationSummary") or details.get("JobSummary")
        return summary or None

    @staticmethod
    def _salary(d: dict) -> Optional[str]:
        rem = d.get("PositionRemuneration") or []
        if not rem:
            return None
        lo = rem[0].get("MinimumRange")
        hi = rem[0].get("MaximumRange")
        if lo and hi:
            return f"{lo} - {hi}"
        return lo or hi or None

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
