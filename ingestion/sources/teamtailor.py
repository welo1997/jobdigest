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
    # AT pass, 2026-08-07. Two Teamtailor slugs probed for Austrian companies answered live
    # and were **rejected on their own postings**, which is the whole reason this check
    # exists: `medicus` is a gynaecology clinic in Oslo, not Medicus AI of Vienna, and
    # `journi` is eight near-identical "Copy of Fullstack Developer … UK" rows in Banbury,
    # GB — a test board, not Journi of Vienna.
    "tractivegmbh", #  2 — Tractive GmbH, Pasching (AT). Slug is not derivable from the name.
    # IT expansion pass, 2026-08-07, identity-checked against the postings' own cities.
    "weroad",       # 19 — WeRoad, Milano (IT) 6 of 8 + US. Travel; the roles are product,
    #                     data engineering and category management, not tour guiding.
    "unobravointernational",
    #                  4 — Unobravo, Milan (IT) 3 of 4. **The company is an online-therapy
    #                     platform but this board is its operations/growth hiring** (SEM
    #                     manager, Head of Growth, operations) — which is why it is taken and
    #                     `ashby:serenis`, the same sector but all clinician roles, is not.
    # FR expansion pass, 2026-08-07. Teamtailor turned out to be where the French mid-market
    # is, and it produced this repo's cleanest example of why a slug is not an identity:
    # **`sunday` answers live on two ATSes and they are two different companies** — `ashby:sunday`
    # is a Redwood City robotics firm (mechanical/firmware/motor-control engineers) and
    # `teamtailor:sunday` is the French restaurant-payments company. Only the second is taken.
    # Two more live-200 impostors rejected: `teamtailor:air` (74) is an Italian energy call
    # centre in Rome and Tivoli, not Air France-KLM — as `greenhouse:air` was already known
    # not to be Air Liquide; and `recruitee:lvmh` (49) **is genuinely LVMH** and was skipped
    # anyway, being 49 German beauty-consultant and shop-floor roles with no knowledge work in
    # it — the `ashby:serenis` call again.
    "sunday",       # 35 — Sunday, Paris 3 of 8 + Lyon/Marseille/Strasbourg, London, Miami
    "groupepositive",
    #                  19 — Sarbacane's group ("Positive"), Lyon 3 + Hem (FR) + Berlin (DE) 3
    #                     + Wrocław (PL). The slug is the group name, not the product's.
    "botify",       # 14 — Botify, Paris (FR) 3 of 8 + New York 5; SEO/search analytics
    "adeo",         # 10 — ADEO (Leroy Merlin's parent), Ronchin (FR) 7 of 8 + Paris
    "thales",       # 22 — **Thales Norway AS**, Oslo 5 / Trondheim 3, all of it software
    #                     engineering, architecture and DevOps. Not an impostor — a real
    #                     subsidiary board — and Norway held 23 active postings in the whole
    #                     corpus, so this roughly doubles it. Distinct from workday:thales.
    # RO pass, 2026-08-07. Two of the four supported-ATS hits in 64 Romanian employers were
    # impostors, both on tokens that look like a safe abbreviation of the company name:
    # `ashby:amber` (22) is a Köln/Aachen company, not Amber Studio of Bucharest, and
    # `teamtailor:yonder` (3) is a Cardiff/London fintech, not Yonder of Cluj (tss-yonder.com).
    # Also skipped: `workday:genpact` — real, but Noida/Gurgaon/Hyderabad/Bangalore bulk with
    # 3 Iași rows in 60, and it is N+1, which is the Barclays/Nike call; and `bamboohr:fintechos`,
    # a genuine Romanian product company sitting on an ATS this repo refuses on terms.
    "zitec",        # 10 — Zitec, București (RO) 4 of 8 + remote-RO. Digital/product agency,
    #                     and the only unambiguously Romanian board the pass found.
    # GB expansion pass, 2026-08-07. **Teamtailor has an impostor pattern worth naming**: two
    # generic English slugs on it are Italian staffing/consultancy firms — `air` (74) is a Rome
    # and Tivoli energy call centre, not Air France-KLM, and `united` (15) is eight B2B sales
    # and reception roles in **Pavia**, not United Utilities. Both answered 200 with plausible
    # counts. `wellcome` (2) was skipped for the opposite reason: two social-media roles with
    # **no location at all**, so identity could not be checked either way.
    "attest",       #  9 — Attest, London (GB) 7 of 8 + New York. Consumer research SaaS.
    # BE pass, 2026-08-07.
    "ml6",          # 12 — ML6, Gand (BE) 2 + Amsterdam 4 / Eindhoven (NL) + München (DE).
    #                     Belgian AI consultancy with a Dutch-weighted board; taken for both.
    "babcock",      # 13 — **Babcock France**: Le Cannet-des-Maures 4, Paris 2, Mérignac, Dijon.
    #                     Probed for Babcock International (UK) and it is a real subsidiary
    #                     board, not an impostor — French aviation-support inventory. Third
    #                     time this session a subsidiary board served a country nobody searched
    #                     (after Thales Norway and Playtech's Baltics).
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
