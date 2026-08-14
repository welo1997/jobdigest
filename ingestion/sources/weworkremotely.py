"""WeWorkRemotely — fetch all remote jobs via RSS feed.

**The feed carries the geographic scope and this adapter used to throw it away.** Every entry
has `<region>` — WWR's own vocabulary, "Anywhere in the World" / "Europe Only" / "USA Only" —
plus `<country>` (an explicit allowlist when the role is country-bound) and `<state>`. All
three were present on 100 of 100 entries when measured on 2026-08-14, and `normalize` passed
`location=None`, which is why every WeWorkRemotely row in production has an empty location and
resolves to no country at all: 170 postings, the only source in the corpus that was 100%
location-empty, and the emptiness looked like the board's fault rather than ours.

That is the exact shape of the failure this repo keeps re-learning — a plausible number (170
remote jobs, ids fresh, links good) hiding a field never read. Nothing downstream could tell,
because "unknown location" is *kept* by the location gate by design.

`region` is also the single best scope signal in the whole corpus: it is a closed vocabulary
filled in by the publisher, so `geo.remote_reach` reads an answer here rather than guessing at
prose. `country` is folded in behind it because a country-bound role names its countries there
and nowhere else. The flag emoji WWR prefixes each country with are dropped — `geo.normalise`
would strip them anyway, but `location` is shown to subscribers.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import feedparser

from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

WWR_FEED_URL = "https://weworkremotely.com/remote-jobs.rss"

#: Regional-indicator pairs (flag emoji) and the variation selectors around them. WWR writes
#: `country` as "🇺🇸 United States of America, 🇨🇦 Canada".
_FLAGS_RE = re.compile("[\U0001F1E6-\U0001F1FF️‍]+")
#: What is left over once the flags are gone: spaces, the list comma, and the zero-width space
#: WWR separates them with. Escaped rather than written literally — an invisible character in
#: source is a character no reviewer can see.
_TRIM = " ," + chr(0x200B)


class WeWorkRemotelySource(BaseSource):
    """WeWorkRemotely RSS — all categories."""

    @property
    def source_name(self) -> str:
        return "weworkremotely"

    def fetch(self) -> list[dict]:
        feed = feedparser.parse(WWR_FEED_URL)
        logger.info("WeWorkRemotely: fetched %d entries", len(feed.entries))
        return [dict(e) for e in feed.entries]

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for item in raw_items:
            url = item.get("link", "")
            if not url:
                continue

            scope = self._scope(item)
            postings.append(
                JobPosting(
                    posting_id=make_posting_id(url),
                    source=self.source_name,
                    title=item.get("title"),
                    company=self._extract_company(item.get("title", "")),
                    url=url,
                    description=item.get("summary") or item.get("description"),
                    # The scope doubles as the location, because for a remote-only board it is
                    # the only place text there is. `country_code` is still left to
                    # `geo.resolve_location`, which reads the country names out of it.
                    location=scope or None,
                    country_code=None,
                    remote_signal=True,
                    salary_raw=None,
                    currency=None,
                    posted_at=self._parse_date(item),
                    scope_raw=scope or None,
                )
            )
        logger.info("WeWorkRemotely: normalised %d postings (%d carrying a scope)",
                    len(postings), sum(1 for p in postings if p.scope_raw))
        return postings

    @staticmethod
    def _scope(item: dict) -> str:
        """`region` then `country`, deduped, stripped of flag emoji.

        **`state` is deliberately not read.** It is the *employer's* seat, not the job's: Proxify
        advertises "Anywhere in the World" with `state: Stockholm`, and a work-from-anywhere role
        carrying "Stockholm" is wrong twice over — `resolve_location` stamps it SE/stockholm, so
        it appears under the Sweden filter and vanishes from every other country's, and
        `geo.remote_reach` reads the implied country and downgrades a genuine `anywhere` to
        `country`. `region` and `country` are the two fields that describe the candidate.

        Region first: a human reading `location` should see the binding sentence before the
        twenty-eight countries "Europe Only" expands to. Duplicates are dropped — "Remote"
        appearing in two fields is not two facts.
        """
        parts: list[str] = []
        for field in ("region", "country"):
            value = _FLAGS_RE.sub("", str(item.get(field) or "")).strip(_TRIM)
            value = re.sub(r"\s{2,}", " ", value)
            if value and value.casefold() not in {p.casefold() for p in parts}:
                parts.append(value)
        return ", ".join(parts)

    @staticmethod
    def _extract_company(title: str) -> Optional[str]:
        """WWR titles are often 'Company: Job Title'."""
        if ":" in title:
            return title.split(":")[0].strip()
        return None

    @staticmethod
    def _parse_date(item: dict) -> Optional[date]:
        published = item.get("published_parsed")
        if published:
            try:
                return date(*published[:3])
            except (TypeError, ValueError):
                pass
        return None
