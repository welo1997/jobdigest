"""Adzuna — fetch tech jobs via REST API across multiple countries."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

# Country config: (country_code, pages_to_fetch). Adzuna runs one index per country and has
# no pan-European endpoint, so breadth here is literally the list below. These are every
# EU-27 market Adzuna covers, plus GB/US/CA. Absent because Adzuna has no index for them:
# CZ and SK (covered by jobs.cz, Profesia, StartupJobs and Cocuma), and the Nordics and
# CEE beyond Poland (covered thinly by the ATS adapters and The Muse).
COUNTRY_CONFIG = [
    ("us", 3),   # 60 results — largest market, and the one the remote-only boards under-serve
    ("gb", 2),
    ("de", 2),
    ("fr", 1),
    ("nl", 1),
    ("es", 1),
    ("it", 1),
    ("pl", 1),
    ("at", 1),
    ("be", 1),
    ("ca", 1),
]

# Search phrases (Adzuna `what` matches all words). Two things changed here on 2026-08-01,
# both because this adapter now feeds JobDigest rather than a personal remote-only search:
#
#   - the "remote " prefix is gone from most terms. Subscribers choose countries and cities;
#     a Prague subscriber wanting on-site Prague work was unreachable through a term list
#     where every phrase demanded the word "remote".
#   - the roles span the taxonomy instead of data alone, because the shortlist recall
#     predicate is `category OR keyword` and six of the nine categories had no term here at
#     all — Adzuna could never contribute a design or product posting to anyone.
SEARCH_TERMS = [
    "data engineer",
    "data analyst",
    "data scientist",
    "machine learning engineer",
    "software engineer",
    "software developer",
    "devops engineer",
    "cloud engineer",
    "product manager",
    "ux designer",
    "remote data",          # kept: the one phrase that still targets remote-first listings
]

RESULTS_PER_PAGE = 20

#: Adzuna's free tier allows 250 calls/day per app id, and one run costs
#: ``sum(pages) * len(SEARCH_TERMS)``. The budget is stated here rather than left implicit
#: because the cost is a *product* of two lists: adding one country and one search term looks
#: like two small edits and is a 30-call increase. `ingestion/tests/test_adzuna.py` fails if a
#: change pushes a run past this, which is the only place the arithmetic gets checked — a
#: quota overrun returns HTTP 429 that `_fetch_page` swallows into an empty list, so in
#: production it would look like "Adzuna got quieter", not like an error.
DAILY_REQUEST_BUDGET = 200

# Substrings that mark a posting as remote (used to set remote_signal).
_REMOTE_HINTS = ("remote", "work from home", "anywhere", "distributed")


class AdzunaAuthError(RuntimeError):
    """Adzuna rejected the app id / key. Not retryable, and not a quiet empty result."""


class AdzunaSource(BaseSource):
    """Adzuna REST API — loops countries and search terms."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._app_id = app_id or os.environ["ADZUNA_APP_ID"]
        self._api_key = api_key or os.environ["ADZUNA_API_KEY"]
        # Validate the *shape* here, not on the first HTTP call. A missing key raises
        # KeyError above and `gather` skips the source; a key that is present but not a
        # credential used to sail through and be sent to Adzuna verbatim, so every call in
        # the run was rejected and the source only admitted it after burning them. Both
        # known non-credentials are silent to the eye in a .env:
        #   - an unresolved `op://vault/item/field` 1Password reference, which is what this
        #     repo's .env actually holds — it resolves only under `op run --env-file=.env`,
        #     and without that the literal string is what reaches the API;
        #   - an empty value, which `os.environ[...]` returns happily rather than raising,
        #     so it never reaches the KeyError path that would have skipped the source.
        for name, value in (("ADZUNA_APP_ID", self._app_id),
                            ("ADZUNA_API_KEY", self._api_key)):
            if not value.strip():
                raise AdzunaAuthError(
                    f"{name} is set but empty — Adzuna would reject every call this run."
                )
            if value.startswith("op://"):
                raise AdzunaAuthError(
                    f"{name} is an unresolved 1Password reference, not a credential. "
                    "Run under `op run --env-file=.env -- <command>` so op resolves it, or "
                    "put the literal value in the environment."
                )

    @property
    def source_name(self) -> str:
        return "adzuna"

    def fetch(self) -> list[dict]:
        all_results: list[dict] = []
        seen_ids: set[str] = set()

        for country, max_pages in COUNTRY_CONFIG:
            for term in SEARCH_TERMS:
                for page in range(1, max_pages + 1):
                    items = self._fetch_page(country, term, page)
                    for item in items:
                        adzuna_id = str(item.get("id", ""))
                        if adzuna_id and adzuna_id not in seen_ids:
                            seen_ids.add(adzuna_id)
                            item["_country"] = country
                            all_results.append(item)

        logger.info("Adzuna: fetched %d unique postings", len(all_results))
        return all_results

    def _fetch_page(self, country: str, what: str, page: int) -> list[dict]:
        url = f"{ADZUNA_BASE}/{country}/search/{page}"
        params = {
            "app_id": self._app_id,
            "app_key": self._api_key,
            "what": what,
            "results_per_page": RESULTS_PER_PAGE,
            "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            # Checked before `raise_for_status`, because an HTTPError here is indistinguishable
            # from a timeout once it reaches the handler below — and the two want opposite
            # treatment. A flaky page should be skipped; a rejected credential should stop the
            # source, because every one of the remaining calls will be rejected too and the
            # run would otherwise report a clean zero. Adzuna's credentials were in exactly
            # this state on 2026-08-01 (AUTH_FAIL on every country) and nothing said so: the
            # handler logged a warning per page and `fetch` returned an empty list, which
            # reads downstream as "Adzuna had nothing today".
            if resp.status_code in (401, 403):
                raise AdzunaAuthError(
                    f"Adzuna rejected the credentials (HTTP {resp.status_code}). "
                    "Check ADZUNA_APP_ID / ADZUNA_API_KEY — every call this run will fail."
                )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            logger.warning("Adzuna %s/%s p%d failed: %s", country, what, page, exc)
            return []

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            redirect_url = item.get("redirect_url", "")
            if not redirect_url:
                continue

            salary_raw = self._build_salary(item)
            currency = self._currency_for_country(item.get("_country", ""))
            location = item.get("location", {}).get("display_name")
            blob = f"{item.get('title', '')} {location or ''} {item.get('description', '')}".lower()
            is_remote = any(hint in blob for hint in _REMOTE_HINTS) or None

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(redirect_url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("company", {}).get("display_name"),
                    url=redirect_url,
                    description=item.get("description"),
                    location=location,
                    country_code=item.get("_country", "").upper() or None,
                    remote_signal=is_remote,  # inferred from the posting text
                    salary_raw=salary_raw,
                    currency=currency,
                    posted_at=self._parse_date(item.get("created")),
                )
            )
        logger.info("Adzuna: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        if sal_min and sal_max:
            return f"{sal_min} - {sal_max}"
        if sal_min:
            return str(sal_min)
        if sal_max:
            return str(sal_max)
        return None

    @staticmethod
    def _currency_for_country(country: str) -> Optional[str]:
        # One entry per country in COUNTRY_CONFIG — `test_adzuna.py` fails if that stops
        # being true. A missing entry is not a crash, it is a posting stored with a salary
        # figure and no unit, which is worse than storing no salary at all.
        return {
            "at": "EUR", "be": "EUR", "de": "EUR", "es": "EUR", "fr": "EUR",
            "it": "EUR", "nl": "EUR",
            "pl": "PLN",
            "gb": "GBP",
            "us": "USD",
            "ca": "CAD",
        }.get(country.lower())

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
