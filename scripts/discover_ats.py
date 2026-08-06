"""Find which ATS a company's own careers page runs on, and read the slug off it.

**Offline discovery tool, not an ingestion source.** It never runs in the 05:00 window; you
run it by hand, read the report, and hand-copy the verified rows into the seed or the
adapter lists. That separation is deliberate — a runtime crawler over 200 employer domains
is a very different thing from a one-off probe, both in load and in what we'd have to
justify to a site owner.

Two ways in, because neither alone is enough
--------------------------------------------
1. **Read the careers page** and extract the slug from the ATS link the company published.
   This is the only way to learn a Workday site, which is a `(tenant, host shard, site slug)`
   triple whose slug is unguessable — Adobe's is `external_experienced`, NVIDIA's
   `NVIDIAExternalCareerSite`, and a brute-force sweep of 2 568 combinations across 100
   companies found **two**. Reading it off the page is that note's "verify and record",
   minus the typing.
2. **Probe the ATS APIs with name-derived slugs.** Measured on the first run of this list,
   path 1 alone found an ATS on 1 company in 12: most careers pages are now client-rendered
   and load jobs by XHR, so the initial HTML contains no ATS reference at all. Every big
   Czech name tested — Kiwi, Productboard, Mews, Rohlík, Packeta, Seznam — returned a
   multi-megabyte document with zero markers in it. Playwright would fix that and is out
   (repo constraint), so the fallback guesses the slug instead. That is a different bet from
   the Workday sweep: one token with ~3 plausible values, not a three-part key.

**Both paths end at the same gate: `_verify` calls the ATS API and counts jobs.** A link in
a footer proves a company once used an ATS, and a guessed slug proves nothing at all; only
a 200 with a job count proves a board that serves jobs today. A dark board is a silent
zero — what `fetch` skips without an error-level log — so `jobs` in the output is always a
measured number, never an inference.

Deliberately not derived from jobs.cz
-------------------------------------
The obvious way to build the input list is to enumerate employers on jobs.cz and follow
where each posting points. Alma Career's Podmínky používání §4.11 forbids "načítat
prezentovaná data … pro další strojové či automatizované zpracování", and a roster of every
employer on the board is a more substantial extraction of their database than the individual
postings `gather()` already excludes (see `ingestion/tests/test_source_exclusions.py`, and
the EU database right, 96/9/EC). `scripts/cz_companies.csv` is hand-assembled instead.

Politeness is not optional here: this is the widest set of hosts anything in the repo
touches, and most of them never agreed to be crawled by anyone. It uses `ingestion.politeness`
unchanged — the honest `JobDigest/1.0` user agent, the robots.txt gate, and the 1 s per-host
delay. Concurrency is across *companies*, so the per-host throttle still holds for each.

Usage
-----
    python scripts/discover_ats.py
    python scripts/discover_ats.py --limit 20 --out scratch/hits.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion import politeness  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("discover")
logging.getLogger("ingestion.politeness").setLevel(logging.ERROR)

TIMEOUT = 15
MAX_CAREERS_PAGES = 4

#: Paths worth trying when the homepage exposes no careers link. Czech first — this list is
#: aimed at Czech employers, and `/kariera` outnumbers `/careers` on .cz domains.
CAREERS_PATHS = [
    "/kariera", "/kariera/", "/cs/kariera", "/kariera-a-volna-mista",
    "/volna-mista", "/nabidka-prace", "/prace", "/o-nas/kariera",
    "/careers", "/careers/", "/en/careers", "/career", "/jobs", "/join-us",
    "/company/careers", "/about/careers", "/en/career",
]

#: href/link text that marks a careers link on a homepage.
CAREERS_HINT = re.compile(
    r"kariér|kariera|volná\s*míst|volna-mist|nabídka\s*práce|prace-u-nas|"
    r"career|jobs|join[-\s]?us|work[-\s]?with[-\s]?us|hiring|vacanc",
    re.I,
)

#: host fingerprint -> (ats name, regex capturing the slug/token).
#: Ordered: the first match on a page wins, so more specific patterns come first.
FINGERPRINTS: list[tuple[str, re.Pattern[str]]] = [
    # (tenant, shard, site) — all three, because none of them is derivable from the others.
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[\w-]{2,5}-[A-Z]{2}/)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board\?for=([\w.-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w.-]+)", re.I)),
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("personio", re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("teamtailor", re.compile(r"https?://([\w-]+)\.teamtailor\.com", re.I)),
    ("bamboohr", re.compile(r"https?://([\w-]+)\.bamboohr\.com", re.I)),
    ("recruitis", re.compile(r"https?://([\w-]+)\.recruitis\.io", re.I)),
    ("join", re.compile(r"join\.com/companies/([\w-]+)", re.I)),
    ("successfactors", re.compile(r"https?://([\w-]+)\.(?:successfactors|sapsf)\.(?:eu|com)", re.I)),
    # (tenant, region, site) — the same shape as Workday, and for the same reason: the region
    # is not derivable from the tenant and the site number is not derivable from either.
    # This pattern used to capture the tenant alone and drop the region on the floor, so every
    # oraclecloud row in the results CSV was unverifiable and sat at jobs = -1. The site is
    # optional because a footer link often names only the host; `_verify` falls back to CX_1,
    # which is the default Oracle provisions and the one Emerson and Vertiv both use.
    ("oraclecloud", re.compile(
        r"https?://([\w-]+)\.fa\.([\w\d]+)\.oraclecloud\.com"
        r"(?:/hcmUI/CandidateExperience/[\w-]+/sites/([\w-]+))?", re.I)),
    ("icims", re.compile(r"https?://([\w-]+)\.icims\.com", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([\w-]+)", re.I)),
    ("teamio", re.compile(r"https?://([\w-]+)\.jobs\.cz|teamio\.com", re.I)),
]

#: ATSes we already ingest — a hit here is a seed/adapter row and nothing more to build.
#: ATSes `gather()` actually has an adapter for. This is a copy of a fact that lives in
#: `search_jobs.py`, and on 2026-08-04 it was **out of date in the direction that loses
#: findings**: `recruitee`, `workable` and `oraclecloud` had all been live sources for days
#: and were reported here as unsupported. The thin-country pass turned up four Greek boards
#: on Workable — for a country holding 43 active postings in the whole corpus — every one of
#: them printed as `no`, which is the same thing as not finding them.
#: `test_discovery_supported.py` fails if this drifts from the adapters again.
SUPPORTED = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday",
             "recruitee", "workable", "oraclecloud", "teamtailor"}

#: Alma Career's own ATS. A hit is recorded but must NOT be treated as a free win: the
#: careers page is served from the infrastructure whose terms excluded jobs.cz and profesia.
#: Different product, plausibly a different document — check before ingesting, don't assume.
#: BambooHR is here for a different reason than Teamio and the distinction matters: its public
#: `/careers/list` endpoint works, answers 200 to the honest agent, and its per-tenant
#: robots.txt *allows* it. Its ToS §4.2 forbids "any robot, spider, other automated device, or
#: manual process to monitor or copy any content from the Service", and the Developer ToS
#: forbids crawling "without BambooHR's prior written consent". The thin-country pass found
#: eight tenants on it — none of them usable. Marked rather than dropped so a hit is reported
#: with the reason attached, instead of turning up again next run as an apparent free win.
RESTRICTED = {"teamio", "bamboohr"}


@dataclass
class Hit:
    company: str
    domain: str
    tier: str
    ats: str
    token: str
    extra: str = ""          # workday shard/site, else empty
    jobs: int = -1           # -1 = not verified, >=0 = measured
    found_on: str = ""
    jsonld: bool = False
    note: str = ""


@dataclass
class Result:
    company: str
    domain: str
    tier: str
    hits: list[Hit] = field(default_factory=list)
    jsonld: bool = False
    error: str = ""


def _fetch(url: str) -> requests.Response | None:
    """Raw GET under the robots gate and the per-host throttle. None if the host refused."""
    try:
        if not politeness.robots_allows(url):
            return None
        politeness.throttle(url)
        return requests.get(url, headers=politeness.HEADERS, timeout=TIMEOUT,
                            allow_redirects=True)
    except requests.RequestException:
        return None


def _get_page(url: str) -> requests.Response | None:
    """A page worth *scanning*, which is not the same as a page that returned 200.

    Client-rendered sites routinely serve a full document under a non-200 — Kiwi.com's
    careers route answers 404 with 2 MB of HTML, Packeta's and Seznam's likewise — and a
    site-wide footer carries the ATS link on every page including the error one. Requiring
    200 here discarded those pages unread. Being liberal costs nothing, because a token only
    becomes a reported hit once `_verify` gets a real answer from the ATS API; the
    strictness lives there, where it can actually prove something.
    """
    resp = _fetch(url)
    if resp is None or resp.status_code >= 500 or len(resp.text) < 500:
        return None
    return resp


def _get_api(url: str) -> requests.Response | None:
    """Fetch an ATS API endpoint. Strict: only a 200 counts as an answer."""
    resp = _fetch(url)
    return resp if resp is not None and resp.status_code == 200 else None


def _careers_urls(home_url: str, html: str) -> list[str]:
    """Careers-page URLs linked from a homepage, most-promising first."""
    urls: list[str] = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html,
                         re.I | re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if not CAREERS_HINT.search(href) and not CAREERS_HINT.search(text):
            continue
        full = urljoin(home_url, href)
        if full.startswith("http") and full not in urls:
            urls.append(full)
    return urls[:MAX_CAREERS_PAGES]


def _scan(html: str) -> list[tuple[str, str, str]]:
    """(ats, token, extra) for every ATS fingerprint present in this HTML."""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ats, pattern in FINGERPRINTS:
        for m in pattern.finditer(html):
            groups = [g for g in m.groups() if g]
            if not groups:
                continue
            token = groups[0].lower().strip(".-")
            # Slugs that are obviously the ATS's own marketing pages, not a customer board.
            if token in {"www", "jobs", "careers", "api", "embed", "app", "help", "blog"}:
                continue
            extra = "/".join(groups[1:]) if len(groups) > 1 else ""
            if (ats, token) in seen:
                continue
            seen.add((ats, token))
            out.append((ats, token, extra))
    return out


def _has_jsonld_jobposting(html: str) -> bool:
    """Does this page publish schema.org/JobPosting markup? (Tier-3 candidate signal.)"""
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html, re.I | re.S):
        if '"JobPosting"' in m.group(1) or "'JobPosting'" in m.group(1):
            return True
    return False


# --- verification ---------------------------------------------------------------------
# A link proves the company once used an ATS. Only the API proves the board still serves
# jobs. Anything unverified is reported with jobs = -1 rather than silently counted.

def _verify(hit: Hit) -> None:
    ats, tok = hit.ats, hit.token
    try:
        if ats == "greenhouse":
            r = _get_api(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
            hit.jobs = len(r.json().get("jobs", [])) if r else -1
        elif ats == "lever":
            r = _get_api(f"https://api.lever.co/v0/postings/{tok}?mode=json")
            hit.jobs = len(r.json()) if r else -1
        elif ats == "ashby":
            r = _get_api(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
            hit.jobs = len(r.json().get("jobs", [])) if r else -1
        elif ats == "smartrecruiters":
            r = _get_api(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=10")
            hit.jobs = int(r.json().get("totalFound", 0)) if r else -1
        elif ats == "recruitee":
            r = _get_api(f"https://{tok}.recruitee.com/api/offers/")
            hit.jobs = len(r.json().get("offers", [])) if r else -1
        elif ats == "workable":
            r = _get_api(f"https://apply.workable.com/api/v1/widget/accounts/{tok}?details=true")
            hit.jobs = len(r.json().get("jobs", [])) if r else -1
        elif ats == "personio":
            r = _get_api(f"https://{tok}.jobs.personio.de/xml")
            hit.jobs = r.text.count("<position>") if r else -1
        elif ats == "bamboohr":
            r = _get_api(f"https://{tok}.bamboohr.com/careers/list")
            hit.jobs = len(r.json().get("result", [])) if r else -1
        elif ats == "oraclecloud":
            # `extra` is "region" or "region/site" — the site is only in the URL when the page
            # linked a deep candidate-experience path. CX_1 is Oracle's default site number and
            # is what Emerson and Vertiv both serve from, so it is the right fallback; a tenant
            # whose only site is CX_2 reads as a zero here, which is a miss, not a false hit.
            region, _, site = hit.extra.partition("/")
            if region:
                url = (f"https://{tok}.fa.{region}.oraclecloud.com/hcmRestApi/resources/"
                       f"latest/recruitingCEJobRequisitions")
                finder = f"findReqs;siteNumber={site or 'CX_1'},limit=1,offset=0"
                r = _get_api(f"{url}?onlyData=true&expand=requisitionList&finder={finder}")
                items = r.json().get("items", []) if r else []
                hit.jobs = int(items[0].get("TotalJobsCount", 0)) if items else -1
        elif ats == "workday":
            shard, _, site = hit.extra.partition("/")
            if shard and site:
                url = (f"https://{tok}.{shard}.myworkdayjobs.com"
                       f"/wday/cxs/{tok}/{site}/jobs")
                politeness.throttle(url)
                r = requests.post(url, headers={**politeness.HEADERS,
                                                "Content-Type": "application/json"},
                                  json={"limit": 1, "offset": 0, "searchText": ""},
                                  timeout=TIMEOUT)
                hit.jobs = int(r.json().get("total", 0)) if r.status_code == 200 else -1
        elif ats == "teamtailor":
            # Keyless public JSON Feed. A wrong slug 404s, so a 200 with items is a real
            # board — but see inspect_hits.py before wiring: the feed carries the real
            # hiringOrganization name and per-posting country, so identity is verifiable.
            r = _get_api(f"https://{tok}.teamtailor.com/jobs.json")
            hit.jobs = len(r.json().get("items", [])) if r else -1
        else:
            hit.note = "no public verify endpoint"
    except (ValueError, KeyError, TypeError, requests.RequestException):
        hit.jobs = -1
        hit.note = "verify failed"


#: Legal forms and corporate noise to drop before slugging a company name.
_NOISE = re.compile(
    r"\b(a\.?s\.?|s\.?r\.?o\.?|spol\.?|se|plc|inc|ltd|llc|gmbh|group|holding|"
    r"czech\s+republic|czechia|cz|international|technologies|technology|software|"
    r"solutions|systems|digital)\b", re.I)


def slug_candidates(company: str, domain: str) -> list[str]:
    """Plausible ATS slugs for a company, best first.

    The domain's second-level label goes first because it is the company's own chosen
    short name and is right far more often than anything derived from the display name
    (`rohlik.group` -> `rohlik`, not `rohlik-group`).
    """
    label = domain.split(".")[0].lower()
    base = unicodedata.normalize("NFKD", company)
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"\(.*?\)", " ", base)                 # "Gen Digital (Avast)" -> "Gen Digital"
    cleaned = _NOISE.sub(" ", base)
    words = re.findall(r"[a-zA-Z0-9]+", cleaned) or re.findall(r"[a-zA-Z0-9]+", base)

    out: list[str] = [label]
    if words:
        out += ["".join(words).lower(), "-".join(words).lower(), words[0].lower()]
    # The parenthesised alias, if any — often the better-known brand ("Avast").
    for alias in re.findall(r"\((.*?)\)", base):
        alias_words = re.findall(r"[a-zA-Z0-9]+", alias)
        if alias_words:
            out.append("".join(alias_words).lower())

    seen, uniq = set(), []
    for s in out:
        if len(s) > 1 and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:4]


#: ATSes whose slug is a single guessable token, so a name-derived candidate can be tested
#: directly against the API. Workday is deliberately absent: its site is a
#: `(tenant, shard, slug)` triple and the slug is unguessable — brute force found 2 sites in
#: 2 568 attempts. Only the careers-page scan can find a Workday triple, by reading it.
API_PROBEABLE = ["greenhouse", "lever", "ashby", "smartrecruiters", "recruitee",
                 "workable", "teamtailor", "personio", "bamboohr"]


def probe_api_slugs(company: str, domain: str, tier: str,
                    already: set[str]) -> list[Hit]:
    """Test name-derived slugs against each ATS API directly.

    This is guessing, which the Workday note warns against — but the warning is about a
    three-part key, where the search space is combinatorial and the hit rate was 0.08%.
    A single-token slug is one dimension with about three plausible values, and the API
    answers definitively: a hit is a live board with a counted number of jobs, not an
    inference. It exists because most careers pages are now client-rendered and carry no
    ATS link in their HTML at all, which is what the page scan needs to work.
    """
    hits: list[Hit] = []
    for ats in API_PROBEABLE:
        if ats in already:
            continue
        for slug in slug_candidates(company, domain):
            probe_hit = Hit(company=company, domain=domain, tier=tier, ats=ats,
                            token=slug, found_on="api-probe")
            _verify(probe_hit)
            if probe_hit.jobs > 0:
                hits.append(probe_hit)
                break                       # one live board per ATS is enough
    return hits


def probe(company: str, domain: str, tier: str) -> Result:
    """Walk a company's careers pages until an ATS fingerprint turns up.

    Two levels deep, because the common shape is homepage -> "Kariéra" landing page ->
    "Volná místa", and the ATS link only appears on the third. One level finds roughly half
    of what two finds, measured on the first run of this list.
    """
    res = Result(company=company, domain=domain, tier=tier)
    resp = _get_page(f"https://{domain}") or _get_page(f"https://www.{domain}")
    if resp is None:
        # An unreachable homepage is not the end of the probe. The API path needs only the
        # company *name*, so a site that blocks us, has moved, or was listed under a guessed
        # domain can still be found on its ATS — and with a hand-assembled input list, a
        # wrong domain is the most likely single defect. Returning here skipped that
        # entirely, which quietly turned "I typed the domain wrong" into "no ATS".
        res.error = "homepage unreachable"
        res.hits = probe_api_slugs(company, domain, tier, already=set())
        return res

    base = f"{urlsplit(resp.url).scheme}://{urlsplit(resp.url).netloc}"
    pages: list[tuple[str, str]] = [(resp.url, resp.text)]
    visited: set[str] = {resp.url}
    seen: set[tuple[str, str]] = set()

    def harvest(url: str, html: str) -> None:
        if _has_jsonld_jobposting(html):
            res.jsonld = True
        for ats, token, extra in _scan(html):
            if (ats, token) in seen:
                continue
            seen.add((ats, token))
            res.hits.append(Hit(company=company, domain=domain, tier=tier, ats=ats,
                                token=token, extra=extra, found_on=url))

    harvest(resp.url, resp.text)

    # Candidate careers pages: linked from the homepage, then the usual paths, then the
    # `careers.`/`kariera.` subdomains that often front an ATS directly.
    queue = list(_careers_urls(resp.url, resp.text))
    # All of them, not a slice: the first eight are the Czech spellings, and cutting the
    # list there meant `/careers` and `/en/careers` were never requested — which lost
    # Kiwi.com, whose board link lives on exactly that path. `budget` bounds the walk.
    queue += [base + p for p in CAREERS_PATHS]
    queue += [f"https://{sub}.{domain}" for sub in ("careers", "kariera", "jobs")]

    budget = 10
    depth2: list[str] = []
    for url in queue:
        if budget <= 0 or any(h.ats in SUPPORTED for h in res.hits):
            break
        if url in visited:
            continue
        visited.add(url)
        page = _get_page(url)
        if page is None:
            continue
        budget -= 1
        pages.append((page.url, page.text))
        harvest(page.url, page.text)
        if not res.hits:
            depth2 += [u for u in _careers_urls(page.url, page.text) if u not in visited]

    for url in depth2[:6]:
        if budget <= 0 or any(h.ats in SUPPORTED for h in res.hits):
            break
        visited.add(url)
        page = _get_page(url)
        if page is None:
            continue
        budget -= 1
        harvest(page.url, page.text)

    for hit in res.hits:
        _verify(hit)

    # Whatever the page walk could not see — which on a client-rendered careers page is
    # everything — is worth one direct API probe per ATS.
    res.hits += probe_api_slugs(company, domain, tier,
                                already={h.ats for h in res.hits if h.jobs > 0})
    for hit in res.hits:
        hit.jsonld = res.jsonld
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", default="scripts/cz_companies.csv")
    ap.add_argument("--out", default="scripts/ats_discovery_results.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    rows = []
    with open(args.companies, encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            rows.append((row["company"], row["domain"], row["tier"]))
    if args.limit:
        rows = rows[: args.limit]

    logger.info("Probing %d companies with %d workers...", len(rows), args.workers)
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, *r): r for r in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            company, domain, _ = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:                       # noqa: BLE001
                res = Result(company=company, domain=domain, tier="", error=str(exc))
            results.append(res)
            live = [h for h in res.hits if h.jobs > 0]
            mark = ("OK " if live else ("?  " if res.hits else ("ld " if res.jsonld else "-  ")))
            detail = ", ".join(f"{h.ats}:{h.token}({h.jobs})" for h in res.hits) or res.error
            logger.info("[%3d/%d] %s %-32s %s", i, len(rows), mark, company, detail)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "domain", "tier", "ats", "token", "extra", "jobs",
                    "supported", "found_on", "jsonld"])
        for res in sorted(results, key=lambda r: (r.tier or "9", r.company)):
            for h in res.hits:
                w.writerow([h.company, h.domain, h.tier, h.ats, h.token, h.extra, h.jobs,
                            "yes" if h.ats in SUPPORTED else
                            ("RESTRICTED" if h.ats in RESTRICTED else "no"),
                            h.found_on, "yes" if h.jsonld else ""])

    live = [h for r in results for h in r.hits if h.jobs > 0]
    logger.info("\n%d companies probed, %d live boards found, %d on supported ATSes.",
                len(results), len(live), len([h for h in live if h.ats in SUPPORTED]))
    logger.info("Wrote %s", out)


if __name__ == "__main__":
    main()
