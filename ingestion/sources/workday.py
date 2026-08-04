"""Workday ATS — fetch jobs from enterprise career sites (no key).

Workday is what most large employers run, which makes it the deepest pool of *located*
inventory available here — the 11 career sites below carried 10 188 open postings when they
were verified. It reaches the kind of employer none of the startup-oriented ATS adapters do:
AstraZeneca, Philips, Sanofi, RELX and Mastercard are all European-headquartered and hire
across the continent.

The public endpoints are the ones a career site's own browser calls:

    list:   POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
            {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}
    detail: GET  https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}

Three consequences shape this adapter:

**A site is a (tenant, host, site) triple, not a name.** The host shard (`wd1`, `wd3`, `wd5`,
`wd12`) and the site slug are both per-customer and unguessable — Adobe's is
`external_experienced`, NVIDIA's is `NVIDIAExternalCareerSite`. A wrong triple is a 404, so
the list below is verified rather than derived.

**The list carries no description, so this is an N+1 adapter** like SmartRecruiters, and
bounded the same way. `searchText` is what makes that affordable: Philips alone has 1 017
open postings and 205 matching "software engineer".

**`locationsText` is not always a location.** A posting open in several offices reads
"7 Locations", which resolves to no country and no city. The detail call gives a real
`location` plus a `country` descriptor, so location is taken from there — see `_location`.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": politeness.USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

#: (tenant, host shard, site slug). Verified live 2026-08-01 with the whole-site posting count
#: each carried — that is the company's total, not what we ingest; `SEARCH_TERMS` cuts it down.
#: A triple that 404s contributes nothing and logs nothing above warning level, so re-probe
#: rather than assume when a company's volume disappears.
SITES: list[tuple[str, str, str]] = [
    ("nvidia", "wd5", "NVIDIAExternalCareerSite"),   # 2000 — US/IL/DE/PL
    ("salesforce", "wd12", "External_Career_Site"),  # 1479 — US/IE/DE/FR/ES
    ("astrazeneca", "wd3", "Careers"),               # 1173 — GB/SE/PL/ES
    ("mastercard", "wd1", "CorporateCareers"),       # 1071 — US/BE/IE/PL
    ("philips", "wd3", "jobs-and-careers"),          # 1017 — NL/DE/PL/US
    ("sanofi", "wd3", "SanofiCareers"),              #  890 — FR/DE/HU/ES
    ("adobe", "wd5", "external_experienced"),        #  834 — US/IE/RO/DE
    ("relx", "wd3", "relx"),                         #  774 — GB/NL/US
    ("autodesk", "wd1", "Ext"),                      #  501 — US/IE/DE
    ("workday", "wd5", "Workday"),                   #  337 — US/IE/DE
    ("evonik", "wd3", "External_Careers"),           #  373 — DE chemicals
    ("equinix", "wd1", "External"),                  #  320 — US/NL/DE datacentres
    ("paypal", "wd1", "jobs"),                       #  112 — US/IE/DE
    # Added 2026-08-03 via scripts/discover_ats.py, which reads the triple off the company's
    # own careers page rather than guessing it — the only method that works here, and the
    # reason these eleven took one run instead of a 2 568-combination sweep. All are large
    # employers with real Czech engineering or shared-service sites.
    ("jci", "wd5", "JCI"),                           # 2635 — Johnson Controls, CZ/US/IT
    ("accenture", "wd103", "AccentureCareers"),      # 2000 — global, large CZ delivery centre
    ("valeo", "wd3", "valeo_jobs"),                  # 1158 — FR/CZ/MX automotive
    ("novartis", "wd3", "Novartis_Careers"),         #  997 — CH/CZ pharma. NOT
    #   `Internal_Careers_for_Acquired_Entities`, which is what the discovery scan first
    #   found: it answers 200 with 603 jobs, so nothing would have flagged it, but it is an
    #   internal board for staff of acquired companies rather than the public one.
    ("msd", "wd5", "SearchJobs"),                    #  831 — US/CZ pharma, Prague IT hub
    ("zebra", "wd501", "Zebra_careers"),             #  244 — US/CZ, Brno R&D
    ("redhat", "wd5", "jobs"),                       #  132 — Brno is its largest non-US site
    ("deloitteie", "wd3", "Experienced_Professionals"),  # 62 — Deloitte Ireland (EU, not CZ)
    ("suse", "wd3", "Jobsatsuse"),                   #   27 — DE/CZ, Prague engineering
    # Second discovery pass, 2026-08-03 (258 CZ+SK companies).
    ("dxctechnology", "wd1", "DXCJobs"),             #  758 — global IT services, CZ delivery
    ("zentiva", "wd3", "Zentiva"),                   #  115 — Prague-headquartered pharma
    ("kyndryl", "wd5", "KyndrylEarlyCareers"),       #   40 — early-careers only. No public
    #   external Kyndryl site answered; six plausible slugs all 404. Junior-weighted, which
    #   suits the digest, but it is a subset — re-probe if a general board turns up.
    # Third pass, 2026-08-04 (141 Polish companies). Every triple below was read off the
    # employer's own careers page and then checked against its postings' locations, because
    # the site slug is the unguessable half — `2`, `Global`, `WDExternal` and
    # `external_career_site` are not derivable from anything.
    ("citi", "wd5", "2"),                            # 2000 — Olsztyn/Warsaw PL among IN/US.
    #   The site slug really is the single character "2".
    ("statestreet", "wd1", "Global"),                # 1128 — Gdańsk/Kraków PL among IN/CN
    ("motorolasolutions", "wd5", "Careers"),         #  924 — Kraków PL, its largest EU site
    ("lighting", "wd3", "jobs-and-careers"),         #  244 — Signify (ex-Philips Lighting).
    #   The tenant is `lighting`, not `signify`; nothing about the brand name reaches it.
    ("nngroup", "wd3", "WDExternal"),                #  219 — Nationale-Nederlanden, NL/RO/HU/PL
    ("cloudflight", "wd103", "external_career_site"),  # 13 — Cloudflight, which acquired the
    #   Polish software house Divante; Divante's careers page is what led here. Small, but it
    #   is AT/DE/PL inventory and costs one query. Note the `wd103` shard.
    # Fourth pass, 2026-08-04 (84 SK + 85 IT + 84 ES companies). Two Slovak boards were found
    # and both rejected: `att` (1 223) and `adient` (197) are the real companies, but every
    # sampled posting was US or Mexican — no Slovak roles at all, so they would add US
    # inventory to a corpus already 24% US rather than fix the country they were sought for.
    ("santander", "wd3", "SantanderCareers"),        #  867 — global bank, EU sites incl. Wien
    ("leonardocompany", "wd3", "LeonardoCareerSite"),#  683 — Italian defence group; the
    #   sampled roles were UK (Luton, Southampton), so it is GB-weighted despite the HQ.
    ("prysmiangroup", "wd3", "Careers"),             #  575 — Milan HQ, sampled US/BR/AR/CO
    ("bracco", "wd103", "BraccoCareers"),            #  111 — Milan pharma; DE (Singen)/US/CN
    ("eng", "wd3", "ExternalCareers"),               #   70 — Roma / Milano. Genuinely Italian.
    ("teamsystem", "wd103", "TeamSystem"),           #   49 — Milano / Napoli / Padova
    ("cerved", "wd3", "Cerved"),                     #   39 — San Donato Milanese / Milano
    # Fifth pass, 2026-08-04 (210 employers across the 19 selectable countries that hold under
    # 300 active postings each). Every triple read off the employer's own careers page and
    # then checked against its postings' cities — which is what rejected `wonder`: the slug
    # was found probing for Just Eat Takeaway, the site is literally `Grubhub_Careers`, and
    # all 51 postings are New York / Boston / Chicago. A real board for a different company.
    ("kone", "wd3", "Careers"),                      #  919 — Budapest, Belgrade, Podgorica,
    #   Ludwigsburg, Antalya. Finnish lifts; much of it is service technicians, which
    #   `SEARCH_TERMS` is what keeps out.
    ("nxp", "wd3", "careers"),                       #  805 — Eindhoven / Nijmegen NL.
    #   Note the lower-case site slug; `Careers` is a different (404) site.
    ("infobip", "wd3", "InfobipCareers"),            #  145 — Zagreb HR among São Paulo /
    #   Curitiba. Croatia held 11 active postings.
    ("gn", "wd3", "GN-Careers"),                     #   74 — Ballerup DK + Warszawa.
    #   Denmark held 65.
    ("upm", "wd103", "Careers"),                     #   60 — Wrocław / Kraków / Joroinen FI
    ("materialise", "wd103", "Materialise_Jobs"),    #   46 — Leuven BE + Kyiv + Barcelona
    ("tricentis", "wd1", "Tricentis_Careers"),       #   28 — CZ Prague / NL Amsterdam.
    #   Austrian company, and not one Austrian posting on it — kept for the CZ/NL rows.
    ("prodege", "wd108", "Prodege_Careers"),         #   24 — Athens GR among El Segundo /
    #   Remote California. Found probing for Pollfish, which Prodege acquired: the parent is
    #   the real board, and it is US-weighted with a genuine Greek presence.
    ("workhuman", "wd1", "WorkhumanCareers"),        #   10 — Dublin IE + Framingham MA
]

#: Role terms, matched by Workday's own full-text search. English-only for the same reason as
#: SmartRecruiters: these employers post their tech roles in English across the whole of
#: Europe, and a per-country term list would be a maintenance burden for no measured gain.
SEARCH_TERMS = [
    "data engineer", "data analyst", "data scientist", "machine learning",
    "software engineer", "devops", "cloud", "product manager", "designer",
]

PAGE_SIZE = 20          # Workday's own page size; larger `limit` values are ignored
MAX_PAGES_PER_QUERY = 10  # 200 hits per (site, term)

#: Ceiling on the per-posting detail calls one run may issue. When it bites, the postings
#: Workday itself calls newest are kept — see `_age_rank`.
#:
#: **This was 1 200 and it bit on every single run, not on a bulk import.** Measured
#: 2026-08-04: `scripts/probe_boards.py` reports 19 892 open postings across these sites and
#: production held 1 454 active Workday rows — 7%. The comment claimed the ceiling was sized
#: above a normal run; it was three times below one. Raising it is the cheapest inventory in
#: the repo, because nothing about permission, adapters or discovery changes.
#:
#: What the size is chosen against: one request per (site, term) reports Workday's own
#: `total`, and those totals sum to 74 560 across 38 sites × 9 terms — with heavy overlap,
#: since a posting matching "cloud" often matches "devops" too. `total` itself saturates at
#: 2 000, so accenture and citi are floors rather than counts. 6 000 is set from the time
#: budget rather than that number: gather() runs at 05:00 and the import is at 07:00, a full
#: international run is ~15 min, and the detail calls here are the slowest thing in it.
MAX_DETAILS = 6000

#: Concurrency for the detail stage, raised from 8 on 2026-08-04 because `MAX_DETAILS` at
#: 6 000 does not fit the window at 8.
#:
#: **The window is the 05:00 export against the ~06:00 claude.ai routine, not the 07:00
#: import.** The routine reads whatever `shortlists.json` is in Drive when it wakes; an export
#: still running at 06:00 does not delay it, it misses it, and every subscriber gets yesterday's
#: file or none. That is roughly 60 minutes for the whole of `gather()`, not 120.
#:
#: Measured 2026-08-04 over 240 real detail calls across 12 tenants:
#:
#:     workers=8   5.0 req/s  -> 6 000 details = 20.0 min
#:     workers=16  9.5 req/s  -> 6 000 details = 10.6 min
#:     workers=32 11.5 req/s  -> 6 000 details =  8.7 min
#:
#: 16 is the knee — 32 buys 20% more for double the concurrency. A full run at 8 measured
#: 28.7 min (8 min list, 21 min details); at 16 that is ~19 min, which leaves real margin for
#: the other 20 sources rather than spending the window on one of them.
#:
#: Politeness: a site is its own host (`{tenant}.{shard}.myworkdayjobs.com`) and there are 46
#: of them, so 16 in flight is well under one request per second per employer — below this
#: repo's own `politeness.DEFAULT_DELAY`. Raising this further without re-checking that
#: arithmetic would not be.
DETAIL_WORKERS = 16

#: Workday states age as relative prose on the list ("Posted Today", "Posted 30+ Days Ago").
#: It is far too coarse to filter on, but it orders well enough to decide what survives
#: `MAX_DETAILS`. The real date arrives with the detail call as `startDate`.
_POSTED_DAYS = re.compile(r"posted\s+(\d+)\+?\s*days?", re.I)


class WorkdaySource(BaseSource):
    """Workday CXS API across a curated list of enterprise career sites."""

    def __init__(self, sites: Optional[list[tuple[str, str, str]]] = None,
                 terms: Optional[list[str]] = None,
                 max_details: int = MAX_DETAILS):
        self._sites = SITES if sites is None else sites
        self._terms = SEARCH_TERMS if terms is None else terms
        self._max_details = max_details

    @property
    def source_name(self) -> str:
        return "workday"

    # ------------------------------------------------------------------ fetch --
    def fetch(self) -> list[dict]:
        # Both stages run concurrently, and the list stage is the one that matters: 13 sites
        # x 9 terms is 117 independent queries, and run one at a time they cost ~500 s — more
        # than the 1 200 detail calls that follow. Paging stays sequential *within* a query,
        # because each page's result decides whether there is another.
        queries = [(site, term) for site in self._sites for term in self._terms]
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = pool.map(lambda q: self._query(*q), queries)

        found: dict[str, dict] = {}
        for hits in pages:
            for hit in hits:
                key = f"{hit['_tenant']}{hit.get('externalPath')}"
                found.setdefault(key, hit)

        ordered = sorted(found.values(), key=self._age_rank)
        if len(ordered) > self._max_details:
            logger.warning("Workday: %d postings over the %d detail cap, keeping newest",
                           len(ordered), self._max_details)
            ordered = ordered[: self._max_details]

        logger.info("Workday: %d unique postings across %d sites, fetching details",
                    len(found), len(self._sites))
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            detailed = list(pool.map(self._detail, ordered))

        out = [d for d in detailed if d]
        logger.info("Workday: fetched %d postings with descriptions", len(out))
        return out

    def _query(self, site: tuple[str, str, str], term: str) -> list[dict]:
        """All pages of one (career site, search term) query, tagged with their origin.

        **Workday does not signal the end of a result set** — it keeps serving full pages.
        Measured 2026-08-04: `philips`, whose whole board is 1 017 postings, returned 4 000
        hits for "data engineer" across 200 pages and stopped only because the harness did;
        its nine terms produced 9 304 rows of which 708 were distinct, a 92% duplicate rate,
        and `nvidia` was 68%. So `len(hits) < PAGE_SIZE` is a stop condition that almost
        never fires, and the page ceiling was doing all the work — which means the cost of
        raising it is paid entirely in duplicate requests.

        A page that contributes **no new posting** ends the query. That is the real end of
        the results, it costs one page to discover, and it makes `MAX_PAGES_PER_QUERY` a
        runaway guard again rather than the thing deciding how much we see.
        """
        tenant, host, slug = site
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES_PER_QUERY):
            hits = self._list(tenant, host, slug, term, page * PAGE_SIZE)
            fresh = 0
            for hit in hits:
                path = hit.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                hit["_tenant"], hit["_host"], hit["_site"] = tenant, host, slug
                out.append(hit)
                fresh += 1
            if len(hits) < PAGE_SIZE or fresh == 0:
                break
        return out

    def _list(self, tenant: str, host: str, site: str, term: str, offset: int) -> list[dict]:
        url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        try:
            resp = requests.post(url, headers=HEADERS, timeout=20, json={
                "appliedFacets": {}, "limit": PAGE_SIZE,
                "offset": offset, "searchText": term,
            })
            if resp.status_code != 200:
                logger.warning("Workday %s/%s: HTTP %d", tenant, term, resp.status_code)
                return []
            return resp.json().get("jobPostings", []) or []
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Workday %s/%s failed: %s", tenant, term, exc)
            return []

    def _detail(self, hit: dict) -> Optional[dict]:
        tenant, host, site = hit.get("_tenant"), hit.get("_host"), hit.get("_site")
        path = hit.get("externalPath")
        url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                return None
            info = resp.json().get("jobPostingInfo") or {}
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Workday detail %s%s failed: %s", tenant, path, exc)
            return None
        if not info:
            return None
        info["_tenant"], info["_host"], info["_site"] = tenant, host, site
        info["_path"] = path
        info.setdefault("remoteType", hit.get("remoteType"))
        return info

    @staticmethod
    def _age_rank(hit: dict) -> int:
        """Days since posting, read out of Workday's relative prose. Unknown sorts last."""
        text = str(hit.get("postedOn") or "")
        if re.search(r"posted\s+(today|yesterday)", text, re.I):
            return 0
        match = _POSTED_DAYS.search(text)
        return int(match.group(1)) if match else 9999

    # -------------------------------------------------------------- normalize --
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            tenant, host, site = item.get("_tenant"), item.get("_host"), item.get("_site")
            url = item.get("externalUrl") or (
                f"https://{tenant}.{host}.myworkdayjobs.com/{site}{item.get('_path') or ''}")
            if not item.get("_path") and not item.get("externalUrl"):
                continue

            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=tenant,
                    url=url,
                    description=item.get("jobDescription"),
                    location=self._location(item),
                    # Left to `geo.resolve_location`, which reads the country name this
                    # adapter appends to the location text. Workday's own `country` object
                    # is a display name, not a code, and mapping it here would duplicate
                    # `geo.COUNTRY_ALIASES` — the one table that already knows "Nemecko" and
                    # "Deutschland" are Germany.
                    country_code=None,
                    remote_signal=self._is_remote(item),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item.get("startDate")),
                )
            )
        logger.info("Workday: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _location(item: dict) -> Optional[str]:
        """Detail `location` plus the country name, which the location alone often omits.

        Not the list's `locationsText`: a requisition open in several offices reads
        "7 Locations" there, which names no place at all and resolves to nothing.
        """
        text = str(item.get("location") or "").strip()
        country = str(((item.get("country") or {}).get("descriptor") or "")).strip()
        if country and country.lower() not in text.lower():
            text = f"{text}, {country}" if text else country
        return text or None

    @staticmethod
    def _is_remote(item: dict) -> bool:
        """Only an explicitly fully-remote `remoteType` counts.

        Workday's other values are "Hybrid", "On-site" and "Flexible" — and "Flexible" is the
        trap: it reads permissive but means the employer decides per hire, not that the job
        can be done from another country. `remote_signal` exempts a posting from the location
        gate outright, so anything short of an explicit remote claim must answer no and let
        `geo.work_mode` read the description.
        """
        value = str(item.get("remoteType") or "").strip().lower()
        return value in ("remote", "fully remote", "remote work", "100% remote")

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
