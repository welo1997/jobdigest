"""Himalayas — fetch worldwide remote jobs via the public JSON API.

https://himalayas.app/jobs/api?limit=&offset= returns {"jobs": [...]}. Each job
carries `locationRestrictions` (list) and `timezoneRestrictions`, and an `expiryDate`
epoch used to skip already-expired postings at ingestion time. Every listing is remote.

**`timezoneRestrictions` was fetched and dropped, and this docstring used to claim otherwise**
(it said both restrictions were kept "in the location text for region tagging"; only the
locations ever were). Corrected 2026-08-14. It is a list of UTC offsets — `[-10, -9, -8, -7,
-6, -5, 14]` for a US role, `[8]` for Singapore, `[0]` for UK — present on 20 of 20 sampled
postings, and it is the only structured timezone data in this entire corpus. Both restrictions
now go to `scope_raw`, offsets rendered as `UTC-10..UTC-5` so a later reader can parse them
back out without another ingest.

An **empty** `locationRestrictions` is a meaningful answer here rather than a missing one:
Himalayas asks publishers what they want to restrict, so no restrictions recorded is the
board's way of saying anywhere. It is deliberately *not* translated into a scope claim — that
would be inferring a promise from silence, which is the one error `geo.remote_reach` must never
make. `location` keeps falling back to the bare string "Remote" and the reach stays unknown.

The upstream sometimes serves the field *name* instead of the value
--------------------------------------------------------------------
Found on 2026-08-08 by `scripts/check_links.py`, which prints the employer alongside the
link: **every row of `?limit=100&offset=0` came back with `companyName: "name"`** — the
literal string, for all 20 postings, repeatably, out of a CDN cache 66 minutes old
(`x-vercel-cache: HIT`). Other parameter combinations against the same endpoint at the same
moment returned real employers, so it is a poisoned cache variant upstream rather than
anything we send; the variant our ingest uses is the broken one.

Nothing else could have caught it. The count was right, the titles were right, the links
were right, and "name" is a plausible-looking string — it would simply have appeared as the
employer in subscribers' digests. `_company` therefore refuses placeholder values, and
`normalize` shouts when a whole run shares one employer.

**Dropping the name is the safe direction, not merely the tidy one.** `digest.dedupe_key`
keys on (company, title, city) and treats an *empty* key as always unique, never a match —
so a missing employer suppresses nothing. A wrong-but-uniform employer does the opposite: it
makes every Himalayas posting look like the same company, so two different employers
advertising the same role in the same city collapse into one and the second is never emailed.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HIMALAYAS_API_URL = "https://himalayas.app/jobs/api"
HEADERS = politeness.HEADERS
#: What we ask for. Himalayas caps a response at 20 regardless, so this is an upper bound and
#: never a stride — see `fetch`, which advances by the count actually received.
PAGE_SIZE = 100
MAX_PAGES = 15

#: Values that are the *shape* of an employer name without being one. `"name"` is what the
#: broken cache variant serves; the rest are the usual JSON placeholders. Compared casefolded.
_NOT_A_COMPANY = frozenset({"name", "companyname", "company", "null", "none", "undefined",
                            "n/a", "-"})

# Map common Himalayas location-restriction strings to ISO-3166 alpha-2.
_COUNTRY_MAP = {
    "united states": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB",
    "germany": "DE", "netherlands": "NL", "france": "FR", "spain": "ES",
    "poland": "PL", "austria": "AT", "ireland": "IE", "portugal": "PT",
    "czech republic": "CZ", "czechia": "CZ", "slovakia": "SK", "canada": "CA",
}


def _utc(offset: float) -> str:
    """`-3.5` -> ``UTC-3.5``, `0` -> ``UTC+0``. Half-hour zones are real (India, Newfoundland)."""
    whole = int(offset)
    body = str(whole) if offset == whole else f"{offset:g}"
    return f"UTC{'+' if offset >= 0 else ''}{body}"


def _company(raw) -> Optional[str]:
    """An employer name, or None if the API handed back a placeholder. See the docstring."""
    name = (raw or "").strip() if isinstance(raw, str) else ""
    return None if name.casefold() in _NOT_A_COMPANY else (name or None)


class HimalayasSource(BaseSource):
    """Himalayas public API — worldwide remote roles with region restrictions."""

    @property
    def source_name(self) -> str:
        return "himalayas"

    def fetch(self) -> list[dict]:
        """Page by what the API *returns*, not by what we asked for.

        Himalayas caps a response at 20 jobs however large a `limit` you send. The loop used
        to advance `offset` by the requested PAGE_SIZE (100) and stop on
        `len(jobs) < PAGE_SIZE` — so the first response, 20 jobs against a request for 100,
        satisfied the stop condition every single time. This source has been returning exactly
        one page since it was written; the other four pages were never fetched, and the 80
        jobs between each offset step would have been skipped even if they had been.

        Neither half is visible from the outside: 20 postings is a plausible number for a
        remote board, and nothing logs a short page. Advancing by the received count fixes
        both at once and needs no constant to stay in sync with their server.
        """
        all_jobs: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            try:
                resp = requests.get(
                    HIMALAYAS_API_URL,
                    params={"limit": PAGE_SIZE, "offset": offset},
                    headers=HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
            except requests.RequestException as exc:
                logger.warning("Himalayas offset %d failed: %s", offset, exc)
                break
            if not jobs:
                break
            all_jobs.extend(jobs)
            offset += len(jobs)
        logger.info("Himalayas: fetched %d postings over %d pages", len(all_jobs), MAX_PAGES)
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        now = time.time()
        postings: list[JobPosting] = []
        skipped_expired = 0
        for item in raw_items:
            url = item.get("applicationLink") or ""
            if not url:
                continue
            expiry = item.get("expiryDate")
            if expiry and float(expiry) < now:
                skipped_expired += 1
                continue

            locations = item.get("locationRestrictions") or []
            location = ", ".join(locations) if locations else "Remote"

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=_company(item.get("companyName")),
                    url=url,
                    description=item.get("description") or item.get("excerpt"),
                    location=location,
                    country_code=self._country_from(locations),
                    remote_signal=True,  # inherently remote source
                    salary_raw=self._build_salary(item),
                    currency=item.get("currency"),
                    posted_at=self._parse_epoch(item.get("pubDate")),
                    scope_raw=self._scope(locations, item.get("timezoneRestrictions")),
                )
            )
        logger.info(
            "Himalayas: normalised %d postings (%d expired skipped)",
            len(postings), skipped_expired,
        )
        # A multi-employer board on which everything shares one employer is a broken payload,
        # not a quiet day — and it is invisible downstream, because the count and the titles
        # stay right. The placeholder above is the case we know; this catches the next one,
        # whatever string it picks. Logged at error level so the pipeline's own logs carry it.
        names = {p.company for p in postings if p.company}
        if len(postings) >= 10 and len(names) == 1:
            logger.error("Himalayas: all %d postings claim one employer (%r) — the feed is "
                         "serving a degraded payload, treat the company column as suspect",
                         len(postings), next(iter(names)))
        return postings

    @staticmethod
    def _scope(locations: list, timezones) -> Optional[str]:
        """Both restrictions as one text claim, or None when the board recorded neither.

        Offsets are collapsed into contiguous `UTC-10..UTC-5` runs because the raw list for a
        US role is seven numbers and reads as noise; the run form survives a round trip. `14`
        (Kiritimati) rides along with the American offsets in Himalayas' own data and is kept
        rather than cleaned — it is their answer, and inventing a tidier one is how a claim
        becomes a classification.
        """
        parts: list[str] = []
        if locations:
            parts.append(", ".join(str(loc).strip() for loc in locations if str(loc).strip()))
        offsets = sorted({float(t) for t in (timezones or [])
                          if isinstance(t, (int, float))
                          or str(t).replace("-", "").replace(".", "").isdigit()})
        if offsets:
            runs: list[str] = []
            start = prev = offsets[0]
            for off in offsets[1:] + [None]:
                if off is not None and off - prev <= 1:
                    prev = off
                    continue
                runs.append(_utc(start) if start == prev else f"{_utc(start)}..{_utc(prev)}")
                if off is not None:
                    start = prev = off
            parts.append(", ".join(runs))
        return " | ".join(p for p in parts if p) or None

    @staticmethod
    def _country_from(locations: list) -> Optional[str]:
        for loc in locations:
            code = _COUNTRY_MAP.get(str(loc).strip().lower())
            if code:
                return code
        return None

    @staticmethod
    def _build_salary(item: dict) -> Optional[str]:
        lo, hi = item.get("minSalary"), item.get("maxSalary")
        if lo and hi:
            return f"{lo} - {hi}"
        return str(lo) if lo else (str(hi) if hi else None)

    @staticmethod
    def _parse_epoch(epoch: Optional[int]) -> Optional[date]:
        if not epoch:
            return None
        try:
            return datetime.utcfromtimestamp(int(epoch)).date()
        except (ValueError, OSError, OverflowError):
            return None
