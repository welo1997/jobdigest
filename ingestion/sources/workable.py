"""Workable ATS — public job-board widget API for a curated list of companies (no key).

Public endpoint: ``https://apply.workable.com/api/v1/widget/accounts/{account}?details=true``

Built for Slovakia, which is the harder half of the gap. When Jobs.cz and Profesia left
`gather()` the Slovak corpus fell 3 362 → 29 (−99%), and unlike Czechia there is no public
alternative to fall back on: the Slovak labour office publishes **no per-vacancy open data**
(the national and EU catalogues carry only Štatistický úrad SR's aggregate vacancy counts),
and worki.sk — the successor to ISTP, run by TREXIMA Bratislava — states that further
dissemination of its job offers without written consent infringes copyright, which is the
same bar that excluded Alma Career. Company ATS boards are therefore the *only* open route
into Slovak inventory, and Workable is where the Slovak product companies turned out to be.

`telecommuting` is a structured employer-set field, so it may set `remote_signal` directly —
the same trust Lever's `workplaceType` and Recruitee's `remote` get, and unlike anything
derived from prose. `country` arrives per posting (`locations[].countryCode`), so there is
no source-level country constant here.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = politeness.HEADERS

#: Verified-live Workable accounts, confirmed 2026-08-03 against each board's own postings
#: (account `name` + posting city), not merely a live HTTP 200. Both are Bratislava/Prague
#: companies that no other adapter in this repo reaches.
ACCOUNTS = [
    "innovatrics",   # 23 — Bratislava, biometrics
    "cloudtalk",     #  8 — Prague / Bratislava, calling software
]

_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(html: Optional[str]) -> Optional[str]:
    if not html:
        return None
    out = _WS.sub(" ", _TAG.sub(" ", html)).strip()
    return out or None


def _country(job: dict) -> Optional[str]:
    """ISO-3166 alpha-2 from the posting's own location block."""
    for loc in job.get("locations") or []:
        code = (loc.get("countryCode") or "").strip().upper()
        if len(code) == 2:
            return code
    return None


def _location(job: dict) -> Optional[str]:
    parts = [job.get("city"), job.get("country")]
    return ", ".join(p for p in parts if p) or None


def _posted(job: dict) -> Optional[date]:
    raw = job.get("published_on") or job.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


class WorkableSource(BaseSource):
    """Workable public job boards across a curated list of accounts."""

    def __init__(self, accounts: Optional[list[str]] = None):
        self._accounts = accounts or ACCOUNTS

    @property
    def source_name(self) -> str:
        return "workable"

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        for account in self._accounts:
            url = (f"https://apply.workable.com/api/v1/widget/accounts/{account}"
                   "?details=true")
            try:
                politeness.throttle(url)
                resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    # Loud on purpose: the list is short and hand-curated, so a dark board is
                    # a real loss rather than noise — and a silent zero is the failure this
                    # repo keeps rediscovering.
                    logger.warning("Workable %s: HTTP %s", account, resp.status_code)
                    continue
                payload = resp.json()
                jobs = payload.get("jobs", [])
                for job in jobs:
                    job["_account"] = account
                    job["_account_name"] = payload.get("name")
                out.extend(jobs)
                logger.info("Workable %s: %d", account, len(jobs))
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Workable %s failed: %s", account, exc)
        return out

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        for job in raw_items:
            url = job.get("url") or job.get("shortlink")
            title = (job.get("title") or "").strip()
            if not url or not title:
                continue
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                company=job.get("_account_name") or job.get("_account"),
                url=url,
                description=_text(job.get("description")),
                location=_location(job),
                country_code=_country(job),
                # Employer-set structured flag. `telecommuting` is Workable's own field, not
                # a phrase mined from the description, so it carries the same weight as
                # Lever's `workplaceType`.
                remote_signal=bool(job.get("telecommuting")),
                posted_at=_posted(job),
                source_category=job.get("function") or job.get("department"),
            ))
        return out
