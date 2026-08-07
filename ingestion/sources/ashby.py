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
    # Thin-EEA expansion pass, 2026-08-06 (scripts/thin_expand_companies.csv — new employers
    # in the under-300-posting selectable countries). Identity-checked against each board's
    # own postings' cities, not a live 200. Rejected in the same pass: ashby:xero (Planday's
    # board redirects to Xero corporate — all AU/NZ/CA, 0 EEA), ashby:krakentech (Octopus's
    # Kraken arm, London/Tokyo/NY, not the Austrian firm the name guessed).
    "iceye",          # 158 — Espoo (FI) satellite radar; strong Finnish engineering board
    "corti",          #   7 — Copenhagen (DK) health-AI, 6 of 7 Danish
    # AT pass, 2026-08-07 — bycatch, and worth saying so. Speedinvest's careers page links its
    # **portfolio's** boards, so probing the Vienna VC surfaced companies that are not Austrian
    # at all. Kept only where the postings are in selectable countries; `greenhouse:dialpad`
    # (90, Austin/Tempe/Tokyo/Bengaluru/Manila) was skipped as N+1-style non-EEA bulk, and
    # `ashby:julius` — probed for Julius Meinl — is a San Francisco startup, rejected.
    "trawa",          #  12 — trawa, Berlin (DE) 7 of 8 + Amsterdam (NL) — energy procurement
    "primer.io",      #  38 — Primer, London/UK 4 of 8 + remote-Europe 2, Singapore tail.
    #                       NOT `primer`, already carried and a *different* company — a US
    #                       microschool operator posting Florida and Alabama teacher roles.
    # IT expansion pass, 2026-08-07, identity-checked against each board's own postings.
    # Rejected here: `greenhouse:indigo` — probed for Indigo.ai of Milan, it is a US insurer
    # (AVP Facilities Underwriting, Underwriter, all Remote). And `ashby:serenis` was **live,
    # genuinely Serenis, genuinely Italian — and skipped anyway**: all five postings are
    # clinical practitioners (psychotherapists, a psychiatrist, a sexologist), which no
    # taxonomy category models, so they would land in the widened retrieval path that drops
    # the recall predicate. Real inventory for a product this is not.
    "docebo",         #  53 — Docebo, Toronto 5 of 8 but Biassono + Milan (IT) 2. Canadian-
    #                       weighted; taken for the Italian rows, which Ashby costs nothing for.
    "kong",           #  91 — Kong Inc, Milan (IT) + US/Canada/India/Australia. US is
    #                       selectable now, so the US bulk is wanted rather than waste.
    # GB expansion pass, 2026-08-07, identity-checked. Rejected in the same pass:
    # `greenhouse:turing` (24) is **Turing.com**, the US AI-talent marketplace (New York,
    # Palo Alto, Bengaluru, São Paulo), not the Alan Turing Institute — and `ashby:alan` is
    # already carried and is the French health insurer, so the Institute has no board here.
    "beamery",        #   8 — Beamery, London (GB) 7 of 8 + Austin. Talent-CRM software.
    # NL pass, 2026-08-07, identity-checked. Skipped: `recharge` (3) is a US/Canada company, my
    # own list error, and `greenhouse:asm` (429) is ASM's China/Singapore/Taiwan board with 0
    # EEA rows in 8 sampled — Greenhouse is cheap, but not free of shortlist slots.
    "bynder",         #  24 — Bynder, Amsterdam 3 + Rotterdam (NL) + Düsseldorf, London, Boston
    "eye-security",   #  23 — Eye Security, The Hague (NL) 3 + Germany 3 + Berlin + Austria.
    #                        Cyber-insurance/SOC; one of the better EEA spreads on this list.
    "datasnipper",    #  29 — DataSnipper, Amsterdam (NL) 3 of 8 + New York 3
    # NO pass, 2026-08-07. Both are Oslo-founded and both boards are hired *away* from Oslo —
    # recorded because it is the honest shape of Norwegian scale-up inventory, not a reason to
    # skip them: GB/US rows are wanted now that both countries are selectable.
    "sanity",         #  25 — Sanity.io (Oslo-founded): "Remote in the United States" 5 of 8 +
    #                        London. Headless CMS; engineering and go-to-market.
    "gelato",         #   8 — Gelato (Oslo-HQ): London 5 of 8 + Stockholm, Atlanta, Denver.
    # GR/HU/BG/HR/SI/LU/MT/CY pass, 2026-08-07. Hungary had never had a dedicated pass.
    "seon",           #   6 — SEON, Budapest (HU) 3 of 6 + London, US-remote. Fraud prevention.
    "shapr3d",        #   4 — Shapr3D, Budapest (HU) 4 of 4. CAD; C++ engineers.
    # **Six one-word Ashby slugs in this pass were somebody else** — the densest run of
    # collisions yet, and every one was caught by the board's own postings rather than by its
    # name. `span` is Span.io of San Francisco, not Span d.d. of Zagreb (already on record from
    # 2026-08-04 and it answered again). `titan` is a New York wealth-management/fintech, not
    # Titan Cement of Athens. `genesis` is a Bay Area robotics lab ("Foundation Models",
    # "Robot Learning"), not Genesis Pharma — and `lever:genesis` is a *third* company again,
    # in Bengaluru. `catena` is US-remote, not Catena Media of Malta. `post` is four remote
    # engineering roles, not POST Luxembourg. `wizz` is Paris creative/video work, not Wizz Air
    # of Budapest.
    # PT/LT/EE pass (batch B), 2026-08-07.
    "surfshark",      #  22 — Surfshark, Vilnius (LT) 21 of 22 + Warsaw. VPN/Incogni. Kept
    #                        alongside `nord-security` (the two merged in 2022 but run separate
    #                        boards, and these 22 rows are Incogni-branded, not Nord's).
    # `light`, probed for Light Conversion of Vilnius, is eight Austin, Texas roles — another
    # one-word slug that is a different company. And **`bite` answered again**: it is the same
    # London founding-engineer board rejected in the Latvian pass hours earlier, and it is not
    # Bitė Lietuva any more than it was Bitė Latvija.
    # CY/LU/MT/IS/LI pass (batch C), 2026-08-07.
    "leovegasgroup",  #  73 — LeoVegas Group: Malta 28 + Leeds, Málaga, Stockholm, Warsaw.
    #                        Malta held 23 postings before this pass; this board alone doubles
    #                        it, and it also carries the first Canadian rows CA can now receive.
    "tempo-io",       #  21 — Tempo (Reykjavík-founded): Spain, Canada, US. **Findable only via
    #                        the careers-page read path** — `slug_candidates("Tempo","tempo.io")`
    #                        returns just `tempo`, and `greenhouse:tempo` is a San Diego
    #                        receptionist at a different company.
    # `odyssey` is New York and US-remote, not Odyssey Cybersecurity of Nicosia. And
    # `ashby:gr8-tech` was rejected in favour of its Greenhouse twin for a reason worth keeping:
    # its board carries literal ".NET Developer Test" and "test Job title" rows — an ATS being
    # trialled, not a board being run.
    # SE pass, 2026-08-07.
    "epidemic-sound", #  18 — Epidemic Sound: "Stockholm HQ" 5 of 8 + London, NYC, Berlin.
    "trustly",        #  23 — Trustly (Stockholm-HQ) and the board is not: San Francisco 4,
    #                        London 2, New York, Lisbon. Taken over `lever:trustly` (22, the
    #                        same jobs) — one company, one board.
    # Rejected: `atlas` is San Francisco/New York, not Atlas Copco; `paradox` is Dubai/Paris,
    # not Paradox Interactive of Stockholm; `resolution` is Berkeley, California, not Resolution
    # Games. Three generic one-word slugs, three different companies — the `norr` lesson.
    # DK/CH/FI/IE pass, 2026-08-07.
    "toogoodtogo",    #  70 — Too Good To Go: Copenhagen, Stockholm 2, Berlin, Warsaw 2,
    #                        Amsterdam, Tokyo. Taken over `greenhouse:toogoodtogo` (69).
    "frontify",       #  15 — Frontify: "Sankt Gallen Metropolitan Area" 4 + London 4 — one of
    #                        the few genuinely Swiss boards found. Taken over `lever:frontify`.
    # Rejected: `icon` is New York/Remote, not ICON plc of Dublin; `lunar` is San Francisco and
    # Newcastle, not Lunar the Danish bank (`teamtailor:lunar` is); `julius` is four San
    # Francisco software-engineer roles, not Julius Baer; `stora` is three GB product roles, not
    # Stora Enso.
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
