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
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.himalayas import HimalayasSource
from ingestion.sources.jobicy import JobicySource
from ingestion.sources.lever import LeverSource
from ingestion.sources.oraclecloud import OracleCloudSource
from ingestion.sources.platsbanken import PlatsbankenSource
from ingestion.sources.remoteok import RemoteOKSource
from ingestion.sources.remotive import RemotiveSource
from ingestion.sources.smartrecruiters import SmartRecruitersSource
from ingestion.sources.themuse import TheMuseSource
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
_JUNIOR_RE = re.compile(
    r"\b(junior|jr|graduate|grad|entry[- ]?level|intern|internship|apprentice|"
    r"working student|werkstudent|trainee|early[- ]career|no experience|"
    r"associate|early[- ]?talent)\b", re.I)
# Senior/lead signals, incl. roman-numeral levels III+ and "II" (mid-senior).
_SENIOR_RE = re.compile(
    r"\b(senior|sr|staff|principal|lead|head|director|vp|expert|architect|"
    r"iii|iv|manager)\b", re.I)


def seniority(title: Optional[str]) -> str:
    """Classify a title as 'junior', 'mid', or 'senior'."""
    t = title or ""
    if _JUNIOR_RE.search(t):
        return "junior"
    if _SENIOR_RE.search(t):
        return "senior"
    return "mid"


def seniority_stated(title: Optional[str]) -> bool:
    """True when the title actually names a level, rather than falling through to 'mid'.

    `seniority()` returns 'mid' for two very different postings: one that says "Mid-level
    Analyst" and one that says nothing at all — and most titles say nothing at all. Anything
    treating the stored value as a fact the poster asserted will mis-handle the second kind.
    The matcher uses this to show the model "seniority=unstated" instead of a confident
    "mid", so its hard seniority filter drops genuine level mismatches without also dropping
    every unlabelled posting from a junior-only subscriber's digest."""
    t = title or ""
    return bool(_JUNIOR_RE.search(t) or _SENIOR_RE.search(t))


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
_EU_CODES = {"DE", "NL", "FR", "ES", "PL", "AT", "IE", "PT", "SK", "IT", "BE", "SE", "DK", "FI", "CZ"}


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


def gather(include_cz: bool) -> list[JobPosting]:
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
               # The last two are last on purpose — both must fetch each posting's
               # description with its own request, so they are by far the slowest, and a
               # failure in either should not cost everything that runs before it.
               SmartRecruitersSource, WorkdaySource]
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
        # Excluded 2026-08-03 pending a conversation with Alma Career about a feed. The cost
        # is deliberate and large: 92% of Czech and 99% of Slovak inventory. Do not re-add
        # either one because the digest looks thin — re-add them when there is permission.
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
        sources += [StartupJobsSource, CocumaSource, RecruiteeSource, WorkableSource,
                    MpsvSource]

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
        rows = [r for r in rows if r["seniority"] in ("junior", "mid")]
        logger.info("Junior filter: kept %d junior/mid candidates", len(rows))
    if args.parttime:
        rows = [r for r in rows if r["part_time"]]
        logger.info("Part-time filter: kept %d part-time candidates", len(rows))

    # Best-eligible first; when hunting junior, float junior/mid up, else freelance up.
    _sen_rank = {"junior": 0, "mid": 1, "senior": 2}
    rows.sort(key=lambda r: (
        _RANK.get(r["eligibility"], 5),
        _sen_rank[r["seniority"]] if args.junior else
        (0 if r["work_type"] == "freelance/contract" else 1),
        r["region"]))
    rows = rows[: args.limit]

    if args.check_live:
        from notify import check_url_liveness  # lazy: avoids the snowflake import unless needed
        live = []
        for r in rows:
            verdict, note = check_url_liveness(r["url"])
            r["liveness"] = verdict
            if verdict != "dead":
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
