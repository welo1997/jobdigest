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
    # Polish, added 2026-08-04 and identity-checked against each board's own postings.
    "booksy-1",      # 35 — Poland/Spain/UK, booking marketplace. Note the `-1` suffix: the
                     #      bare `booksy` slug is not this board, so it cannot be derived.
    "netguru",       # 28 — Poland (Poznań), software house
    "getresponse",   #  4 — Gdańsk, email marketing
    "exoticca",      # 42 — Barcelona, travel. Added 2026-08-04, identity-checked.
    # Thin-country pass, 2026-08-04. Greece held **43 active postings** in the whole corpus;
    # these four are Greek employers on their own boards, identity-checked by company name
    # and city on the postings themselves.
    "viva",          # 40 — "Viva.com", Marousi (Athens) + Lisbon. The largest of the four.
    "blueground",    # 28 — Athens + Paris + New York. Note the bare slug `j` also answered
                     #      during discovery and returned nothing usable — a live response is
                     #      not a board.
    "skroutz",       # 10 — "Skroutz S.A", Athens / Aspropyrgos / Paiania
    "hellasdirect",  #  6 — Marousi + Nicosia + Bucharest
    # FR pass, 2026-08-05 via scripts/discover_ats.py, identity-checked against the postings.
    "exotec",        # 72 — Lille / Wasquehal, warehouse-robotics engineering (French)
    "huggingface",   #  7 — Paris + US-remote, ML/open-source
    # GB pass, 2026-08-05. UK gated out post-Brexit; kept for the EEA seats. Identity-checked.
    "withplum",      # 32 — Plum: Nicosia (CY) + Athens (GR) + London
    "yapily",        # 11 — Vilnius (LT) + Málaga (ES) + London
    # IT re-run, 2026-08-05, identity-checked against the postings.
    "moneyfarm",     # 14 — Milan 10 / Cagliari 2 / London 2, wealth management
    # GB on-site revisit, 2026-08-05 (GB now selectable). UK-based, from gb_ats_discovery.csv.
    "starling-bank", # 87 — UK (digital bank)
    "kroo",          # 21 — London / Manchester (bank)
    "zego",          # 33 — London (insurtech); its Ashby board (2 jobs) is not taken
    "zoopla",        #  5 — London (property)
    # DE pass, 2026-08-05, identity-checked.
    "scalable",      #  5 — Munich (Scalable Capital, digital investing)
    # Thin-EEA expansion pass, 2026-08-06, identity-checked against each board's postings.
    "adverity",      #  7 — Vienna (AT) 3 of 7, adtech/analytics
    "m-files",       # 16 — Tampere / Helsinki / Lappeenranta (FI) + Stockholm, doc mgmt
    "fenergocareers",#  8 — Dublin (IE) regtech; Dublin seats among London/NY
    # Expansion pass 2, 2026-08-06 (PT/RO/LV/EE focus), identity-checked against the postings.
    "aerones",       # 50 — Riga (LV) 20 + US wind-ops. Latvian wind-turbine robotics; the
    #                       biggest single LV board found (LV held ~11 active postings).
    "flowxai",       # 25 — Bucharest (RO) 10 + Budapest/Warsaw/Gdańsk. Romanian banking AI.
    # CZ expansion pass, 2026-08-06, identity-checked from the VPS (local IP was 429'd):
    # account name "ROI Hunter", all 4 postings Brno, Czechia (Backend Dev, Product Designer,
    # Product/Campaign Manager, IT Technician). The recruitee `roi` slug is a Berlin impostor.
    "roi-hunter",    #  4 — Brno (CZ) adtech; the bare `roi` recruitee slug is NOT this company.
    # GB expansion pass, 2026-08-07.
    "lyst",          #  5 — Lyst, London (GB), all five. Fashion search/marketplace.
    # NO pass, 2026-08-07.
    "kahoot",        # 11 — Kahoot!, Oslo 4 of 8 + Copenhagen 3 + Barcelona. Best Workable
    #                     board of the run; product, programme and sales roles.
    # Two Workable slugs were **rejected on their own postings**, and both are the same trap:
    # a real company exists at the name, in the wrong country. `boost` is in Wellington, New
    # Zealand — not Boost.ai of Stavanger. `crayon` is three Boston roles — Crayon.co, the US
    # competitive-intelligence firm, not Crayon AS of Oslo. `strise` is genuinely Strise (Oslo)
    # but its only row is an open application, which `_TALENT_POOL` drops anyway.
    # GR/HU/BG/HR/SI/LU/MT/CY pass, 2026-08-07.
    "upstream",      # 17 — Upstream, Athens (GR) 11 of 17 + ZA/BR/NG. Mobile commerce.
    "epignosis",     #  4 — Epignosis (TalentLMS), Athens (GR) 2 + Atlanta/Florida.
    "piraeus-bank",  #  4 — Piraeus Bank, all four Athens (GR).
    "eurobank",      #  1 — **Eurobank's Luxembourg entity**, not its Greek one: the single row
    #                      is a syndicated-loans officer in Neudorf-Weimershof (LU). Kept
    #                      because Luxembourg held 43 postings and the role is ISCO 2.
    # `orfium` (21) was **rejected on inventory** and is the clearest case in this pass: it is
    # genuinely Orfium of Athens, but 20 of its 21 rows are entry-level data-processing
    # associates in Colombo (Sri Lanka) — "Cue sheet processing", "Audio Recognition Services"
    # — for exactly ONE Greek engineering role. `persado` is genuinely Persado but its board is
    # New York and Washington with no Greek row at all.
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
