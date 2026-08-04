"""Lever ATS — fetch jobs from company job-board APIs (no key).

Public endpoint: https://api.lever.co/v0/postings/{org}?mode=json
Org tokens below are verified-live (EU-heavy); grow freely.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = politeness.HEADERS

# Verified-live Lever boards (EU-heavy tech + fintech / ecommerce). Re-probed 2026-08-01:
# all 10 originals still serve jobs. Lever has the lowest hit rate of the three ATS adapters
# — 6 live out of 82 EU/US candidates probed — because most of the companies that used it
# have since moved to Greenhouse or Ashby. Check the other two adapters before adding here.
ORGS = ["spotify", "qonto", "contentsquare", "aircall", "swile", "blablacar",
        "zopa", "moonpig", "moonpay", "vestiairecollective",
        # added 2026-08-01, FR/PT/EE-heavy
        "farfetch", "malt", "agicap", "360learning", "pipedrive", "ledger",
        # CZ-headquartered, added 2026-08-03 via scripts/discover_ats.py. Both were found by
        # probing the Lever API directly: neither careers page carries an ATS link in its
        # HTML, so a page scan alone sees nothing. Job counts are small but the postings are
        # Prague-based, which is what the Czech inventory now lacks.
        "ataccama", "keboola",
        # IT/ES pass, 2026-08-04, identity-checked. `reply` was found and rejected: it is
        # genuinely Reply, but the board is its US arm (Chicago/Atlanta/Seattle) and carries
        # no Italian roles, so it would add US inventory to a corpus already 24% US.
        "musixmatch",     #  6 — Bologna
        "jobandtalent",   # 23 — Madrid HQ / Guadalajara / Stockholm
        # Thin-country pass, 2026-08-04 (210 employers across AT/NL/BE/PT/DK/IE/RO/GR and the
        # Baltics — the 19 selectable countries holding under 300 active postings each). Every
        # token below was checked against its own postings' locations, not its job count.
        "oxylabs",        # 49 — Vilnius / Warsaw. Lithuania had 24 active postings.
        "swordhealth",    # 19 — Porto. Also answers on Greenhouse with **one** posting;
        #                        taken here because it is the board carrying the inventory.
        "deliverect"]     # 26 — Amersfoort + Ghent HQ. Belgian, but only 2 of 26 rows are
        #                        BE (the rest Mexico City / Casablanca) — kept because it is
        #                        genuine and small, not because it fills Belgium.


class LeverSource(BaseSource):
    """Lever public postings API across a curated list of orgs."""

    def __init__(self, orgs: Optional[list[str]] = None):
        self._orgs = orgs or ORGS

    @property
    def source_name(self) -> str:
        return "lever"

    def fetch(self) -> list[dict]:
        all_jobs: list[dict] = []
        for org in self._orgs:
            try:
                resp = requests.get(
                    f"https://api.lever.co/v0/postings/{org}?mode=json",
                    headers=HEADERS, timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if isinstance(data, list):
                    for j in data:
                        j["_org"] = org
                    all_jobs.extend(data)
            except requests.RequestException as exc:
                logger.warning("Lever %s failed: %s", org, exc)
        logger.info("Lever: fetched %d jobs across %d orgs", len(all_jobs), len(self._orgs))
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("hostedUrl") or item.get("applyUrl") or ""
            if not url:
                continue
            cats = item.get("categories") or {}
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("text"),
                    company=item.get("_org"),
                    url=url,
                    description=item.get("descriptionPlain") or item.get("description"),
                    location=cats.get("location"),
                    country_code=(item.get("country") or None),
                    remote_signal=(str(item.get("workplaceType", "")).lower() == "remote") or None,
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_ms(item.get("createdAt")),
                )
            )
        logger.info("Lever: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _parse_ms(ms: Optional[int]) -> Optional[date]:
        if not ms:
            return None
        try:
            return datetime.utcfromtimestamp(int(ms) / 1000).date()
        except (ValueError, OSError, OverflowError):
            return None
