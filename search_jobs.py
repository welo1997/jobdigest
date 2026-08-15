"""On-demand remote-international data-job search — no Snowflake, no automation.

Fetches from free public job sources, keeps remote data roles that are plausibly
EU-eligible, dedups, and writes a ranked candidate list to JSON for scoring/curation
in chat. Because it fetches live each run, results are always current — no stale-row
or warehouse cost.

Usage:
    python search_jobs.py                 # fetch international remote data roles
    python search_jobs.py --check-live    # also HTTP-probe each URL (slower)
    python search_jobs.py --cz            # also include Czech sources
    python search_jobs.py --freelance     # keep only freelance/contract data roles
    python search_jobs.py --limit 60      # cap output

Every candidate is tagged with a ``work_type`` (freelance/contract vs permanent),
detected from the title + description, so freelance opportunities that already flow
through the remote boards (Himalayas "Contractor", Lemon.io / A.Team / Toptal network
ads, contract postings on Remotive/RemoteOK/WeWorkRemotely) surface distinctly.

Adzuna is included only if ADZUNA_APP_ID / ADZUNA_API_KEY are set (1Password / env).
Output: candidates.json (+ a printed summary).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Optional

from ingestion.base import JobPosting
from ingestion.sources.arbeitnow import ArbeitnowSource
from ingestion.sources.ashby import AshbySource
from ingestion.sources.goldencareers import GoldenCareersSource
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.himalayas import HimalayasSource
from ingestion.sources.jobicy import JobicySource
from ingestion.sources.lever import LeverSource
from ingestion.sources.nav import NavSource
from ingestion.sources.nva import NvaSource
from ingestion.sources.oraclecloud import OracleCloudSource
from ingestion.sources.platsbanken import PlatsbankenSource
from ingestion.sources.remoteineurope import RemoteInEuropeSource
from ingestion.sources.remoteok import RemoteOKSource
from ingestion.sources.remotive import RemotiveSource
from ingestion.sources.themuse import TheMuseSource
from ingestion.sources.werkenvoornederland import WerkenVoorNederlandSource
from ingestion.sources.weworkremotely import WeWorkRemotelySource
from ingestion.sources.workday import WorkdaySource
from ingestion.sources.workingnomads import WorkingNomadsSource

logger = logging.getLogger(__name__)

# --- Data-role classification (Python mirror of stg_job_postings role_category) ---
_DATA_PATTERNS = (
    "data engineer", "analytics engineer", "dataops", " etl",
    "data analyst", "bi analyst", "business intelligence",
    "machine learning", "ml engineer", "ai engineer", "data scientist",
)


def is_data_role(title: Optional[str]) -> bool:
    t = (title or "").lower()
    return any(p in t for p in _DATA_PATTERNS)


# --- Freelance / contract detection ---------------------------------------------
# Positive signals for freelance/contract work. Kept broad but guarded against the
# "smart contract" (blockchain) false-positive that pollutes naive keyword matches.
_FREELANCE_PATTERNS = (
    "freelance", "freelancer", "contractor", "contract role", "contract position",
    "contract basis", "on a contract", "fixed-term", "fixed term", "interim",
    "day rate", "daily rate", "/day", "per day", "b2b", "self-employed",
    "self employed", "independent consultant", "ičo", "outside ir35", "inside ir35",
    "6-month contract", "12-month contract", "month contract", "gig", "project-based",
    "project based", "temporary contract",
)
# Bare "contract" is only trusted when not part of "smart contract" / "contract address".
_BARE_CONTRACT = re.compile(r"\bcontract(?:s|or|ors|ing)?\b")
_CONTRACT_FALSE = re.compile(r"smart contract|contract address|contract law")


# --- Seniority detection -------------------------------------------------------
#
# **Six levels and an honest null.** Measured against the live corpus on 2026-08-12
# (128 080 active postings, `notes/2026-08-12-seniority-ladder.md`): 70.7% of titles name no
# level at all, so *unknown* is the majority answer and this function must be able to return
# it. The previous three-value scheme could not — it fell through to `mid`, and the stored
# `mid` bucket (64.4%) was within 3 000 rows of "titles containing no level word", i.e. it was
# never a level, it was the absence of one.
#
# The boundary between the two lowest rungs is **contract shape, not experience**:
#   intern       a placement — internship, Praktikum, stáž, Werkstudent, apprenticeship
#   entry_level  a first *permanent* job — graduate scheme, absolvent, trainee, entry level
# That line is crisp enough to hold across languages, which "junior vs entry-level" is not.
#
# Three tokens were removed on measured evidence, not intuition:
#   `manager`   11 210 active titles (8.8% of the corpus, 27% of everything stored `senior`)
#               carried it as their ONLY level signal — Account Manager, Product Manager,
#               Territory Sales Manager, *Assistant* Manager. All were classified senior, and
#               so were hidden from every junior/mid filter. Bare `manager` now sets no level;
#               only the handful of forms that genuinely denote people-leadership do.
#   `associate` "Associate Director" and "Production Associate II" both classified *junior*,
#               because junior was tested first. In banking and consulting an Associate is
#               mid-level, in retail it is entry. It is ambiguous, so it now says nothing.
#   `mid`       "Mid-Market" / "Mid-Enterprise" are sales *segments*. Same class of trap as
#               the `georgia` rule in CLAUDE.md — see `_MID_FALSE`.
# `staff` was suspected of the same fault (UK "Staff Nurse" means an ordinary nurse) and the
# corpus refuted it: all 1 663 occurrences are senior IC roles. It stays.

_INTERN_RE = re.compile(
    r"\b(intern|interns|internship|internships|praktikant\w*|praktikum|praktyk\w*|"
    r"st[áa][žz]\w*|stagiaire|stagista|tirocinio|becario|becaria|"
    r"working student|werkstudent\w*|apprentice\w*|apprenti|l[äæ]rling|l[æe]rling)\b", re.I)

# A first permanent job. `graduate` is the one token here that also names a *field* of work
# ("Graduate Recruitment Consultant", "Head of Graduate Talent"), so it is guarded.
_ENTRY_RE = re.compile(
    r"\b(entry[- ]?level|graduate|grad|school ?leaver|trainee\w*|"
    r"absolvent\w*|absolwent\w*|ny(?:ut)?examinerad|nyutbildad|nyutdannet|"
    r"berufseinsteiger\w*|einsteiger|einstiegs\w*|startersfunctie|"
    r"d[ée]butant\w*|jeune dipl[ôo]m[ée]\w*|neo[- ]?laureat\w*|"
    r"reci[ée]n titulad[oa]|reci[ée]n graduad[oa]|rec[ée]m[- ]formad[oa]|"
    r"no experience|early[- ]?career|early[- ]?talent)\b", re.I)
_ENTRY_FALSE = re.compile(
    r"\bgrad(?:uate)?\s+(?:recruit\w*|talent|hiring|admissions?|programme? manager)", re.I)

_JUNIOR_RE = re.compile(r"\b(junior|jr|juniorn[íi])\b", re.I)

# Explicitly mid. Tiny (~1% of the corpus) and that is fine: it is now a *stated* level rather
# than the fall-through, so a small honest bucket beats a large dishonest one.
_MID_RE = re.compile(r"\b(medior|mid[- ]?level|mid[- ]?senior|mid|ii)\b", re.I)
_MID_FALSE = re.compile(r"\bmid[- ](market|enterprise|size[d]?|cap|funnel|term)\b", re.I)

# People-leadership, which is a different axis from senior IC and was previously collapsed
# into it. 8 165 active titles carry one of these and no senior word at all.
_LEAD_RE = re.compile(
    r"\b(lead|leader|leads|head|director|vp|vice president|chief|c[teofi]o|"
    r"vedouc[íi]|kierownik|leiter\w*|responsable|"
    r"(?:engineering|development|software|data|general|country|managing)\s+(?:manager|director))\b",
    re.I)

# Senior individual contributor. Roman-numeral levels III+ ride here; II is `_MID_RE`.
_SENIOR_RE = re.compile(
    r"\b(senior|sr|staff|principal|expert|architect|seniorn[íi]|iii|iv)\b", re.I)

#: Every value `seniority()` can return, weakest first. `None` is deliberately not in it —
#: it is the absence of an answer, not a seventh level, and nothing may offer it as a choice.
SENIORITY_LEVELS = ("intern", "entry_level", "junior", "mid", "senior", "lead")


def seniority(title: Optional[str]) -> Optional[str]:
    """Classify a title into `SENIORITY_LEVELS`, or `None` when it names no level.

    **`None` is the majority answer (70.7% of the live corpus) and is not a failure.** It
    means "this title did not say", which is a different statement from "mid", and every
    consumer has to be able to tell them apart: the AI matcher must not treat silence as a
    mismatch, while a visitor ticking a filter chip is narrowing to what was actually stated.

    Precedence is **leadership first, then lowest stated rung**. Leadership wins because the
    big buckets must not leak into the small ones — "Head of Graduate Recruitment" is a lead
    role that happens to mention graduates, and letting `entry_level` win there would pollute
    a 400-row bucket with rows from an 8 000-row one. Below that the *lowest* rung wins, so
    "Junior/Medior Engineer" reads as junior: the lower bound is the bar the employer set.
    """
    t = title or ""
    if not t:
        return None
    if _LEAD_RE.search(t):
        return "lead"
    if _SENIOR_RE.search(t):
        return "senior"
    if _INTERN_RE.search(t):
        return "intern"
    if _ENTRY_RE.search(t) and not _ENTRY_FALSE.search(t):
        return "entry_level"
    if _JUNIOR_RE.search(t):
        return "junior"
    if _MID_RE.search(t) and not _MID_FALSE.search(t):
        return "mid"
    return None


def seniority_stated(title: Optional[str]) -> bool:
    """True when the title actually names a level.

    Kept as a name because `service/evaluate.py` reports on it, but it is now the trivial
    consequence of the column being able to say "I don't know" rather than a second,
    separately-maintained regex pass that could disagree with the first. It used to exist
    because `seniority()` returned a confident 'mid' for a posting that said nothing, and the
    matcher needed to know the difference; the classifier now carries that itself."""
    return seniority(title) is not None


def work_type(title: Optional[str], description: Optional[str]) -> str:
    """Classify a posting as 'freelance/contract' or 'permanent' from its text."""
    text = f"{title or ''} {description or ''}".lower()
    if any(p in text for p in _FREELANCE_PATTERNS):
        return "freelance/contract"
    if _BARE_CONTRACT.search(text) and not _CONTRACT_FALSE.search(text):
        return "freelance/contract"
    return "permanent"


# --- Part-time / reduced-hours detection (fit alongside a full-time job) ---------
_PARTTIME_RE = re.compile(
    r"part[- ]?time|part time|half[- ]?time|reduced hours|flexible hours|"
    r"\b0\.[1-8]\s?fte\b|\b(?:10|15|20|24|25|30)\s?(?:h|hrs|hours)\s?(?:/|per|a)\s?week|"
    r"a few hours|moonlight|side project|evenings and weekends|"
    # Czech part-time / side-contract signals
    r"\bdpp\b|\bdp[cč]\b|[cč]áste[cč]n[yý] [uú]vazek|polovi[cč]n[ií] [uú]vazek|"
    r"zkr[aá]cen[yý] [uú]vazek|brig[aá]d|[uú]vazek 0[.,]",
    re.I)
_FULLTIME_NEG = re.compile(r"full[- ]?time only|plný úvazek pouze", re.I)


def is_part_time(title: Optional[str], description: Optional[str]) -> bool:
    """True if the posting signals part-time / reduced-hours availability."""
    text = f"{title or ''} {description or ''}"
    return bool(_PARTTIME_RE.search(text)) and not _FULLTIME_NEG.search(text)


# --- Region + EU-eligibility heuristics ---
#: Where an EU/EEA citizen may work without a permit. **This is the EEA, not the EU**, and it
#: must stay in step with `service.geo.EEA_COUNTRIES`, which is the one definition — a drift
#: test in `service/tests/test_geo.py` fails if it does not. The copy exists because
#: `search_jobs` sits upstream of `service` in the import graph (`service.ingest` imports
#: `work_region` from here), which is the same reason `web/lib/geo.ts` is a drift-tested
#: mirror rather than an import.
#:
#: **It was 15 countries until 2026-08-11 and had been quietly stale for months.** NO, CH, IS,
#: LI, GR, HU, RO, BG, HR, SI, LT, LV, EE, LU, MT and CY were all missing, so a posting in any
#: of them resolved to `region="other"` and `eligibility="unknown"`. That never blocked
#: anything — `unknown` is admitted unconditionally — which is exactly why nobody noticed: the
#: only visible effect was `_RANK` sorting genuinely-eligible EEA roles below Czech and German
#: ones. Found while adding `nav`, because Norway was the sixteenth missing country.
_EU_CODES = {
    # EU-27
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    # EEA-EFTA and Switzerland — free movement, so eligibility is the same
    "CH", "IS", "LI", "NO",
}


def work_region(location: Optional[str], country_code: Optional[str]) -> str:
    loc = (location or "").lower()
    cc = (country_code or "").upper()
    if cc == "CZ" or re.search(r"czech|praha|prague|brno|ostrava", loc):
        return "cz"
    if re.search(r"worldwide|anywhere|global|fully remote", loc):
        return "worldwide"
    if cc == "GB" or re.search(r"united kingdom|england|london", loc):
        return "uk"
    if cc == "US" or re.search(r"united states|\busa\b|remote us", loc):
        return "us"
    if cc in _EU_CODES or re.search(r"europe|emea|\beu\b|germany|netherlands|poland|spain|france|ireland", loc):
        return "eu"
    return "other"


def eligibility(region: str, text: str) -> str:
    """Coarse EU-eligibility flag for a Czech-based candidate."""
    t = text.lower()
    if re.search(r"security clearance|ts/sci|public trust|must be a us citizen|us citizen", t):
        return "blocked (clearance/US-only)"
    if region in ("eu", "worldwide", "cz"):
        return "eligible"
    if region == "uk":
        return "verify UK right-to-work"
    if region == "us":
        return "likely needs US work auth"
    return "unknown"


# Preference order for grouping (best first).
_RANK = {"eligible": 0, "unknown": 1, "verify UK right-to-work": 2,
         "likely needs US work auth": 3, "blocked (clearance/US-only)": 4}


def dedup_key(p: JobPosting) -> str:
    title = re.sub(r"\b(senior|junior|medior|mid|lead|principal|staff|sr|jr)\b", "",
                   (p.title or "").lower().split("|")[0].split(" - ")[0])
    return re.sub(r"\s+", " ", title).strip() + "::" + (p.company or "").lower().strip()


def source_classes(include_cz: bool) -> list[type]:
    """The adapters `gather` will run, without running them.

    Split out of `gather` so `service.source_watchdog` can ask *which sources are expected
    to produce rows today* and read the answer from the list itself rather than a second
    copy that drifts — the same reason `scripts/probe_boards.py` reads its board lists out
    of the adapters. A source missing from here is not stale, it is retired (jobscz,
    profesia), and alerting on those forever is how a monitor gets muted."""
    return _source_classes(include_cz)


def gather(include_cz: bool) -> list[JobPosting]:
    sources = _source_classes(include_cz)

    postings: list[JobPosting] = []
    for cls in sources:
        try:
            src = cls()
            got = src.run()
            postings.extend(got)
            logger.info("%s: %d", src.source_name, len(got))
        except Exception:
            logger.exception("Source %s failed, skipping.", cls.__name__)
    return postings


def _source_classes(include_cz: bool) -> list[type]:
    sources = [RemotiveSource, WeWorkRemotelySource, RemoteOKSource, HimalayasSource,
               JobicySource, ArbeitnowSource, WorkingNomadsSource,
               GreenhouseSource, AshbySource, LeverSource,
               # Located (non-remote-only) inventory, which everything above is thin on:
               # The Muse is the widest US source in the stack; SmartRecruiters and Workday
               # reach the large European employers outside the startup belt. Oracle
               # Recruiting Cloud reaches the large *industrials* neither of those touches —
               # Vertiv, Honeywell and Emerson, which between them hold 32 Czech and Slovak
               # engineering roles that no other adapter here carries. It sits before the
               # last two rather than beside them because it is not an N+1 adapter: its list
               # rows already carry descriptions, so a full pull is ~17 requests, not ~1 200.
               TheMuseSource, OracleCloudSource,
               # Platsbanken is the Swedish labour office's own register, published as open
               # data by Arbetsförmedlingen — the MPSV pattern in a second country, and the
               # single largest permitted national source found so far: ~15 200 white-collar
               # ads with 100% descriptions at a median of 3 775 characters. Germany's
               # equivalent is ~55× bigger and is NOT here: the Bundesagentur's terms §2a(3)
               # forbid automated reading of the portal, which is the Alma Career situation
               # again. See the README before reaching for it.
               PlatsbankenSource,
               # NAV is Norway's statutory register and the third of that shape after `mpsv`
               # and `platsbanken` — and the only one whose terms *grant* republication rather
               # than merely disclaiming a database right. It is the one source that keeps a
               # local mirror: its feed is an append-only change log, so an ad unchanged for a
               # month appears in no recent window, and `deactivate_stale` would otherwise take
               # Norwegian inventory to zero within a week. See the module docstring.
               NavSource,
               # Werken voor Nederland is the fourth register of that shape and the best
               # licensed of them: CC0 with attribution *not required*, against MPSV's mere
               # disclaimer of the database right. ~1 260 central-government vacancies, all
               # professional (no ISCO filter needed, unlike MPSV), 100% with a min/max EUR
               # salary off schema.org JSON-LD. It is also the only NL route that exists —
               # werk.nl renders 110 characters of visible text and lists no vacancies in its
               # sitemap. Two requests per posting, ~21 min; its pages name individual
               # recruiters with direct mobiles, so the description is built from an
               # allowlist of content sections and the contact block is never read. See the
               # module docstring and `test_werkenvoornederland.py`.
               WerkenVoorNederlandSource,
               # Remote in Europe publishes NO terms of use and no privacy policy — permission
               # is unestablished, which is normally a skip in this repo (the Bundesagentur
               # error). Wired 2026-08-15 as a deliberate, enumerated exception; robots permits
               # the job pages and nothing on the host refuses automated reading. See the
               # module docstring, `ingestion/tests/test_unestablished_permission.py`, and the
               # entry in docs/sources.md. It is here for its `schema-loc` country arrays —
               # multi-country remote scopes are what the EU-International row is short of and
               # almost nothing else in this stack emits them. Expect heavy overlap with the
               # ATS adapters: it is a re-lister, so judge it on postings surviving dedupe.
               RemoteInEuropeSource,
               # NVA is Latvia's live vacancy register (`cvvp.nva.gov.lv`) — NOT the CC0
               # "Vakances" CSV, which covers only the public sector. It publishes no terms and
               # no robots.txt, so it is a member of the same 2026-08-15 unestablished-
               # permission decision as RemoteInEuropeSource; see that test and docs/sources.md.
               # `KEEP_FIELDS` is the MPSV dilution guard in a second country: the register is
               # Latvia's whole labour market and its five commonest professions are labourer,
               # shop assistant, driver, cook and nurse. Not `nav` — that is Norway.
               NvaSource,
               # Golden Careers is a Cypriot recruitment agency's own board — the first
               # non-ATS Cypriot inventory here, and small (~62 vacancies). No terms and no
               # privacy policy, robots is allow-all: the same 2026-08-15 unestablished-
               # permission decision. NOT cyprusjobs.com, which serves an Art. 4 EU DSM TDM
               # reservation and is deliberately excluded. Its ads anonymise the employer
               # ("our client"), so `company` is the agency and dedupe_key cannot collapse a
               # duplicate carried on the employer's own ATS board — see the module docstring.
               GoldenCareersSource,
               # Workday is last on purpose — it must fetch each posting's description with
               # its own request, so it is by far the slowest, and a failure there should not
               # cost everything that runs before it.
               #
               # `SmartRecruitersSource` USED to sit here beside it and was removed
               # 2026-08-11: `api.smartrecruiters.com/robots.txt` is `Disallow: /` for `*`
               # and grants our exact path (`/v1/companies/`) to LinkedInBot alone. See
               # `ingestion/tests/test_source_exclusions.py` before re-adding it.
               WorkdaySource]
    # Adzuna only if keys are present.
    try:
        from ingestion.sources.adzuna import AdzunaSource
        AdzunaSource()  # raises if creds are missing, empty, or unresolved op:// refs
        sources.append(AdzunaSource)
    except KeyError as exc:
        name = exc.args[0] if exc.args else "ADZUNA_APP_ID / ADZUNA_API_KEY"
        logger.info("Adzuna skipped (no %s in env).", name)
    except Exception as exc:
        # Not the missing-key case, so say which one it is. A fixed "no keys in env" line
        # here sent you looking for an absent variable when the variable was present and
        # holding an unresolved 1Password reference.
        logger.info("Adzuna skipped: %s", exc)
    # USAJOBS only if a key + registered email are present. The US federal register — the
    # mpsv/platsbanken pattern in a third country — worth having only since the US became
    # selectable (2026-08-05); before that it was all gated-out inventory.
    try:
        from ingestion.sources.usajobs import USAJobsSource
        USAJobsSource()  # raises KeyError if USAJOBS_API_KEY / USAJOBS_EMAIL are missing/empty
        sources.append(USAJobsSource)
    except KeyError as exc:
        name = exc.args[0] if exc.args else "USAJOBS_API_KEY / USAJOBS_EMAIL"
        logger.info("USAJOBS skipped (no %s in env).", name)
    if include_cz:
        # Jobs.cz and Profesia are NOT here, and their adapters are kept only as code —
        # `ingestion/sources/jobscz.py` and `profesia.py` still work and are still tested.
        # Both are Alma Career brands, and Alma Career's Podmínky používání §4.11 forbids
        # automated processing of the data in their systems and "načítat prezentovaná data …
        # pro další strojové či automatizované zpracování", with §4.7(e) making bulk automated
        # reading grounds for termination. Their robots.txt permits the paths we fetched, but
        # robots.txt is a crawling convention and the terms are the binding document; the same
        # words — vytěžovat / zužitkovávat — are the statutory language of the EU database
        # right (96/9/EC), which applies with or without a contract.
        # Excluded 2026-08-03, and closed rather than pending: this project does not write to
        # publishers, so a source either already permits us or it is skipped. The cost is
        # deliberate and large: 92% of Czech and 99% of Slovak inventory. Do not re-add either
        # one because the digest looks thin.
        from ingestion.sources.startupjobs import StartupJobsSource
        from ingestion.sources.cocuma import CocumaSource
        # MPSV is the Czech public employment service's own register, published as open data
        # by the Ministry of Labour. Its licence metadata expressly disclaims the sui generis
        # database right — the exact right that put Jobs.cz and Profesia out of reach — so it
        # is the one large CZ source this repo can read without asking anyone. ~7 300
        # white-collar vacancies, 1 576 of them in Prague. Recruitee carries the mid-size
        # Czech employers that no existing ATS adapter reaches.
        # Workable exists for Slovakia specifically: there is no Slovak equivalent of MPSV
        # (the labour office publishes no per-vacancy open data, only aggregate counts) and
        # worki.sk forbids further dissemination without written consent, so company ATS
        # boards are the only open route into Slovak inventory at all.
        from ingestion.sources.mpsv import MpsvSource
        from ingestion.sources.recruitee import RecruiteeSource
        from ingestion.sources.workable import WorkableSource
        # Teamtailor is the same shape as Recruitee/Workable — a curated multi-country ATS
        # read from a keyless public feed. It reaches the thinnest selectable countries
        # (EE/LV/FI/DK) whose product companies run Teamtailor rather than a supported ATS,
        # which the Greenhouse/Ashby/Lever passes could not. Permitted on its terms (no
        # anti-scraping clause; robots + Content-Signal affirmatively allow it) — see the
        # module docstring. Personio was evaluated alongside and skipped (unreadable terms).
        from ingestion.sources.teamtailor import TeamtailorSource
        sources += [StartupJobsSource, CocumaSource, RecruiteeSource, WorkableSource,
                    TeamtailorSource, MpsvSource]
    return sources


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-live", action="store_true", help="HTTP-probe each URL (slower)")
    ap.add_argument("--cz", action="store_true", help="also include Czech sources")
    ap.add_argument("--freelance", action="store_true",
                    help="keep only freelance/contract data roles")
    ap.add_argument("--junior", action="store_true",
                    help="keep only junior/mid roles (drop senior/lead/staff/principal)")
    ap.add_argument("--parttime", action="store_true",
                    help="keep only roles signalling part-time / reduced hours")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--out", default="candidates.json")
    args = ap.parse_args()

    postings = gather(include_cz=args.cz)
    logger.info("Collected %d total postings", len(postings))

    # Keep data roles, dedup.
    seen: dict[str, JobPosting] = {}
    for p in postings:
        if p.url and is_data_role(p.title):
            seen.setdefault(dedup_key(p), p)
    candidates = list(seen.values())
    logger.info("%d unique data-role candidates", len(candidates))

    rows = []
    for p in candidates:
        region = work_region(p.location, p.country_code)
        elig = eligibility(region, f"{p.title or ''} {p.location or ''} {p.description or ''}")
        rows.append({
            "title": p.title, "company": p.company, "source": p.source,
            "location": p.location, "region": region, "eligibility": elig,
            "work_type": work_type(p.title, p.description),
            "seniority": seniority(p.title),
            "part_time": is_part_time(p.title, p.description),
            "url": p.url, "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "description": (p.description or "")[:600],
        })

    if args.freelance:
        rows = [r for r in rows if r["work_type"] == "freelance/contract"]
        logger.info("Freelance filter: kept %d freelance/contract candidates", len(rows))
    if args.junior:
        # `None` is kept deliberately: it means the title named no level, which is most of
        # them, and dropping silence would throw away the bulk of what a junior can apply to.
        rows = [r for r in rows
                if r["seniority"] in ("intern", "entry_level", "junior", "mid", None)]
        logger.info("Junior filter: kept %d junior/mid/unstated candidates", len(rows))
    if args.parttime:
        rows = [r for r in rows if r["part_time"]]
        logger.info("Part-time filter: kept %d part-time candidates", len(rows))

    # Best-eligible first; when hunting junior, float junior/mid up, else freelance up.
    # Unstated sorts between mid and senior rather than last: it is the majority answer, and
    # burying it would empty the top of a junior hunt of everything that simply didn't say.
    _sen_rank = {lvl: i for i, lvl in enumerate(SENIORITY_LEVELS)}
    rows.sort(key=lambda r: (
        _RANK.get(r["eligibility"], 5),
        _sen_rank.get(r["seniority"], 3.5) if args.junior else
        (0 if r["work_type"] == "freelance/contract" else 1),
        r["region"]))
    rows = rows[: args.limit]

    if args.check_live:
        # `notify.check_url_liveness` used to live here and was deleted with the rest of the
        # personal-alert feed on 2026-07-21, leaving this flag as an ImportError nobody hit.
        # `scripts/check_links.py` is the replacement, and it is stricter: a 200 is not a pass
        # unless the page actually carries the posting's title.
        from scripts.check_links import probe  # lazy: only this flag needs requests-per-row
        live = []
        for r in rows:
            verdict, note = probe(r["url"], r.get("title") or "", r.get("company"))
            r["liveness"] = verdict
            r["liveness_note"] = note
            if verdict != "DEAD":
                live.append(r)
        logger.info("Liveness: kept %d of %d", len(live), len(rows))
        rows = live

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    from collections import Counter
    print(f"\n{len(rows)} candidates -> {args.out}")
    print("by eligibility:", dict(Counter(r["eligibility"] for r in rows)))
    print("by region:     ", dict(Counter(r["region"] for r in rows)))
    print("by work_type:  ", dict(Counter(r["work_type"] for r in rows)))
    print("by seniority:  ", dict(Counter(r["seniority"] for r in rows)))
    print("\nTop eligible:")
    for r in [x for x in rows if x["eligibility"] == "eligible"][:12]:
        tag = "[FL]" if r["work_type"] == "freelance/contract" else "[PT]"
        print(f"  {tag} [{r['region']}] {(r['title'] or '?')[:44]:<44} | {(r['company'] or '?')[:22]:<22} | {r['source']}")
    fl = [x for x in rows if x["work_type"] == "freelance/contract"]
    if fl and not args.freelance:
        print(f"\nFreelance/contract ({len(fl)} found — run with --freelance to isolate):")
        for r in fl[:12]:
            print(f"  [{r['eligibility'][:8]:<8}|{r['region']}] {(r['title'] or '?')[:42]:<42} | {(r['company'] or '?')[:20]:<20} | {r['source']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    main()
