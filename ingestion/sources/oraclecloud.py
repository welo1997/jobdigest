"""Oracle Recruiting Cloud — enterprise career sites via the candidate-experience API.

Oracle Recruiting Cloud (ORC) is what the large industrials run, the way Workday is what the
large tech and pharma employers run. The endpoint below is the same one a company's own
careers page calls from the browser:

    GET https://{tenant}.fa.{region}.oraclecloud.com/hcmRestApi/resources/latest/
        recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList
        &finder=findReqs;siteNumber={site},limit=200,offset=N

Five things shape this adapter, and four of them are the opposite of the Workday one:

**A site is a `(tenant, region, site)` triple and none of it is derivable.** The tenant is an
opaque Oracle-assigned string — Emerson's is `hdjq`, Vertiv's `egup`, Honeywell's `ibqbjb`.
The *region* is not `us2` by default: Honeywell's is `ocs`, which no amount of guessing would
have produced, and it was found only by reading the company's own careers page. The site
number is usually `CX_1`, but Vertiv's is `CX` and Cummins' is `CX_1001`. Discovery is
`scripts/discover_ats.py`, whose `oraclecloud` fingerprint captures all three; it used to
capture the tenant alone and drop the region, which is why every oraclecloud row in
`scripts/ats_discovery_results.csv` sat unverified at `jobs = -1`.

**The company name has to come from this list, because the API does not carry it.**
`LegalEmployer`, `Organization` and `BusinessUnit` are present in the schema and null in the
data on every tenant measured. So unlike Greenhouse or Recruitee, where the board names its
owner, the name beside each triple below is load-bearing data and not a comment.

**This is NOT an N+1 adapter, and that is why there is no keyword ceiling.** The list row
already carries `ShortDescriptionStr` — 87% non-empty, median 401 characters, capped by
Oracle at 1 000. SmartRecruiters and Workday need a `q`/`searchText` parameter to bound their
per-posting detail calls; there are no detail calls here, so that ceiling would be bounding
nothing. It was measured rather than assumed: filtering with Workday's nine `SEARCH_TERMS`
cost nine times the requests, removed only 22% of the corpus, and *lowered* the EU-27 share
from 16% to 12% — the terms are English tech phrases and these are industrial employers, so
the filter cut disproportionately into exactly the European inventory subscribers can take.
A full pull is 5–12 requests per tenant and is complete.

**Page by the count received, never the count requested.** Oracle caps a response at 200 rows
whatever `limit` says — asking for 500 returns 200. Striding by the requested size would skip
300 postings a page while every response still looked healthy, which is the Himalayas bug
exactly (capped at 20 for its whole life, `offset += PAGE_SIZE`).

**Old requisitions linger, so `MAX_AGE_DAYS` is real.** `query_shortlist_meta` orders by
`first_seen_at`, so a two-year-old posting ingested today sorts to the top of the freshest
bucket and is emailed as new — The Muse's problem. Vertiv holds 227 postings older than 180
days with a tail to 791, and Cummins' reaches 1 293. The Muse's 45 days is deliberately *not*
copied: its median was 17 days and it never expires anything, whereas ORC medians here are
26–43 days and most tenants do expire, so 45 would delete live inventory rather than zombies.
180 drops 6% and every one of them is genuinely stale. An undated posting is dropped, the
same call The Muse makes and the opposite of this repo's usual "unknown is kept" — with
`PostedDate` present on essentially every row, keeping the handful that lack one buys nothing
and costs the freshness guarantee.

`robots.txt` returns 404 on all four hosts (verified 2026-08-04), i.e. allow-all. Like the
other ATS adapters here — Greenhouse, Lever, Ashby, SmartRecruiters, Workday — this calls a
documented public API rather than crawling pages, so it throttles per host but does not gate
on robots the way the HTML scrapers do.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

HEADERS = {**politeness.HEADERS, "Accept": "application/json"}

#: (tenant, region, site number, company name). Verified live 2026-08-04 with the whole-site
#: posting count each carried, and identity-checked against the postings themselves rather
#: than accepted on an HTTP 200 — the lesson from the 2026-08-03 discovery run, where 110
#: live boards yielded 34 genuine ones and four BambooHR slugs all resolved to the same
#: London requisition. Here the risk is lower by construction: the tenant is not a guessable
#: slug but an opaque string read off the employer's own careers page.
SITES: list[tuple[str, str, str, str]] = [
    ("egup", "us2", "CX", "Vertiv"),            # 2251 — US/IN/MX/IT, 16 CZ+SK (Nové Mesto,
    #                                             Volyně). Site is `CX`, not `CX_1`.
    ("ibqbjb", "ocs", "CX_1", "Honeywell"),     # 1308 — US/IN/RO, 14 SK (Stará Turá,
    #                                             Bratislava). Region `ocs` — read, not guessed.
    ("hdjq", "us2", "CX_1", "Emerson"),         #  811 — US/IN/HU/RO, 2 CZ+SK (Brno)
    ("fa-eonj-saasfaprod1", "ocs", "CX_1001", "Brembo"),
    #    140 — the best EU ratio of the five at 42% (31 IT, 11 PL), and the reason to keep
    #    sweeping: Italian and Polish inventory is thin everywhere else in this stack.
    #    Identity-checked against Brembo's real plants — Stezzano, Curno, Mapello, Bergamo,
    #    and Dąbrowa Górnicza. Note the tenant shape: Oracle's newer provisioning names look
    #    like `fa-eonj-saasfaprod1`, not the short `hdjq` form, so anything matching tenants
    #    must allow hyphens. Only 30% carry a description, the weakest here.
    ("eofe", "us2", "BNY-Careers", "BNY Mellon"),
    #   1568 — added 2026-08-04 from the Polish discovery pass, and the largest single board
    #   found in it. Real per-posting country codes (US/GB/IN/IE/DE/LU/BR), so the location
    #   gate can act on it. **But 0 of 25 sampled rows carried a `ShortDescriptionStr`**,
    #   against the 86% that makes this adapter worth having elsewhere — so this tenant
    #   contributes titles and locations, not text the matcher can read. Recorded rather than
    #   assumed from the source's general behaviour. Note the site slug is a *name*,
    #   `BNY-Careers`, not the usual `CX_n` — one more thing that cannot be derived.
    ("efuf", "em2", "CX_1", "Amplifon"),
    #    698 — Milan hearing-care group, added 2026-08-04 from the Italian pass. Sampled
    #    countries BE/DE/CH/US/AU/NZ, so it is a genuine multi-EU board rather than an
    #    Italy-only one. Region `em2`, which no other tenant here uses.
    ("fa-ewwx-saasfaprod1", "ocs", "CX_1", "Nexi"),
    #     84 — Italian payments group, and the best EU spread of any Oracle tenant here:
    #     PL 4, CH 2, DE 2, HR 1, DK 1 in a sample of 10. Polish inventory from an Italian
    #     employer is exactly what the country-by-country search keeps failing to predict.
    ("hdbs", "em2", "CX_1001", "Illycaffè"),
    #      3 — Trieste, and all three roles are in Trieste. Tiny, but it is the only board in
    #      the Italian pass whose inventory is *entirely* Italian, and it costs one request.
    ("hdjd", "em3", "CX_1001", "Cummins"),      #   17 — a Middle East regional site (SA/AE),
    #   not Cummins' global board, which no public tenant answered for. Kept because it is
    #   correct and costs one request; it contributes no EU inventory, so if it ever needs
    #   justifying, that is the number to weigh.
    ("ebza", "em2", "CX_1001", "Telenet"),
    #     69 — Mechelen, Belgium. From the thin-country pass, 2026-08-04, and the one Oracle
    #     board in it. **The site number was verified the way this file's warning says to**:
    #     the invented `CX_999` answers with 91 — the tenant's whole set, because Oracle
    #     ignores an unrecognised `siteNumber` — while `CX_1001` returns a strict subset of
    #     69. A live count proves the tenant; only the difference between the two proves the
    #     site.
    # CZ expansion pass, 2026-08-06. Two industrials whose Czech engineering slice no other
    # adapter reaches — the same rationale as Vertiv/Honeywell/Emerson above. Both site slugs
    # were read off the employer's own careers page, and both are single-site tenants: the
    # bogus `CX_99999` returns the identical count, so the slug is not a filter here and a full
    # pull already yields every Czech row. The non-CZ bulk (US/Asia) resolves to non-selectable
    # countries and is dropped by the location gate, so it never reaches a shortlist.
    ("hctz", "us2", "CX_1001", "onsemi"),
    #    657 — US/PH/IN/MY dominate, but **29 CZ**, and they are the prize: onsemi's Brno site
    #    is a full analog/digital chip-design centre (ASIC/RTL, memory architects, design
    #    directors, analog design engineers) plus engineering at Rožnov pod Radhoštěm. ~5 of
    #    the 29 are fab operators/shift-leads; the rest are senior engineering nothing else here
    #    carries. Identity-checked against the postings' own Czech locations.
    ("ehth", "em2", "CX_2001", "Garrett Motion"),
    #    107 — the best CZ/SK ratio of the industrials at 28%: **22 CZ + 8 SK**, Garrett's Brno
    #    turbocharger R&D/engineering centre. CN/US/RO/IN/MX make up the rest and gate out.
    # IT expansion pass, 2026-08-07. Oracle turned out to be where Italian employers actually
    # are — 5 of the 11 boards this pass found, and the largest of them. Every site slug was
    # checked against the bogus `CX_99999`, per this file's own warning: **all five return the
    # identical count**, so each is a single-site tenant and the slug is not a filter. Italy's
    # non-ATS routes are all closed (Cliclavoro's search moved into the SPID-gated SIISL, the
    # Ministry's open data is statistics, InfoJobs.it has shut down, both Italian ATSes
    # disallow their own job pages in robots), so curated employers are the whole route.
    ("fa-etjb-saasfaprod1", "ocs", "CX_1", "Bip"),
    #    367 — **the largest genuinely Italian board found in either Italian pass.** Milan and
    #    Rome consultancy; 8 of 8 sampled Italian, and the titles are the product's own
    #    segment (IT project managers, functional analysts in banking/insurance, finance
    #    transformation). Note the newer `fa-…-saasfaprod1` tenant shape, as with Brembo.
    ("iaagmj", "ocs", "GG", "Golden Goose"),
    #     58 — Venezia (IT) corporate ICT/finance + Paris/Cannes (FR), Wien (AT), Bucharest
    #     (RO). Honest about what it is: 5 of 8 sampled are store retail (sales assistants,
    #     sneaker makers), so roughly half of it is not knowledge work. Kept because Oracle is
    #     not N+1 — one request buys the Venice corporate roles and a real EEA retail spread.
    ("hdeh", "em3", "CX_1", "A2A"),
    #     53 — Brescia/Milano utility. Same caveat, sharper: genuine engineering (process
    #     engineers, waste-to-energy boiler engineers, an AI developer) sitting beside waste
    #     collection drivers and plant operators. The ISCO 1–3 problem `mpsv` filters for, in a
    #     source that has no such filter — but it is 53 rows, not 32 000.
    ("iacrgs", "ocs", "CX_1", "Aeroporti di Roma"),
    #     20 — Rome airports. The cleanest of the five: all 8 sampled are Roma and all are
    #     engineering/PM (infrastructure design, electrical systems, PMO). Site read as `ocs`
    #     with no slug on the careers page; `CX_1` verified to return the tenant's full 20.
    ("eizj", "em2", "CX", "Mondadori"),
    #      5 — Milano publishing (Rizzoli Libri / Rizzoli Education). Tiny and internship-heavy,
    #      kept for the same reason as Illycaffè: entirely Italian and it costs one request.
    # GB expansion pass, 2026-08-07. Site checked against the bogus `CX_99999`: identical count,
    # so a single-site tenant and the slug is not a filter.
    ("fa-eqid-saasfaprod1", "ocs", "CX_1", "Marks & Spencer"),
    #    695 — and **not the retail board it looks like**. `workday:jlp` (John Lewis/Waitrose)
    #    was rejected in the same pass for being Salisbury and Petersfield shop floors; M&S's
    #    sample is Senior Software Engineer - Loyalty, Head of Software Engineering - Mobile
    #    Apps, Lead Decision Science Analyst and Senior Finance Manager alongside the store
    #    roles — a genuine digital org. Two caveats: `PrimaryLocation` is the bare string
    #    "United Kingdom" on every row, so no city ever resolves; and Oracle is not N+1, so
    #    695 postings cost about four requests, which is what makes the store rows affordable.
    # BE pass, 2026-08-07. Site verified against the bogus `CX_99999`: identical 150, so a
    # single-site tenant.
    ("don", "em2", "CX_1003", "Euroclear"),
    #    150 — Brussels-headquartered and the board is **Polish**: 7 of 8 sampled are Poland
    #    (Kraków tech centre — network security, CyberArk, platform engineering, an IT project
    #    manager) with one Euroclear Nederland row. Probed as a Belgian employer and kept for
    #    the Polish engineering, which is the pattern this whole session kept turning up.
    #    The tenant is the opaque three-letter `don`, derivable from nothing.
    # NO pass, 2026-08-07.
    ("ecyq", "em2", "CX_1", "DNV"),
    #    205 — DNV (Høvik, NO), and the board is European rather than Norwegian: Amsterdam,
    #    Bristol, Milano 2, Hamburg, London, plus Austin and Mexico City. Certification and
    #    advisory work — OT security consultant, data-science innovator, wind-blade
    #    certification engineer — so it is ISCO 1-2 throughout, and Oracle is not N+1, so 205
    #    postings cost about two requests.
    #    **The site number is unverifiable on this tenant, per the Vertiv lesson**: `CX_1`,
    #    `CX` and the invented `CX_999` all return the identical 205 rows, so Oracle is ignoring
    #    the parameter and 205 is the whole tenant. Recorded rather than presented as a checked
    #    site — the number in this comment is the tenant total, which is the only thing that
    #    can be compared against on a re-probe.
    # `fa-evem-saasfaprod1/ocs/CX_1` (Posten Bring, 18) was **rejected on inventory, not
    # identity**: it is genuinely Posten Bring and 17 of 18 rows are Norwegian, which makes it
    # the most tempting rejection of the run — but the roles are yrkessjåfør, varebilsjåfør,
    # terminalarbeider and postbud. Delivery driving is a category the taxonomy does not model,
    # so it would land in the widened retrieval path that drops the recall predicate entirely.
    # Same call as `workday:jlp`, and the same reason.
]

#: Oracle's own ceiling. Asking for more returns 200; `_page` strides by what it receives.
PAGE_SIZE = 200

#: Hard stop on pages per tenant, so a tenant whose `TotalJobsCount` disagrees with its own
#: paging cannot spin forever. 60 pages is 12 000 postings — far above the largest site here.
MAX_PAGES = 60

#: See the module docstring: 180, not The Muse's 45, and not "keep everything".
MAX_AGE_DAYS = 180

_TIMEOUT = 40

#: Oracle's workplace vocabulary. Only `ORA_REMOTE` may set `remote_signal`: that flag exempts
#: a posting from the location gate entirely, so `ORA_HYBRID` must never be folded in — two
#: days a week in Nové Mesto is still a commute to Nové Mesto. The label field is `''` rather
#: than null when unset and 64% of rows are unset, so absence is read from the code.
REMOTE_CODE = "ORA_REMOTE"


#: Oracle's descriptions are plain text — no tags, no entities — but they are peppered with
#: non-breaking spaces: 221 of them across 200 Vertiv rows, in the middle of sentences
#: ("The\xa0Sr. Test Engineer\xa0assists"), plus newlines. U+00A0 is not whitespace to every
#: tokeniser, so left alone it can glue two words into one term in the shortlist's full-text
#: query, and it reads as a missing space in the digest email. Python's `\s` matches U+00A0
#: (and U+202F) under the default Unicode rules, so collapsing runs folds them to ordinary
#: spaces on its own — no separate replace, and no invisible literal in this file to delete
#: by accident. Same normalisation `recruitee._text` applies.
_WS = re.compile(r"\s+")


def _clean(text: Optional[str]) -> Optional[str]:
    """Plain text with non-breaking spaces folded and whitespace runs collapsed."""
    if not text:
        return None
    # The escape is deliberate: a literal U+00A0 here is indistinguishable from a space on
    # screen, so a later edit could delete the substitution without anything looking wrong.
    return _WS.sub(" ", text).strip() or None


def _posted(raw: Optional[str]) -> Optional[date]:
    """`PostedDate` as a date. Oracle sends ISO `YYYY-MM-DD`, sometimes with a time."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


class OracleCloudSource(BaseSource):
    """Oracle Recruiting Cloud across a curated list of enterprise career sites."""

    def __init__(self, sites: Optional[list[tuple[str, str, str, str]]] = None,
                 max_age_days: int = MAX_AGE_DAYS):
        self._sites = sites if sites is not None else SITES
        self._max_age_days = max_age_days

    @property
    def source_name(self) -> str:
        return "oraclecloud"

    # ------------------------------------------------------------------ fetch --
    def fetch(self) -> list[dict]:
        # Tenants run concurrently; paging stays sequential *within* one, because each page's
        # result is what decides whether there is another. Each tenant is its own host, so
        # `politeness.throttle` spaces them independently rather than serialising the run.
        with ThreadPoolExecutor(max_workers=4) as pool:
            per_site = pool.map(self._site, self._sites)

        out: list[dict] = []
        for rows in per_site:
            out.extend(rows)
        logger.info("OracleCloud: %d postings across %d sites", len(out), len(self._sites))
        return out

    def _site(self, site: tuple[str, str, str, str]) -> list[dict]:
        """Every page of one tenant, deduplicated by requisition id."""
        tenant, region, site_no, company = site
        found: dict[str, dict] = {}
        offset = 0
        for _ in range(MAX_PAGES):
            rows, total = self._page(tenant, region, site_no, offset)
            if not rows:
                break
            for row in rows:
                rid = row.get("Id")
                if not rid:
                    continue
                # Oracle repeats a requisition across page boundaries occasionally (1 in 812
                # on Emerson). Keyed rather than appended, so a repeat cannot become two rows.
                row["_tenant"], row["_region"] = tenant, region
                row["_site"], row["_company"] = site_no, company
                found.setdefault(str(rid), row)
            # Stride by what arrived, not by PAGE_SIZE: Oracle caps the response at 200
            # regardless of what `limit` asked for.
            offset += len(rows)
            if total and offset >= total:
                break
        logger.info("OracleCloud %s (%s.%s/%s): %d", company, tenant, region, site_no,
                    len(found))
        return list(found.values())

    def _page(self, tenant: str, region: str, site_no: str,
              offset: int) -> tuple[list[dict], int]:
        url = (f"https://{tenant}.fa.{region}.oraclecloud.com/hcmRestApi/resources/latest/"
               f"recruitingCEJobRequisitions")
        finder = f"findReqs;siteNumber={site_no},limit={PAGE_SIZE},offset={offset}"
        try:
            politeness.throttle(url)
            resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT, params={
                "onlyData": "true", "expand": "requisitionList", "finder": finder,
            })
            if resp.status_code != 200:
                # Loud: this list is short and hand-verified, so a tenant going dark is a real
                # loss rather than routine noise, and a silent zero is the failure this repo
                # keeps rediscovering.
                logger.warning("OracleCloud %s.%s/%s: HTTP %s", tenant, region, site_no,
                               resp.status_code)
                return [], 0
            items = resp.json().get("items") or []
            if not items:
                return [], 0
            return items[0].get("requisitionList") or [], int(items[0].get("TotalJobsCount") or 0)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("OracleCloud %s.%s/%s failed: %s", tenant, region, site_no, exc)
            return [], 0

    # -------------------------------------------------------------- normalize --
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        out: list[JobPosting] = []
        stale = undated = 0
        for row in raw_items:
            rid, title = row.get("Id"), (row.get("Title") or "").strip()
            tenant, region = row.get("_tenant"), row.get("_region")
            site_no = row.get("_site")
            if not rid or not title or not tenant:
                continue

            posted = _posted(row.get("PostedDate"))
            if posted is None:
                undated += 1
                continue
            if (date.today() - posted).days > self._max_age_days:
                stale += 1
                continue

            url = (f"https://{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/"
                   f"en/sites/{site_no}/job/{rid}")
            country = (row.get("PrimaryLocationCountry") or "").strip().upper() or None
            out.append(JobPosting(
                posting_id=make_posting_id(url),
                source=self.source_name,
                title=title,
                # From the curated list: every company-name field Oracle exposes is null.
                company=row.get("_company"),
                url=url,
                description=_clean(row.get("ShortDescriptionStr")),
                location=row.get("PrimaryLocation"),
                # A real per-posting ISO code, not a source-level constant — these tenants
                # hire globally and the Arbeitnow mistake was exactly such a constant.
                country_code=country,
                # Structured, employer-set, and checked against a closed vocabulary. Hybrid is
                # deliberately excluded: `remote_signal` skips the location gate.
                remote_signal=(row.get("WorkplaceTypeCode") == REMOTE_CODE),
                salary_raw=None,        # Oracle exposes no compensation field on this API.
                currency=None,
                posted_at=posted,
                source_category=row.get("JobFamily") or row.get("JobFunction") or None,
            ))
        if stale or undated:
            logger.info("OracleCloud: dropped %d older than %d days and %d undated",
                        stale, self._max_age_days, undated)
        return out
