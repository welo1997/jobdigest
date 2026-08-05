"""Ashby ATS — fetch jobs from company job-board APIs (no key).

Public endpoint: https://api.ashbyhq.com/posting-api/job-board/{org}
Org tokens below are verified-live remote-first / data / AI companies; grow freely.
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

# Verified-live Ashby boards (remote-first / data / AI + fintech / ecommerce). Whole list
# re-probed 2026-08-01: all 18 originals still serve jobs, and the additions below were
# confirmed the same way. A board that goes dark is a silent zero — `fetch` skips a non-200
# and logs nothing at error level — so re-probe rather than assume when volume drops.
#
# Deliberately NOT here despite being live on Ashby: `clickhouse` (already a Greenhouse board
# in dbt/seeds/target_companies.csv) and `qonto` (already a Lever org). Both serve the same
# postings under a second source, and while `digest.dedupe_key` would collapse them in the
# email, they would still occupy two rows and two shortlist slots each.
ORGS = [
    "notion", "openai", "ramp", "cohere", "supabase", "elevenlabs",
    "baseten", "linear", "posthog", "langchain", "modal",
    # fintech / trading / ecommerce
    "plaid", "pleo", "wealthsimple", "mollie", "keyrock", "backmarket", "deliveroo",
    # AI / applied ML, added 2026-08-01
    "harvey", "sierra", "cursor", "synthesia", "lovable", "writer", "abridge",
    "runway", "photoroom", "langfuse", "n8n",
    # data / infra / devtools
    "airbyte", "prefect", "motherduck", "neon", "render", "railway", "resend",
    "vanta", "attio", "tldraw", "dune",
    # EU-based (FR/DE/NL/PT), which the rest of this list is thin on
    "alan", "swan", "tacto", "forto", "choco", "oyster",
    # second wave, verified 2026-08-01 — AI labs and applied-AI, the densest hiring segment
    "perplexity", "cognition", "decagon", "mercor", "suno", "pika", "listenlabs",
    "physicalintelligence", "poolside", "rogo", "legora", "granola",
    # fintech / infra / devtools
    "airwallex", "column", "primer", "unit", "vivid", "warp", "zed", "chromatic",
    # GB-based, which this list had almost none of
    "wayve", "applied",
    # Czech-headquartered, added 2026-08-03 via scripts/discover_ats.py and identity-checked
    # against each board's own postings (Prague / Opatovice locations), not just a live 200.
    # Ashby turned out to be where the Czech product companies actually are — nine of them
    # against Greenhouse's four, which no one would have guessed from the existing lists.
    "gen-digital", "rohlik", "kiwi-com", "apify", "gooddata",
    "braiins", "deepnote", "rossum.ai", "satoshilabs",
    # Polish-headquartered, added 2026-08-04 and identity-checked. Docplanner is a Warsaw
    # company whose board is Barcelona-heavy (13 ES / 8 PL) — the postings are what makes it
    # worth having, not the flag on the head office.
    "docplanner",
    # IT/ES pass, 2026-08-04, identity-checked against each board's own postings.
    "satispay",    # 88 — Milan 45 / Luxembourg 15 / Barcelona 6 / Naples 4. The densest
    #                    genuinely Italian board found in 85 companies.
    "amenitiz",    # 18 — Barcelona 10 / Lisbon 4 / Lyon 2
    # Thin-country pass, 2026-08-04 (210 employers across the 19 selectable countries holding
    # under 300 active postings each). Identity-checked against each board's own postings.
    "nord-security",  # 140 — Vilnius / Warsaw. The largest Lithuanian board found.
    "hostinger",      #  71 — Vilnius / Kaunas
    "eneba",          #  30 — Kaunas + remote
    "supercell",      #  38 — Helsinki / Shanghai
    "reaktor",        #  21 — Helsinki / Tampere / Turku / Amsterdam
    "wayflyer",       #  18 — Dublin / Berlin / Charlotte NC
    "uipath",         # 113 — RO 20 of 113; the rest US/IN/APAC. Romania had 105 active
    #                         postings, so 20 is material even at a 22% EEA share.
    "glia",           #  30 — EE 5 / PL 4 among 13 US. Estonian-founded, US-weighted board.
    # Rejected in the same pass, each on its own postings rather than its name:
    #   `novo`   — New York fintech, not Novo Nordisk. `span` — Span.io of San Francisco,
    #   not Span d.d. of Zagreb (18 US / 12 IN, 0 EEA).
    # FR pass, 2026-08-05 via scripts/discover_ats.py, each identity-checked against its own
    # postings. Ashby is where the French product/AI companies concentrate. Doctolib answers
    # on BOTH Ashby (152) and Greenhouse (155) but is already carried on the greenhouse seed,
    # so neither is added here — the same postings must not occupy two shortlist slots.
    # Rejected on identity: `frontcareers` (Argentina/Chile/SF, 0 EEA), `shift` (Shift
    # Technology, sampled all Sydney/Melbourne), `sweep` (NYC/USA/Tel Aviv).
    "pennylane",   # 116 — Paris / Caen / Bordeaux / remote-EU. The densest French board found.
    "voodoo",      # 103 — Paris 5 of 8 (mobile gaming)
    "dust",        #  23 — Paris (applied-AI)
    "nabla",       #  13 — Paris (health AI)
    "owkin",       #   8 — Paris / UK (bio-AI)
    "ankorstore",  #   4 — Paris
    "sorare",      #   4 — Paris
    # GB pass, 2026-08-05. UK is gated out (post-Brexit), so these are kept for their EEA seats,
    # not their London ones. Identity-checked against the postings. `stackfuel` is the Berlin
    # data-training company Multiverse acquired — its board is entirely Berlin, which is the
    # EEA inventory that made it worth taking over Multiverse's UK-only board.
    "stackfuel",   #  23 — Berlin (Multiverse DE)
    "trainline",   #  48 — London + Paris; kept for Paris + any EU-remote
    "quantexa",    #  32 — Munich among London/Singapore/NY
    "causaly",     #   9 — Athens + London (Greek inventory is thin)
    # US pass, 2026-08-05. US is gated out, so kept for the EEA seat only. Verified against the
    # boards: snowflake has a Warsaw hub (9 of 396) plus scattered EU; sentry has Vienna. The
    # US-dominant Workday medical/apparel giants (Abbott/J&J/Amgen/Medtronic/Nike) were skipped
    # as N+1 budget hogs. ashby:temporal was skipped — Temporal is already carried on greenhouse.
    "snowflake",   # 396 — Warsaw + EU among US/IN. Cheap board, so the US rows cost nothing.
    "sentry",      #  48 — Vienna (AT) among San Francisco
    # PL re-run, 2026-08-05, identity-checked. Rejected the documented impostors again:
    # greenhouse:ideo (US design firm), greenhouse:sas (Canoga Park), lever:blue (LatAm).
    "zowie",       #   9 — Warsaw (AI customer-service), genuinely Polish
    "preply",      # 117 — Barcelona 60 / Kyiv / London. Listed under PL but it is the
    #                      Barcelona inventory (ES) that makes it worth carrying.
    # US-HQ pass, 2026-08-05 (US now selectable). Identity-checked. For Chainalysis, only the
    # main `chainalysis-careers` board is taken, not its `-government-solutions` sibling; and
    # Anyscale is taken on Ashby (19), not its 1-job Lever board — one company, one board.
    "clickup",     #  65 — US (project management)
    "docker",      #  58 — US / Canada
    "miro",        #  46 — Amsterdam / Copenhagen / Tokyo (EEA + global)
    "chainalysis-careers",  # 46 — DC / New York / Germany (blockchain analytics)
    "astronomer",  #  29 — New York (data orchestration)
    "hex",         #  28 — New York / San Francisco (analytics)
    "anyscale",    #  19 — San Francisco / Bengaluru (Ray / LLM infra)
    "hims-and-hers",  # 102 — New Albany OH; a lot of facilities roles, real US employer
    "montecarlodata", # 6 — London / New York / San Francisco (data observability)
    # GB on-site revisit, 2026-08-05. These UK-based boards were found in the GB pass but
    # SKIPPED while GB was gated out (UK-onsite reached nobody). Now GB is selectable, so a UK
    # subscriber wants exactly these. From the same gb_ats_discovery.csv, identity-checked.
    # Still skipped: cleo/immersivelabs/tractable (US), nium (APAC), zego's 2-job Ashby board
    # (its 33-job Workable board is taken instead), bloom/healx (ambiguous/tiny).
    "lendable",       # 63 — London (consumer lending)
    "faculty",        # 62 — London / UK-remote (applied AI)
    "allica-bank",    # 39 — London / Milton Keynes / Manchester (SME bank)
    "thought-machine",# 40 — London-majority (core banking)
    "oaknorth",       # 28 — London (SME bank)
    "paddle",         # 21 — UK (payments/SaaS)
    "marshmallow",    # 17 — London (insurtech)
    "genomics",       # 13 — London (Genomics England)
    "zilch",          # 13 — UK (BNPL)
    "improbable",     #  9 — London + international-remote
    "freetrade",      #  8 — London (investing)
    "griffin",        #  3 — London / remote-UK (banking-as-a-service)
    "skyscanner",     # 13 — Edinburgh / Glasgow / London (travel)
    # DE pass, 2026-08-05, identity-checked (see the note in target_companies.csv for the
    # generic-slug impostors rejected here: eon, clark).
    "deepl",          # 49 — Cologne / Berlin (translation AI)
    "pliant",         # 43 — Berlin (corporate cards)
    "camunda",        # 40 — Berlin (process orchestration)
    "moss",           # 35 — Berlin / Amsterdam (spend management)
    "tourlane",       #  9 — Berlin (travel)
    "alephalpha",     #  2 — Heidelberg (sovereign AI); NOT lever:aleph, which is an ad agency
    "babbel",         #  2 — Berlin (language learning)
]

# Map Ashby address country names to ISO-3166 alpha-2.
_COUNTRY = {
    "united states": "US", "united kingdom": "GB", "germany": "DE",
    "netherlands": "NL", "france": "FR", "spain": "ES", "ireland": "IE",
    "poland": "PL", "portugal": "PT", "canada": "CA", "czechia": "CZ",
}


class AshbySource(BaseSource):
    """Ashby public job-board API across a curated list of orgs."""

    def __init__(self, orgs: Optional[list[str]] = None):
        self._orgs = orgs or ORGS

    @property
    def source_name(self) -> str:
        return "ashby"

    def fetch(self) -> list[dict]:
        all_jobs: list[dict] = []
        for org in self._orgs:
            try:
                resp = requests.get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                    headers=HEADERS, timeout=15,
                )
                if resp.status_code != 200:
                    continue
                for j in resp.json().get("jobs", []):
                    j["_org"] = org
                    all_jobs.append(j)
            except requests.RequestException as exc:
                logger.warning("Ashby %s failed: %s", org, exc)
        logger.info("Ashby: fetched %d jobs across %d orgs", len(all_jobs), len(self._orgs))
        return all_jobs

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            if item.get("isListed") is False:
                continue
            url = item.get("jobUrl") or item.get("applyUrl") or ""
            if not url:
                continue
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=item.get("_org"),
                    url=url,
                    description=item.get("descriptionHtml") or item.get("descriptionPlain"),
                    location=item.get("location"),
                    country_code=self._country(item),
                    remote_signal=bool(item.get("isRemote")),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse(item.get("publishedAt")),
                )
            )
        logger.info("Ashby: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _country(item: dict) -> Optional[str]:
        try:
            name = item["address"]["postalAddress"]["addressCountry"]
            return _COUNTRY.get(str(name).strip().lower())
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _parse(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None
