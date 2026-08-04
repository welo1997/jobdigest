"""SmartRecruiters ATS — fetch jobs from company career sites (no key).

Public endpoints, same shape as the Greenhouse / Lever / Ashby adapters:

    list:   https://api.smartrecruiters.com/v1/companies/{tenant}/postings?q=&limit=100
    detail: https://api.smartrecruiters.com/v1/companies/{tenant}/postings/{id}

Why this source exists: the other ATS adapters are curated *startup* boards, which skew
US/UK and remote-first. SmartRecruiters is what large European employers run, so it is the
one adapter that reaches Bulgaria, Romania, Hungary, Finland and Poland at any volume —
Delivery Hero alone posts across a dozen EU countries from a single tenant.

Two things about the API shape drive the design:

**The list endpoint carries no description and no URL.** It gives title, company, location,
``releasedDate`` and a ``function`` label; everything the matcher actually reads lives behind
a per-posting detail call. So this adapter is the only one here that issues N+1 requests, and
it is deliberately bounded — see ``MAX_DETAILS``. A posting whose detail call fails is
dropped rather than stored description-less: an empty description would be indexed into
``search_tsv`` as title-only and reach a subscriber with nothing for the model to judge.

**`q=` is what keeps this tenant list usable.** Bosch alone has 4 715 open postings, nearly
all of them manufacturing and logistics. Fetching per role term instead of per tenant is what
makes the difference between a few hundred relevant rows and 7 500 mostly-irrelevant ones
consuming shortlist slots for every subscriber.
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

API = "https://api.smartrecruiters.com/v1/companies"
HEADERS = politeness.HEADERS

# Verified live 2026-08-01, with the posting count each tenant carried on that day. These
# are whole-company totals, not what we ingest — the `q` terms below cut them down.
TENANTS = [
    "BoschGroup",     # 4715 — DE/HU/PL/RO/CZ engineering and IT
    "DeliveryHero",   # 1075 — Glovo/foodpanda, BG/ES/DE/TR + APAC
    "Continental",    # 902  — DE/RO/CZ/HU automotive software
    "Wise",           # 406  — GB/EE/HU fintech
    "Konecranes",     # 373  — FI/DE/NL industrial
    "Sportradar",     # 70   — AT/SI/DE sports data
    "Visa",           # 2
    "Thales",         # 2
]

#: Rows per list page. SmartRecruiters caps a response at 100 whatever `limit` says — asking
#: for 500 returns 100 — so the loop pages by the count **received**. This is the Himalayas
#: bug, and it was live here: `_list` issued one `limit=100` request per (tenant, term) with
#: no `offset` at all. "software engineer" at BoschGroup reports `totalFound=1866` and we
#: took the first 100 of it, on every run, since the adapter was written.
PAGE_SIZE = 100

#: Runaway guard on the paging loop, not a trimmer: the largest tenant is 4 714 postings.
MAX_LIST_PAGES = 150

#: Which titles are worth a detail call.
#:
#: Until 2026-08-04 this job was done by SmartRecruiters' own `q` parameter, and it was doing
#: it badly in both directions. `q` is a **ranked full-text search, not a filter** — at
#: BoschGroup "software engineer" returns 1 866 of 4 714 postings and "data engineer" returns
#: 2 109, and both hand back the same first row. So the term matrix neither bounded the pull
#: nor selected tech roles; it just truncated an arbitrary ranked slice at 100.
#:
#: Paging the tenant instead costs *fewer* list requests (88 against 96, measured across all
#: eight tenants) and returns all 7 503 postings, so relevance becomes a decision we make
#: locally on a title we can see. 2 607 of the 7 503 match. What the rest are is the reason
#: this filter exists at all: Bosch is an industrial employer, and the dropped rows are
#: "Manifold Assembler I", "MSR-Techniker Gebäudeautomation", "HVAC Specialist" and
#: "Monteur/Prüfer". Letting those in would put shop-floor work into the *widened* retrieval
#: path, which drops the recall predicate entirely — the same reason `mpsv` keeps only ISCO
#: major groups 1–3.
#:
#: Matched on word boundaries rather than substrings, because the first draft of this list
#: used `"ai "` and silently dropped six postings titled "AI/ML Expert" — a false negative is
#: the safe error here, but not one worth making by accident.
TECH_TITLE = re.compile(
    r"\b("
    r"software|developer|engineer(ing)?|engineers|programmer|architect|analyst|scientist"
    r"|data|analytics|machine learning|ml|ai|artificial intelligence|nlp|llm"
    r"|devops|sre|site reliability|platform|infrastructure|cloud|kubernetes|linux"
    r"|security|cyber|iam|network|database|dba|sap|erp|salesforce|crm"
    r"|qa|quality assurance|test(ing|er)?|automation|embedded|firmware|iot"
    r"|frontend|front.end|backend|back.end|full.?stack|web|mobile|android|ios"
    r"|java|python|javascript|typescript|golang|c\+\+|\.net|react|node"
    r"|product (manager|owner|lead)|scrum|agile coach|tech lead|cto|it"
    r"|ux|ui|designer|design (lead|system)|research(er)?"
    r"|informatik|entwickler|softwareentwickl\w*|programmierer"
    r")\b", re.I)

#: Hard ceiling on the per-posting detail calls a single run may issue — a runaway guard, not
#: a routine trimmer. What it exists for is a tenant bulk-importing thousands of rows and
#: turning one nightly run into an unbounded crawl of a free API. When it does bite, the
#: newest postings are kept, because those are the ones a daily digest is for.
#:
#: Measured 2026-08-04, paging every tenant: 7 503 postings in 88 list requests and 68 s, of
#: which 2 607 pass `TECH_TITLE`. So a normal run sits well under this and the guard stays a
#: guard — which was not true of the old 1 800 against a truncated 1 671, where the *list*
#: stage was silently doing the trimming.
MAX_DETAILS = 6000

#: Drop anything released longer ago than this. The shortlist orders by `first_seen_at`, so a
#: posting we meet for the first time today ranks as brand new whatever its age — a stale row
#: does not look stale downstream, it looks fresh. Generous, because these are live ATS
#: listings that the employer removes when filled; it is a backstop, not the primary filter.
MAX_AGE_DAYS = 120

# SmartRecruiters `function.label` -> role_category hint. Used by `taxonomy.classify` only
# when the title itself cannot be classified, so a wrong-but-plausible mapping costs little
# and a missing one costs nothing. `None` = no confident mapping; anything not listed (Legal,
# Manufacturing, Supply Chain, …) is deliberately absent rather than forced into a bucket.
FUNCTION_HINTS: dict[str, Optional[str]] = {
    "information technology": "software_engineering",
    "engineering": None,  # at Bosch/Continental this is mechanical far more often than software
    "product management": "product",
    "design": "design",
    "marketing": "other_tech_function",
    "sales": "other_tech_function",
    "finance": "other_tech_function",
    "accounting": "other_tech_function",
    "human resources": "other_tech_function",
    "customer service": "other_tech_function",
    "business development": "other_tech_function",
    "consulting": "other_tech_function",
}


class SmartRecruitersSource(BaseSource):
    """SmartRecruiters public postings API across a curated list of tenants."""

    def __init__(self, tenants: Optional[list[str]] = None,
                 max_details: int = MAX_DETAILS):
        self._tenants = TENANTS if tenants is None else tenants
        self._max_details = max_details

    @property
    def source_name(self) -> str:
        return "smartrecruiters"

    # ------------------------------------------------------------------ fetch --
    def fetch(self) -> list[dict]:
        summaries: dict[str, dict] = {}
        for tenant in self._tenants:
            for item in self._list(tenant):
                pid = str(item.get("id") or "")
                if pid and pid not in summaries:
                    item["_tenant"] = tenant
                    summaries[pid] = item

        # Relevance is decided here, on a title, rather than by SmartRecruiters' ranking.
        relevant = [s for s in summaries.values() if self._is_tech(s.get("name"))]
        fresh = [s for s in relevant if self._is_fresh(s)]
        # All three numbers, because each one is a different question and the gap between the
        # first two is the whole reason the title filter exists.
        logger.info("SmartRecruiters: %d listed, %d tech-titled, %d inside %d days",
                    len(summaries), len(relevant), len(fresh), MAX_AGE_DAYS)
        # Newest first, so the MAX_DETAILS cut keeps the postings a daily digest wants.
        fresh.sort(key=lambda s: str(s.get("releasedDate") or ""), reverse=True)
        if len(fresh) > self._max_details:
            logger.warning("SmartRecruiters: %d postings over the %d detail cap, keeping newest",
                           len(fresh), self._max_details)
            fresh = fresh[: self._max_details]

        logger.info("SmartRecruiters: fetching %d details", len(fresh))
        with ThreadPoolExecutor(max_workers=8) as pool:
            detailed = list(pool.map(self._detail, fresh))

        out = [d for d in detailed if d]
        logger.info("SmartRecruiters: fetched %d postings with descriptions", len(out))
        return out

    def _list(self, tenant: str) -> list[dict]:
        """Every posting a tenant has, paged by the count received."""
        out: list[dict] = []
        offset = 0
        for _ in range(MAX_LIST_PAGES):
            try:
                resp = requests.get(f"{API}/{tenant}/postings",
                                    params={"limit": PAGE_SIZE, "offset": offset},
                                    headers=HEADERS, timeout=30)
                if resp.status_code != 200:
                    logger.warning("SmartRecruiters %s: HTTP %d at offset %d",
                                   tenant, resp.status_code, offset)
                    break
                payload = resp.json()
                got = payload.get("content", []) or []
            except (requests.RequestException, ValueError) as exc:
                logger.warning("SmartRecruiters %s failed at offset %d: %s", tenant, offset, exc)
                break
            out += got
            # Two rules, and both are the Himalayas bug written down. **Advance by what
            # arrived**, never by `PAGE_SIZE`: the API caps a response at 100 however large a
            # `limit` you send, so a fixed stride would skip four rows in five the moment
            # someone raises the page size. And **a short page is not the end** — only an
            # empty one is, or `totalFound` being reached, which is what saves the extra
            # request that discovering emptiness would otherwise cost.
            if not got:
                break
            offset += len(got)
            total = payload.get("totalFound")
            if isinstance(total, int) and offset >= total:
                break
        else:
            logger.warning("SmartRecruiters %s: hit the %d-page guard at %d postings",
                           tenant, MAX_LIST_PAGES, len(out))
        return out

    @staticmethod
    def _is_tech(title: Optional[str]) -> bool:
        return bool(title) and bool(TECH_TITLE.search(str(title)))

    def _detail(self, summary: dict) -> Optional[dict]:
        tenant, pid = summary.get("_tenant"), summary.get("id")
        try:
            resp = requests.get(f"{API}/{tenant}/postings/{pid}", headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                return None
            detail = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("SmartRecruiters detail %s/%s failed: %s", tenant, pid, exc)
            return None
        detail["_tenant"] = tenant
        return detail

    @staticmethod
    def _is_fresh(item: dict) -> bool:
        released = SmartRecruitersSource._parse_date(item.get("releasedDate"))
        if released is None:
            return True  # unknown age is kept, the same call `resolve_location` makes
        return (date.today() - released).days <= MAX_AGE_DAYS

    # -------------------------------------------------------------- normalize --
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            tenant = item.get("_tenant")
            url = item.get("postingUrl") or item.get("applyUrl") or ""
            if not url and tenant and item.get("id"):
                url = f"https://jobs.smartrecruiters.com/{tenant}/{item['id']}"
            if not url:
                continue

            loc = item.get("location") or {}
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("name"),
                    company=(item.get("company") or {}).get("name") or tenant,
                    url=url,
                    description=self._description(item),
                    location=self._location(loc),
                    country_code=(str(loc.get("country") or "").upper() or None),
                    remote_signal=bool(loc.get("remote")),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item.get("releasedDate")),
                    source_category=self._hint(item),
                )
            )
        logger.info("SmartRecruiters: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _location(loc: dict) -> Optional[str]:
        """The free-text location `geo.resolve_location` and `geo.work_mode` will read.

        The ATS states hybrid as a structured boolean, and `work_mode` reads the bare word
        "hybrid" from the *location* field only (it is far too common in body copy to trust
        there). Prefixing it is therefore how a structured flag reaches the one classifier
        that decides it — and it matters: `remote_signal` exempts a posting from the location
        gate entirely, so a hybrid Berlin role must never arrive claiming to be remote.
        """
        parts = [p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p]
        text = loc.get("fullLocation") or ", ".join(str(p) for p in parts)
        if loc.get("hybrid") and not loc.get("remote"):
            text = f"Hybrid — {text}" if text else "Hybrid"
        elif loc.get("remote"):
            text = f"Remote — {text}" if text else "Remote"
        return text or None

    @staticmethod
    def _description(item: dict) -> Optional[str]:
        sections = ((item.get("jobAd") or {}).get("sections") or {})
        # Company blurb last: it is boilerplate repeated across every posting, and the matcher
        # reads a truncated description, so leading with it would crowd out the actual role.
        order = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
        chunks = [str((sections.get(k) or {}).get("text") or "").strip() for k in order]
        body = "\n\n".join(c for c in chunks if c)
        return body or None

    @staticmethod
    def _hint(item: dict) -> Optional[str]:
        label = str(((item.get("function") or {}).get("label") or "")).strip().lower()
        return FUNCTION_HINTS.get(label)

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
