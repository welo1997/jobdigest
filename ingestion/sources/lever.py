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
ORGS = ["spotify", "qonto", "contentsquare", "swile", "blablacar",
        "zopa", "moonpig", "moonpay", "vestiairecollective",
        # added 2026-08-01, FR/PT/EE-heavy
        "farfetch", "malt", "agicap", "360learning", "pipedrive",
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
        "deliverect",     # 26 — Amersfoort + Ghent HQ. Belgian, but only 2 of 26 rows are
        #                        BE (the rest Mexico City / Casablanca) — kept because it is
        #                        genuine and small, not because it fills Belgium.
        # FR pass, 2026-08-05 via scripts/discover_ats.py, each identity-checked against its
        # own postings' locations. `pigment` is Americas-heavy (NY/Toronto/SF) with a London
        # and a Paris seat — kept because Lever is cheap and the geo gate simply never
        # retrieves its US rows for an EEA subscriber. Rejected here: lever:playplay (1 job),
        # and recruitee:memo which is a Belgian firm in Gent/Zele, NOT Memo Bank of Paris.
        "veepee",         # 88 — Saint-Denis / Saint Vulbas, French e-commerce
        "gojob",          # 85 — Aix-en-Provence / Paris, all France
        "verkor",         # 38 — Grenoble / Bourbourg, French battery gigafactory
        "doctrine",       # 27 — Paris + Düsseldorf/Milan sales
        "brevo",          # 21 — Paris (ex-Sendinblue)
        "younited",       # 7 — Paris + Barcelona/Rome
        "pigment",        # 99 — mostly US/CA, London + Paris the only EEA seats
        # GB pass, 2026-08-05. The UK is gated out (post-Brexit, not in the selectable set), so
        # these are kept for their EEA/remote roles, not their UK ones. Identity-checked.
        "octoenergy",     # 145 — Octopus Energy's EU arm: Valencia/Munich/Berlin/Verona +
        #                         Worldwide-remote. The strongest EEA board in the GB run.
        "sophos",         # 110 — cybersecurity, Germany seat + global remote among UK/US/ZA
        # US-HQ pass, 2026-08-05 (US now selectable). Identity-checked against the postings.
        "includedhealth", # 143 — US health navigation
        "sysdig",         #  12 — cloud security, US + global remote
        # GB on-site revisit, 2026-08-05 (GB now selectable).
        "moneyboxapp",    #  10 — London (saving/investing app)
        # DE pass, 2026-08-05, identity-checked.
        "finn",           #  32 — Munich (car subscription); NOT lever:aleph/zeiss (impostors)
        # Thin-EEA expansion pass, 2026-08-06, identity-checked against each board's postings.
        "nfq",            #  15 — Vilnius / Kaunas (LT) + Kraków; Lithuanian/Polish software
        "siteground",     #   2 — Sofia (BG); small but purely Bulgarian
        # FR expansion pass, 2026-08-07, identity-checked against each board's own postings.
        # France's non-ATS routes are all closed: France Travail's API is permitted but
        # declined on Art. 5.3/Art. 7 (unchanged), APEC forbids "capter tout ou partie du
        # contenu … et des bases de données qui lui sont liées (offres d'emploi …)", HelloWork
        # forbids scraping "à quelques fins que ce soit, commerciales ou non", and Welcome to
        # the Jungle forbids "extraction automatisée de données". Curated employers is the route.
        # `aircall` and `ledger` were here and were REMOVED 2026-08-08, and the pair is
        # the lesson: both boards' APIs still answer (aircall with 77 postings), and both
        # boards are **gone** — `jobs.lever.co/aircall` and `jobs.lever.co/ledger` are 404 at
        # the root, so every posting link 404s too. Found by `scripts/check_links.py` at full
        # board coverage; six of six probed aircall links were dead.
        #
        # They were replaced rather than dropped, which is the part that matters. Both
        # companies were already known on another ATS and had been "skipped as duplicates" in
        # favour of these Lever boards — greenhouse:aircallioinc (64 live, links render) and
        # ashby:ledger (9 live). So the employers moved ATS and we were left holding the old
        # board. **When a board dies, check whether the duplicate that was skipped for it is
        # now the live one**; deleting alone would have cost 73 real postings.
        "scaleway",       #  34 — Paris 6 of 8 + Lille + Milan; French cloud (Iliad)
        "aqemia.com",     #   6 — Paris 5 of 6 (drug-discovery AI). The slug really does carry
        #                        the `.com`, which no name-derived guess would produce.
        # GB expansion pass, 2026-08-07. Both are UK-headquartered with US-weighted boards;
        # Lever costs one request, and the US rows are wanted since GB and US are both
        # selectable. Identity-checked against the postings.
        "bumbleinc",      #  22 — Bumble: New York 5 of 8 / Austin 2 / London 1
        "matillion",      #  22 — Matillion (Manchester HQ): US territories + Manchester
        # NO pass, 2026-08-07. Norway held 23 active postings in the whole corpus, 22 of them
        # from one board found by accident, so this was the first deliberate Norwegian sweep.
        "bekk",           #  22 — Bekk, Oslo 6 of 8 + Trondheim 2. **The best board of the run**
        #                        and the only one that is 100% Norwegian: "Erfaren utvikler",
        #                        "Erfaren data engineer", "Digital forretningsutvikler" — a
        #                        consultancy, so the whole board is ISCO 1-2 by construction.
        "netlight",       #  35 — Netlight: Stockholm 3 of 8 + Amsterdam, Munich 2, Copenhagen,
        #                        Cologne. Nordic/DACH consultancy, entirely EEA, all consulting
        #                        and in-house engineering. `lever:dnb` from the same pass was
        #                        **rejected — it is Dun & Bradstreet**, not Norway's largest
        #                        bank: 133 rows of "R-19586"-style requisitions in Dublin,
        #                        Warsaw and Jacksonville. A three-letter slug is an initialism
        #                        before it is a company.
        # SE pass, 2026-08-07.
        "avalanchestudios",
        #                 16 — Avalanche Studios, Stockholm 8 of 8. Art direction, DevOps/AI,
        #                      network programming, QA — game development, all in Sweden.
        # DK/CH/FI/IE pass, 2026-08-07.
        "cartrawler",     # 11 — CarTrawler, Dublin 7 of 8 + London. One of very few genuinely
        #                      Irish boards found, and Ireland has neither a register we may
        #                      read (DSP requires written permission) nor an Adzuna index.
        # PT/LT/EE pass (batch B), 2026-08-07.
        "omnisend",       # 11 — Omnisend, Vilnius (LT). E-commerce marketing automation.
        "metasite",       #  3 — Metasite, Vilnius (LT). .NET/Java engineering.
        # CY/LU/MT/IS/LI pass (batch C), 2026-08-07.
        "capital"]        # 43 — Capital.com: Warsaw (PL) 20, Limassol (CY) 5, Sofia (BG) 4.
        #                      A one-word slug that IS the company, which is worth recording
        #                      next to all the ones that are not.



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
                    scope_raw=self._scope(cats),
                )
            )
        logger.info("Lever: normalised %d postings (%d naming more than one location)",
                    len(postings), sum(1 for p in postings if p.scope_raw))
        return postings

    @staticmethod
    def _scope(cats: dict) -> Optional[str]:
        """`categories.allLocations` — every place the posting is open in, not just the primary.

        `categories.location` is one string and was the only field read, so a role open across
        several countries was stored as whichever one Lever put first. Lever writes the entries
        with their arrangement attached ("United Kingdom - Remote", "Spain - Remote", "Poland -
        Remote"), which is a scope statement rather than an office list — a MoonPay posting
        measured on 2026-08-15 named six countries this way and was stored as the United Kingdom.

        Only emitted for two or more entries: a single `allLocations` is `location` verbatim.

        **Not folded into `location`**, per `ashby._scope` — the secondaries would outrank the
        real city in `geo.resolve_location`. Note that a multi-entry list is *not* by itself a
        remote scope: most of them are an employer's office pair (Spotify runs "Stockholm,
        London" on hybrid and on-site roles alike), which is why `geo` computes reach only for
        rows that are fully remote on their own words.
        """
        parts: list[str] = []
        for loc in cats.get("allLocations") or []:
            name = str(loc or "").strip()
            if name and name.casefold() not in {p.casefold() for p in parts}:
                parts.append(name)
        return "; ".join(parts) if len(parts) > 1 else None

    @staticmethod
    def _parse_ms(ms: Optional[int]) -> Optional[date]:
        if not ms:
            return None
        try:
            return datetime.utcfromtimestamp(int(ms) / 1000).date()
        except (ValueError, OSError, OverflowError):
            return None
