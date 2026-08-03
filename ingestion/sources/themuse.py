"""The Muse — public jobs API (no key), the widest US source in the stack.

    https://www.themuse.com/api/public/jobs?page=N&category=...&location=...

Why this source exists: everything else here is either EU-focused (Arbeitnow, the Czech
boards, most of the ATS tenants) or remote-only (Remotive, RemoteOK, WeWorkRemotely,
Himalayas, Jobicy, WorkingNomads). Nothing covered *located* US postings once Adzuna's
credentials stopped authenticating. The Muse carries ~400 000 listings, heavily US, with a
real category taxonomy and a location filter.

**The freshness filter is the load-bearing part of this adapter.** The Muse never expires
listings: a sample of 160 results across its "Data and Analytics" pages on 2026-08-01 had a
median age of 17 days but a long tail reaching back to March 2025. That tail is dangerous
here rather than merely untidy, because `store.query_shortlist_meta` orders a subscriber's
window by `first_seen_at` — when *we* met the posting, not when it was published. A
17-month-old listing ingested today therefore sorts to the very top of the freshest bucket
and is emailed as new. `posted_at` cannot save it either: it is null for most of the corpus
(jobs.cz, profesia, startupjobs, cocuma), so nothing downstream filters on it. So this
adapter drops stale rows at the source, which is the only place that still knows.

Paging is `descending=false`, which is not a typo: measured against the same sample, the
default order returns markedly fresher results (median 17 days, 60/80 inside 30 days) than
`descending=true` (median 37 days, 38/80). The parameter does not mean what its name
suggests, and the numbers are the reason for the choice.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import requests

from ingestion.base import BaseSource, JobPosting, make_posting_id
from ingestion import politeness

logger = logging.getLogger(__name__)

API = "https://www.themuse.com/api/public/jobs"
HEADERS = politeness.HEADERS

#: Muse category -> role_category hint, and the categories we ask for at all. Verified live
#: 2026-08-01 with the listing count each carried; names must match exactly ("IT" and
#: "Finance" return 0, "Computer and IT" returns 1 536).
#:
#: Absent on purpose: "Science and Engineering" (7 148) is overwhelmingly mechanical,
#: chemical and civil work that would classify as `uncategorised` and pad every shortlist,
#: and "Customer Service" (1 302) models no category here. `None` means fetch it but let the
#: title speak for itself — "Computer and IT" and "Project Management" are too mixed to hint.
CATEGORIES: dict[str, Optional[str]] = {
    "Software Engineering": "software_engineering",   # 100 185
    "Data and Analytics": "data_analysis",            #  17 822
    "Business Operations": "other_tech_function",     #  17 625
    "Project Management": None,                       #   9 012
    "Sales": "other_tech_function",                   #   8 924
    "Design and UX": "design",                        #   2 218
    "Product Management": "product",                  #   2 163
    "Computer and IT": None,                          #   1 536
    "Media, PR, and Communications": "other_tech_function",  # 723
}

#: Locations to sweep in addition to the unfiltered category pages. "Flexible / Remote" is
#: the Muse's own remote bucket; the European cities are here because the category pages skew
#: so far US that EU postings would otherwise never surface within `PAGES_PER_QUERY`.
LOCATIONS = [
    "Flexible / Remote",
    "Berlin, Germany", "Munich, Germany", "Amsterdam, Netherlands",
    "Paris, France", "Dublin, Ireland", "Madrid, Spain", "Barcelona, Spain",
    "Warsaw, Poland", "Prague, Czech Republic", "Lisbon, Portugal",
    "Stockholm, Sweden", "Copenhagen, Denmark", "Vienna, Austria",
    "Milan, Italy", "Brussels, Belgium", "Bucharest, Romania", "Budapest, Hungary",
]

PAGES_PER_QUERY = 3      # 20 results per page
RESULTS_PER_PAGE = 20

#: See the module docstring — this is the whole reason the adapter is safe to run.
MAX_AGE_DAYS = 45

#: The Muse's remote bucket, matched against the joined location string.
_REMOTE_LOCATION = "flexible / remote"

#: US state and territory postal codes. The Muse writes American locations as "Austin, TX"
#: and names the country nowhere, so `geo.resolve_location` — which knows EU-27 cities and
#: country names — reads them as no country at all. Measured over a full fetch on 2026-08-01:
#: 345 of 475 postings resolved to nothing, nearly all of them American.
#:
#: That is not a harmless unknown here. The location gate *keeps* unknown-country postings
#: and hands them to the AI matcher, so an on-site job in Boise would enter the shortlist of
#: every EU subscriber and spend one of their ~120 slots to be rejected. A trailing state code
#: is unambiguous proof of the country, which makes this the one place it can be read.
#:
#: Deliberately here and not in `service/geo.py`: that module is the definition of which
#: places a subscriber may *choose*, mirrored in `web/lib/geo.ts` and drift-tested. This is
#: parsing one source's address format, the same job Ashby's `_COUNTRY` map does.
_US_STATES = frozenset("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ
NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP
""".split())


class TheMuseSource(BaseSource):
    """The Muse public jobs API — category and location sweeps, freshness-filtered."""

    def __init__(self, categories: Optional[dict[str, Optional[str]]] = None,
                 locations: Optional[list[str]] = None,
                 pages: int = PAGES_PER_QUERY,
                 max_age_days: int = MAX_AGE_DAYS):
        self._categories = CATEGORIES if categories is None else categories
        self._locations = LOCATIONS if locations is None else locations
        self._pages = pages
        self._max_age_days = max_age_days

    @property
    def source_name(self) -> str:
        return "themuse"

    # ------------------------------------------------------------------ fetch --
    def fetch(self) -> list[dict]:
        seen: dict[int, dict] = {}
        stale = 0

        queries: list[dict] = [{"category": c} for c in self._categories]
        queries += [{"location": loc} for loc in self._locations]

        for query in queries:
            for page in range(self._pages):
                for item in self._page({**query, "page": page}):
                    jid = item.get("id")
                    if jid is None or jid in seen:
                        continue
                    if not self._is_fresh(item):
                        stale += 1
                        continue
                    seen[jid] = item

        logger.info("The Muse: fetched %d fresh postings (%d dropped as older than %d days)",
                    len(seen), stale, self._max_age_days)
        return list(seen.values())

    def _page(self, params: dict) -> list[dict]:
        try:
            resp = requests.get(API, params={"descending": "false", **params},
                                headers=HEADERS, timeout=25)
            if resp.status_code != 200:
                logger.warning("The Muse %s: HTTP %d", params, resp.status_code)
                return []
            return resp.json().get("results", []) or []
        except (requests.RequestException, ValueError) as exc:
            logger.warning("The Muse %s failed: %s", params, exc)
            return []

    def _is_fresh(self, item: dict) -> bool:
        published = self._parse_date(item.get("publication_date"))
        if published is None:
            # Unlike the ATS sources, an unknown date here is a *reason to drop*: the corpus
            # is known to contain multi-year-old rows and the whole point of this filter is
            # that nothing downstream can tell one from a new listing.
            return False
        return (date.today() - published).days <= self._max_age_days

    # -------------------------------------------------------------- normalize --
    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = ((item.get("refs") or {}).get("landing_page") or "").strip()
            if not url:
                continue

            location = self._location(item)
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("name"),
                    company=(item.get("company") or {}).get("name"),
                    url=url,
                    description=item.get("contents"),
                    location=location,
                    # The Muse states a country in prose for the rest of the world
                    # ("Berlin, Germany"), which `geo.resolve_location` reads on its own, and
                    # states none at all for the US ("Austin, TX"), which it cannot. So the
                    # only country resolved here is the one the resolver has no way to reach.
                    country_code=self._us_state_country(location),
                    remote_signal=self._is_remote(location),
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item.get("publication_date")),
                    source_category=self._hint(item),
                )
            )
        logger.info("The Muse: normalised %d postings", len(postings))
        return postings

    @staticmethod
    def _location(item: dict) -> Optional[str]:
        names = [str(loc.get("name")).strip()
                 for loc in (item.get("locations") or []) if loc.get("name")]
        return "; ".join(names) or None

    @staticmethod
    def _us_state_country(location: Optional[str]) -> Optional[str]:
        """``"US"`` when every stated place is a US one, else ``None``.

        Unanimity is the point. A posting listed as "Austin, TX; London" is not a US job in
        the sense the location gate means — an explicit ``country_code`` *wins* over the
        location text in `resolve_location`, so claiming US here would erase the London half
        and hide the role from the subscriber it fits. When the places disagree, saying
        nothing is right: the gate keeps unknowns and the matcher reads the full string.
        """
        if not location:
            return None
        saw_state = False
        for place in location.split(";"):
            tail = place.strip().rsplit(",", 1)[-1].strip().upper()
            if tail in _US_STATES:
                saw_state = True
            elif place.strip().lower() != _REMOTE_LOCATION:
                return None  # a non-US place, or one we cannot read — do not claim a country
        return "US" if saw_state else None

    @staticmethod
    def _is_remote(location: Optional[str]) -> bool:
        """Only the Muse's own remote bucket counts, and only when it stands alone.

        A listing tagged "Austin, TX; Boise, ID; Flexible / Remote" is an office job with a
        remote option for some hires, not a role someone in Prague can take. `remote_signal`
        exempts a posting from the location gate outright, so reading that as remote is the
        2026-07-28 failure again with a different board's flag in place of jobs.cz's.
        """
        if not location:
            return False
        parts = [p.strip().lower() for p in location.split(";") if p.strip()]
        return parts == [_REMOTE_LOCATION]

    def _hint(self, item: dict) -> Optional[str]:
        for cat in (item.get("categories") or []):
            hint = self._categories.get(str(cat.get("name") or "").strip())
            if hint:
                return hint
        return None

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
