"""Teamtailor ATS — public career-site JSON Feed for a curated list of companies (no key).

Public endpoint: ``https://{tenant}.teamtailor.com/jobs.json`` — a JSON Feed (application/
feed+json) that every Teamtailor career site publishes without a key, login, or browser.
Each item carries a schema.org ``_jobposting`` block with a structured ``jobLocation`` (ISO
``addressCountry``), ``baseSalary``, ``hiringOrganization.name`` and ``datePosted`` — so the
company name is read off the feed, never guessed from the slug, and the country resolves
cleanly.

Why this is permitted (settled 2026-08-06 — read this before assuming, per the repo's rule
that a source is settled on its terms, never on robots.txt alone):
  - **There is no anti-scraping term.** Teamtailor's public footer carries only a Privacy
    Policy, Cookie Policy, Security page, Code of Conduct and Modern Slavery statement — no
    Terms of Service governing career-site visitors, and nothing forbidding automated reading.
    (Contrast Alma Career / BambooHR / the Bundesagentur, each of which *did* forbid it in a
    binding document while robots.txt looked fine — the three times that trap has been hit.)
  - **robots.txt affirmatively allows it.** A career site's robots.txt disallows only
    ``/app/``, ``/messages/``, ``/messenger/`` and ``/jobs/internal/``; the public ``/jobs``
    path and the feed are allowed, and it carries ``Content-Signal: search=yes, ai-input=yes``
    (only ``ai-train=no``) — a machine-readable permission for exactly this use: read for
    search / as input to a matcher, not for training.
  - **The feed is published for consumption.** Teamtailor markets AI-agent/aggregator access
    to these feeds; ``jobs.json`` exists to be read.

Personio was evaluated in the same pass and is NOT built: its ``/xml`` feed works, but its
governing website terms could not be read (the marketing site returns 429/403 to every
attempt, as it has every prior time), and the ``/xml`` endpoint answers with a job count for
bogus slugs (``amazon``, ``johnson`` — neither a Personio customer), so identity cannot be
verified the way it can here. Unresolved terms are not permission; it stays skipped.

Tenants are identity-checked against each board's own ``hiringOrganization.name`` and posting
countries, not a live 200 — the same discipline as recruitee/workable. The curated set fills
the thinnest selectable countries (EE/LV/FI/DK) that the Greenhouse/Ashby/Lever passes could
not reach, because those companies run Teamtailor rather than a supported ATS.

No source-level country constant and no source ``remote_signal``: the feed gives a per-posting
country, and it carries no structured remote flag, so remote is left to the downstream text
classifier (``geo.is_fully_remote``) rather than asserted here.
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
_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: Verified live 2026-08-06 against each board's own jobs.json — company name AND posting
#: countries, not a job count. The subdomain is the exact Teamtailor slug and is not always
#: derivable from the company name (Printful's is `printfulinc`, IQM's is `iqm` not
#: `meetiqm`). Skipped in the same pass: `starship` (0 jobs), `edrone` (3 of 4 Brazil).
TENANTS = [
    "comodule",     #  3 — Comodule, Tallinn (EE) — IoT for e-bikes
    "mintos",       # 21 — Mintos, Riga (LV) 20 + LT/DE — investment marketplace
    "printfulinc",  # 37 — FYUL (ex-Printful), Riga (LV) 12 + ES 9 + PL 7 + EE 4 + spread
    "iqm",          # 18 — IQM Quantum Computers, Espoo (FI) 12 + Munich (DE) 10
    "templafy",     #  1 — Templafy, Copenhagen (DK)
    "queueit",      #  2 — Queue-it ApS, Copenhagen (DK)
    "lightyear",    #  1 — Lightyear, Tallinn (EE) — investing app
    "cybelangel",   #  8 — CybelAngel, Paris (FR) 5 — cyber threat intelligence
    "memobank",     #  1 — Memo Bank, Paris (FR). NOT recruitee:memo, a Belgian impostor.
    "sweep",        #  7 — SWEEP, Paris (FR) 4 + London — carbon accounting
    "podimo",       #  7 — Podimo, Amsterdam (NL) 6 + Berlin — audio (Danish-HQ)
    # Expansion pass 2, 2026-08-06, identity-checked against the postings.
    "infraspeak",   #  9 — Infraspeak, Porto (PT) + Barcelona — maintenance-management SaaS
]


def _text(html: Optional[str]) -> Optional[str]:
    if not html:
        return None
    out = _WS.sub(" ", _TAG.sub(" ", html)).strip()
    return out or None


def _first_address(jp: dict) -> dict:
    for loc in jp.get("jobLocation") or []:
        addr = (loc or {}).get("address") or {}
        if addr:
            return addr
    return {}


def _country(addr: dict) -> Optional[str]:
    code = (addr.get("addressCountry") or "").strip().upper()
    return code if len(code) == 2 else None


def _location(addr: dict) -> Optional[str]:
    parts = [addr.get("addressLocality"), addr.get("addressCountry")]
    return ", ".join(p for p in parts if p) or None


def _salary(jp: dict) -> tuple[Optional[str], Optional[str]]:
    """(salary_raw, currency) from a schema.org MonetaryAmount, best-effort."""
    sal = jp.get("baseSalary") or {}
    val = sal.get("value") or {}
    currency = sal.get("currency") or None
    lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText")
    if lo is None and hi is None:
        return None, currency
    amount = f"{lo}-{hi}" if (lo is not None and hi is not None and lo != hi) else str(lo if lo is not None else hi)
    raw = amount + (f" {currency}" if currency else "") + (f"/{unit}" if unit else "")
    return raw.strip() or None, currency


def _posted(jp: dict, item: dict) -> Optional[date]:
    raw = jp.get("datePosted") or item.get("date_published")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


#: Talent-pool / spontaneous-application entries carry no real role and would only add noise
#: to a shortlist. Dropped by title, deliberately narrow — a genuine posting never matches.
_TALENT_POOL = re.compile(
    r"submit your cv|didn'?t find|couldn'?t find|open application|spontaneous|"
    r"talent (pool|community|network)|connect with us|future opportunit|"
    r"otevřená pozice|iniciativní", re.I)


class TeamtailorSource(BaseSource):
    """Teamtailor public career-site JSON feeds across a curated list of tenants."""

    def __init__(self, tenants: Optional[list[str]] = None):
        self._tenants = tenants or TENANTS

    @property
    def source_name(self) -> str:
        return "teamtailor"

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        for tenant in self._tenants:
            url = f"https://{tenant}.teamtailor.com/jobs.json"
            try:
                if not politeness.robots_allows(url):
                    logger.warning("Teamtailor %s: blocked by robots.txt", tenant)
                    continue
                politeness.throttle(url)
                resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    # Loud on purpose: the list is short and hand-curated, so a dark board is
                    # a real loss rather than noise — the silent-zero failure this repo keeps
                    # rediscovering.
                    logger.warning("Teamtailor %s: HTTP %s", tenant, resp.status_code)
                    continue
                items = resp.json().get("items", [])
                for it in items:
                    it["_tenant"] = tenant
                out.extend(items)
                logger.info("Teamtailor %s: %d", tenant, len(items))
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Teamtailor %s failed: %s", tenant, exc)
        return out

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        for it in raw_items:
            url = it.get("url")
            title = (it.get("title") or "").strip()
            if not url or not title or _TALENT_POOL.search(title):
                continue
            jp = it.get("_jobposting") or {}
            addr = _first_address(jp)
            org = (jp.get("hiringOrganization") or {}).get("name")
            salary_raw, currency = _salary(jp)
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                company=(org or it.get("_tenant") or "").strip() or None,
                url=url,
                description=_text(jp.get("description") or it.get("content_html")),
                location=_location(addr),
                country_code=_country(addr),
                remote_signal=None,
                salary_raw=salary_raw,
                currency=currency,
                posted_at=_posted(jp, it),
            ))
        return out
