"""Recruitee ATS — public job-board API for a curated list of companies (no key).

Public endpoint: ``https://{company}.recruitee.com/api/offers/``

Recruitee is where the mid-size Czech employers turned out to be. The five ATSes this repo
already read are dominated by US and Western-European companies; when
`scripts/discover_ats.py` probed 129 Czech companies, the Czech-headquartered hits that had
**no adapter at all** clustered here — STRV, Trask, Twisto and Livesport, all Prague or
Mladá Boleslav. That is not a large number of postings, but it is exactly the segment the
corpus lost when Jobs.cz and Profesia were excluded: Czech companies hiring in Czech cities.

Every company below was identity-checked against its own board rather than accepted on a
live HTTP 200, because a guessed slug that resolves proves only that *somebody* owns it.
`kb` (for Komerční banka) turned out to be the **Dutch national library** in Den Haag, and
`accenture` a sample board in Amsterdam. Both would have passed a job-count check silently.

Unlike the scrapers, the workplace flags here are structured fields the employer set
(`remote`, `hybrid`, `on_site`), not substrings mined out of graded Czech prose — so
`remote_signal` may be taken from `remote` directly, the same trust `lever`'s
`workplaceType` gets. `country_code` is likewise per-posting and real; there is no
source-level country constant here, and there should not be one — Recruitee is a Dutch
product and these boards carry non-CZ offices too.
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

#: Verified-live Recruitee boards, confirmed 2026-08-03 against each board's own postings
#: (`company_name` + `city`), not merely a 200. Grow from `scripts/discover_ats.py` output.
COMPANIES = [
    "strv",           # 5 — Prague, software studio
    "trask",          # 5 — Mladá Boleslav / Prague, IT consultancy
    "twisto",         # 3 — Prague, fintech
    "livesportsro",   # 2 — Prague, Livesport s.r.o. (Flashscore)
    "lundegaard",     # 1 — Praha, "Lundegaard a.s." on its own postings
    # Genuine company boards, but Dutch entities rather than Czech — kept because subscribers
    # choose countries and NL is one of them, not because they help the CZ gap.
    "konicaminolta",  # 11 — "Konica Minolta Business Solutions", Gouda NL
    # Polish, added 2026-08-04 from scripts/pl_ats_discovery.csv, each identity-checked
    # against its own postings rather than a live 200 — which is what caught four boards
    # that answered but belonged to nobody (see the note below).
    "tylko",          # 13 — Warszawa (+ Amstelveen), furniture ecommerce
    "espeo",          #  6 — Poznań, software house
    "11bitstudios",   #  3 — Warszawa, games (This War of Mine, Frostpunk)
    # SK/IT/ES pass, 2026-08-04. Same identity check: company name and city off the postings.
    "goodrequest",    #  1 — Žilina, Slovakia. The *only* Slovak board found in 84 companies.
    "holded",         # 11 — Barcelona, business software
    "delonghigroup",  #  3 — De'Longhi, Neu-Isenburg DE + Wiener Neudorf AT
    # Thin-country pass, 2026-08-04. Two more demo-content slugs were caught here and are in
    # KNOWN_IMPOSTORS below: `alpha` (Alpha Bank) and `redbull` (Red Bull).
    "colruytgroup",   #  8 — Halle, Belgium. Colruyt Group on its own postings.
    "chaos",          # 11 — Sofia, Bulgaria (+ Karlsruhe). Chaos, the V-Ray developer.
    # PL re-run, 2026-08-05. Three genuine Polish games studios, each identity-checked against
    # its own postings (company name AND city), because recruitee slugs collide: `dpd` here is
    # the Dutch DPD, not DPD Polska, and stayed rejected.
    "tensquaregames", #  9 — Ten Square Games, Wrocław (data science / game design)
    "huuuge",         #  8 — Huuuge Games, Warszawa / Szczecin
    "blooberteam",    #  3 — Bloober Team SA, Kraków + remote
    # IT re-run, 2026-08-05, identity-checked: company name Mia-Platform, Milano.
    "miaplatform",    #  5 — Mia-Platform, Milano (+ London), cloud-native platform
    # DE pass, 2026-08-05, identity-checked: company name Adjust, Amstelveen/Berlin. Rejected
    # recruitee:personio, whose board is company "FD Sandbox" (Recruitee demo content).
    "adjust",         # 29 — Adjust (adtech), Amstelveen NL + Berlin
    # Thin-EEA expansion pass, 2026-08-06, each identity-checked against its own postings.
    "aikidosecurity", # 61 — Ghent (BE) code-security; 4 of 8 sampled Ghent
    "lansweeper",     # 26 — Ghent (BE) HQ; US-weighted board but genuine, recruitee is cheap
    "pvcase",         # 15 — Vilnius (LT) solar-design; all remote-tagged
    "deepsea",        #  2 — Deepsea (GR) maritime AI, remote engineering roles
    # CZ expansion pass, 2026-08-06. Genuine Czech product companies, each identity-checked
    # against its own postings (company name + city), the exact segment lost with Jobs.cz.
    "shoptet",        #  5 — Praha, e-commerce platform (all 5 Prague: PHP/Node, Shoptet Pay)
    "confirmo",       #  7 — Prague crypto-payments; remote-heavy (DevOps, Java) + Dubai. The
    #                       `roi` slug for Roi Hunter was rejected here — it is a Berlin company.
    "safetica",       #  2 — Brno data-loss-prevention security; Prague + Bogotá on the board
]

#: Slugs that answer with a live board which is **not the company whose name they spell**.
#: Recruitee refuses an invented slug outright — `zzz-not-a-company-9174` gets no answer at
#: all — so these are real registered accounts, which is exactly why probing by name is not
#: verification. `accenture`, `ccc`, `samsung` and `wp` each returned one or two Amsterdam or
#: Berlin postings titled "Senior Marketer (Sample)" / "(Muster)": Recruitee's own demo
#: content sitting on an unclaimed vanity slug. Adding any of them would have put a fake
#: marketing job into a Polish subscriber's digest under a household brand name.
#: `ey` was in this list until 2026-08-04 for the same reason — three Amsterdam offers, one
#: of them literally "Senior Marketer (Sample)", and no evidence it was EY's board.
#:
#: The SK/IT/ES pass added two of a second kind, which are not demo content but a *different
#: real company* answering on a name-derived slug: `talent` is **Sysmex Iberia** (Barcelona),
#: not Talent Garden, and `clarity` is **Clarity Travel** (Manchester), not Clarity AI —
#: whose genuine board is `clarityai` on Greenhouse. Both would have been filed under the
#: wrong employer in a subscriber's digest, which is the same harm by a different route.
#:
#: The thin-country pass, 2026-08-04, added two more of the demo-content kind, and they are
#: the reason this list keeps growing rather than settling: the discovery CSV proposes them
#: afresh every run, each with a plausible job count beside it. `alpha` (probing for Alpha
#: Bank, Greece) is two Amsterdam postings titled "Recruiter (Sample)" and "Senior Marketer
#: (Sample)". `redbull` is one posting, "Senior Marketer (Sample)", filed under the company
#: name **"Fiture Marketing"** — the board itself says it is not Red Bull, which is exactly
#: the field a job count does not show you.
KNOWN_IMPOSTORS = frozenset({
    "accenture", "ccc", "samsung", "wp", "ey", "talent", "clarity",
    "alpha", "redbull",
})

_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: Recruitee's own demo postings, which ship inside otherwise genuine boards.
#:
#: `trask` is a real Czech consultancy with real Prague roles, and three of its five offers
#: on 2026-08-04 were "Senior Marketer (Sample)", "Recruiter (Sample)" and "Pracovní template
#: - Freelancer" — seed content a customer never deleted. They were being ingested, scored and
#: made emailable like any other job, and a subscriber receiving "Recruiter (Sample)" at a
#: named employer is a product that looks broken.
#:
#: Deliberately narrow. It matches Recruitee's own bracketed marker and the word `template`,
#: both of which are unambiguous, and nothing else. It does **not** touch open-application
#: placeholders ("Nevidíš svoji pozici?" at livesport, "Didn't find a suitable position" at
#: Brainly) or talent-pool ads ("Blockchain Developer - Talent Pool" at Espeo): those are
#: posted deliberately by the employer, a subscriber may genuinely want to answer one, and
#: guessing at their phrasing across eight languages is how a filter starts deleting real
#: jobs. The rule for this repo's classifiers holds here too — the only safe error is a miss.
_DEMO_TITLE = re.compile(r"\((?:sample|muster|voorbeeld)\)|\btemplate\b", re.I)


def _text(html: Optional[str]) -> Optional[str]:
    """Plain text from Recruitee's HTML description fields."""
    if not html:
        return None
    out = _WS.sub(" ", _TAG.sub(" ", html)).strip()
    return out or None


def _salary(offer: dict) -> tuple[Optional[str], Optional[str]]:
    """``(salary_raw, currency)`` from Recruitee's structured salary object."""
    sal = offer.get("salary")
    if not isinstance(sal, dict):
        return None, None
    lo, hi, cur = sal.get("min"), sal.get("max"), sal.get("currency")
    period = sal.get("period")
    if not (lo or hi):
        return None, None
    span = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi)
    return f"{span} {cur or ''}/{period or ''}".strip("/ ").strip(), cur


def _posted(offer: dict) -> Optional[date]:
    raw = offer.get("published_at") or offer.get("created_at")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(str(raw), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(raw).replace(" UTC", "+00:00")).date()
    except ValueError:
        return None


class RecruiteeSource(BaseSource):
    """Recruitee public job boards across a curated list of companies."""

    def __init__(self, companies: Optional[list[str]] = None):
        self._companies = companies or COMPANIES

    @property
    def source_name(self) -> str:
        return "recruitee"

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        for company in self._companies:
            url = f"https://{company}.recruitee.com/api/offers/"
            try:
                politeness.throttle(url)
                resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    # Loud, unlike most adapters here: this list is short and hand-curated,
                    # so a board going dark is a meaningful loss rather than routine noise —
                    # and a silent zero is the failure this repo keeps rediscovering.
                    logger.warning("Recruitee %s: HTTP %s", company, resp.status_code)
                    continue
                offers = resp.json().get("offers", [])
                for offer in offers:
                    offer["_company"] = company
                out.extend(offers)
                logger.info("Recruitee %s: %d", company, len(offers))
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Recruitee %s failed: %s", company, exc)
        return out

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        for offer in raw_items:
            url = offer.get("careers_url")
            title = (offer.get("title") or "").strip()
            if not url or not title:
                continue
            if _DEMO_TITLE.search(title):
                logger.info("Recruitee %s: skipping demo posting %r",
                            offer.get("_company"), title)
                continue
            salary_raw, currency = _salary(offer)
            description = _text(offer.get("description"))
            requirements = _text(offer.get("requirements"))
            if requirements:
                description = f"{description}\n\n{requirements}" if description else requirements
            country = (offer.get("country_code") or "").strip().upper() or None
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                company=offer.get("company_name") or offer.get("_company"),
                url=url,
                description=description,
                location=offer.get("location") or offer.get("city"),
                country_code=country,
                # Structured employer-set flag, not a substring dug out of prose — the same
                # basis on which Lever's `workplaceType` is trusted. `hybrid` is deliberately
                # NOT folded in: hybrid is a commute, and treating it as remote would exempt
                # it from the location gate.
                remote_signal=bool(offer.get("remote")),
                salary_raw=salary_raw,
                currency=currency,
                posted_at=_posted(offer),
                source_category=offer.get("category_code"),
            ))
        return out
