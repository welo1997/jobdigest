"""NAV (Norway) — the national employment service's public job feed.

**Not `nva.py`.** That is NVA, *Latvia*'s register, one transposition away. Two national
employment services whose acronyms differ by a swapped letter both live in this directory;
check which one you are editing before changing anything that mentions a country.

`arbeidsplassen.nav.no` is Norway's statutory vacancy register, and the third national
register this project carries after `mpsv` (CZ) and `platsbanken` (SE). Permission is a
**positive grant** rather than a disclaimer, which makes it the strongest of the three:
`/vilkar-api` says *"Alle kan bruke tenesta. Tenesta er kostnadsfri"* and gives consumers
*"rett til å **republisere og vise** mottekne jobbannonsar på sine tenester"* — the act this
product performs, named. The token comes from `GET /api/publicToken` with no account, no form
and no email, so the skip rule does not apply. **Do not mail NAV for the stable token**: that
is correspondence, and the public one works.

**Use the feed and nothing else.** The site also publishes a sitemap that enumerates the exact
active set, and its ad pages are server-rendered — but only ~71% of sitemap uuids resolve on
the feed API, and every 404 checked carries a `finn.no` attribution. NAV may *display* those
ads and may not redistribute them, so **the feed is the permission boundary, not a transport**,
and scraping the gap would read as a coverage win while republishing what is not ours. FINN.no
independently refuses in its own robots.txt. `arbeidsplassen.nav.no/stillinger/api/search` is
the site's internal Elasticsearch and sits outside the document that grants us anything.

**This is the first source in the repo that needs persisted state, and the reason is
`deactivate_stale`.** The feed is an append-only *change log*: an ad appears when it is created
or modified, so one published a month ago and never edited appears in no recent window.
`store.deactivate_stale(days=7)` deactivates anything not re-seen in seven days, and there is
no per-source exemption. Without a mirror, Norwegian inventory would decay to nothing within a
week of each ad's last edit — silently, with no error and no failed timer.

So the adapter keeps a local mirror (`uuid -> ad`) in a Docker volume, walks the feed forward
from a persisted cursor, applies INACTIVE and `expires`, and **emits the whole active set every
run**. From the pipeline's point of view it is then an ordinary adapter that re-sees everything,
which is exactly what every other part of the system already assumes.

Three obligations from `/vilkar-api` that change behaviour rather than shape:

  - an entry arriving `status: "INACTIVE"` must deactivate the ad, and `expires` must be
    enforced (*"skal straks fjernast frå resultatlista"*);
  - the apply link must deep-link the ad — but **`posting_id` hashes the immutable `uuid`**,
    never a third-party URL, so a link repair can never churn the corpus;
  - personal data is heavy and is handled as for `mpsv`: **95.5% of ads carry a structured
    contact** (measured over 2 000 live ads), so `contactList` is never read at all, and free
    text is scrubbed on top of that.
"""

from __future__ import annotations

import email.utils
import html as _html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests

from ingestion import politeness
from ingestion.base import BaseSource, JobPosting, make_posting_id

logger = logging.getLogger(__name__)

BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = f"{BASE}/api/publicToken"
FEED_URL = f"{BASE}/api/v1/feed"
ENTRY_URL = f"{BASE}/api/v1/feedentry/{{uuid}}"

#: Where the mirror lives. A Docker-managed named volume, so it survives `compose up -d`
#: recreating the container — which a bind mount under the shipped tree would not, because
#: `deploy.sh` replaces that tree wholesale via `git archive | tar -x`.
STATE_DIR = Path(os.environ.get("NAV_STATE_DIR", "/state"))

#: STYRK-08 major groups kept **whatever their code says**: 1 managers, 2 professionals,
#: 3 technicians. White-collar work whose *titles* the taxonomy reads directly, so an unmapped
#: code here (216 architects, 212 statisticians) is not a reason to refuse the ad.
#:
#: It used to be the whole scope rule, and that was the wrong shape — see `_in_scope`. What
#: survives of the original dilution guard is its reasoning, not its mechanism: under
#: `SHORTLIST_FLOOR` the recall predicate is dropped entirely, so **unrankable** inventory
#: reaches subscribers who never asked for it. Rankable inventory does not have that problem,
#: because `role_category` is what the predicate filters on.
STYRK_MAJOR_KEEP = frozenset("123")

#: STYRK-08 prefix -> `role_category`, longest prefix first.
#:
#: **Written here and deliberately NOT imported from `scripts/categorization_score.ISCO_MAP`.**
#: That table is the answer *key*; this one is an *input* to the classifier, and a key that
#: cannot contradict the thing it grades is decorative. `mpsv.ISCO_CATEGORIES` is kept separate
#: from the same scorer map for exactly this reason.
#:
#: STYRK-08 is Norway's ISCO-08 and the four-digit codes agree, but the schemes do diverge
#: nationally: STYRK minor group 222 has four unit groups where ISCO-08 has two, and there is
#: no ISCO-08 2223 at all — it is *Sykepleiere*, the commonest professional code in the corpus.
#: Keying `22` at two digits is what makes those national subdivisions land correctly.
#:
#: **Majors 4-9 were added 2026-08-26 and they are what makes `_in_scope` possible**, on the
#: same rule `platsbanken.SSYK_FIELD_CATEGORIES` follows: a group that means exactly one thing
#: gets an entry, a group with no counterpart in `CATEGORIES` is left out and its ads are
#: refused. Measured over the 5 445-ad refreshed answer key (b0+b1, 2026-08-22): they admit
#: **2 361 ads, 43.4% of the register**, of which **1 004 (43%) are titles the patterns cannot
#: read at all** — "Vi søker ringevikarer", "Er du min nye BPA-assistent?". Those are not
#: vocabulary misses. They are ads whose title never names an occupation, and the publisher's
#: own code is the only thing that can answer for them.
#:
#: `5322` is the entry worth arguing about, and it is a **deliberate disagreement with the
#: answer key**, which rolls the whole of `532` up to healthcare. 5322 is home-based personal
#: care, which in Norway is overwhelmingly BPA (*brukerstyrt personlig assistanse*) — the
#: disability-support scheme, 229 ads in the key, 203 of them unreadable by title. The
#: taxonomy already settled this shape for Sweden: LSS `personlig assistent` is `social_care`,
#: not healthcare (see the note in `taxonomy.PATTERNS`). `5321` (helsefagarbeider) stays
#: healthcare and `5329` with it, so the split is at the level STYRK itself distinguishes.
#: **The code route also sidesteps the false friend outright**: NO `personlig assistent` can be
#: an office PA and the title cannot tell you which, but 5322 can.
#:
#: The disagreements this creates with the title classifier are all near-neighbour boundary
#: calls on the same key — 712 says construction where the title says skilled_trades (18 ads),
#: 723 the reverse (11), 531 education against social_care (8) — and `classify` gives the title
#: precedence, which is the existing policy and stays.
STYRK_CATEGORIES: dict[str, str] = {
    # 1 managers
    "121": "operations", "122": "sales", "132": "manufacturing_production",
    "133": "devops_platform", "134": "healthcare", "141": "hospitality", "142": "sales",
    # 2 professionals
    "21": "engineering",                          # science & engineering (251/252 override)
    "22": "healthcare", "23": "education",
    "241": "finance_accounting", "242": "operations", "243": "marketing",
    "251": "software_engineering", "252": "devops_platform",
    "261": "legal", "263": "social_care",
    # 3 associate professionals
    "31": "engineering", "32": "healthcare",
    "331": "finance_accounting", "332": "sales", "333": "operations",
    "334": "other_tech_function", "3412": "social_care",
    "351": "customer_support", "352": "devops_platform",
    # 4 clerical support. 41 (general and keyboard clerks) is deliberately absent: "Kontor-
    # medarbeider produksjon og logistikk" is the residual bucket by any reading, and no chip
    # selects it. 4224 (hotel receptionists) likewise — 27 ads, and `hospitality` would claim
    # a front-desk job the taxonomy files as customer-facing admin.
    "4222": "customer_support", "4225": "customer_support", "4226": "hospitality",
    "431": "finance_accounting",
    "4321": "logistics_transport", "4322": "manufacturing_production",
    "4323": "logistics_transport",
    # 5 service and sales. 514 (hairdressers, beauticians), 5142 (spa), 5153 (building
    # caretakers), 541 (protective services) and 516 have no counterpart and stay refused.
    "512": "hospitality", "513": "hospitality",
    "52": "sales",
    "531": "education",                            # childcare workers, barnehageassistent
    "5321": "healthcare", "5322": "social_care", "5329": "healthcare",
    # 7 craft and trades. 731 (handicraft) and 754 (other craft) are absent.
    "71": "construction",
    "721": "manufacturing_production", "722": "manufacturing_production",
    "723": "skilled_trades",                       # machinery mechanics and repairers
    "732": "manufacturing_production",
    "741": "skilled_trades", "742": "skilled_trades",
    "751": "manufacturing_production", "752": "manufacturing_production",
    "753": "manufacturing_production",
    # 8 operators and assemblers. 835 (ships' deck crews) is absent — 6 ads, no category.
    "81": "manufacturing_production", "82": "manufacturing_production",
    "831": "logistics_transport", "832": "logistics_transport", "833": "logistics_transport",
    "834": "construction",                         # earthmoving and crane operators
    "8344": "logistics_transport",                 # lifting-truck (forklift) work is warehouse
    # 9 elementary. 91 (cleaners, 100 ads — the largest single refusal), 92, 95 and 96 have no
    # counterpart in CATEGORIES and mapping them to the nearest thing is what the answer key's
    # own first rule forbids.
    "931": "construction", "932": "manufacturing_production", "933": "logistics_transport",
    "94": "hospitality",
}

#: How far back a cold start reaches. No backfill is needed or wanted: every ad carries
#: `expires`, and the mirror fills in over a few runs as ads are created or edited. Walking
#: from the feed's 2023-06-14 head would be hundreds of thousands of entries for inventory
#: that has long since expired.
COLD_START_DAYS = 30
MAX_PAGES = 60
#: Detail calls per run. Bounds the cold start (~20 min at `politeness`'s 1 s/host) so this
#: adapter cannot silently eat the export window on its first day. Steady state is far below
#: it: 2 673 distinct active ads came from 9 749 change-log entries over six days, so most
#: entries are re-edits of ads already mirrored and need no call at all.
MAX_DETAILS = 1200

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
#: Norwegian numbers as employers actually write them. **Matched by digit count rather than by
#: fixed groups**, which is the `platsbanken` lesson: `+47 22 33 44 55`, `22334455`,
#: `934 56 789` and `22-33-44-55` all differ, and a fixed-group pattern that covers one misses
#: the others. Over-matching is the safe direction — a false positive only redacts, and the
#: nine-digit organisation number this also catches is not something we need in free text.
_PHONE = re.compile(r"(?:\+47[\s-]?)?(?:\d[\s-]?){7}\d\b")
_BLOCK = re.compile(r"</(?:p|div|li|h[1-6]|section|tr)>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def _text(raw: Optional[str]) -> Optional[str]:
    """HTML ad body -> readable plain text.

    **Every NAV description is HTML** (2 000 of 2 000 measured), unlike `mpsv` and
    `platsbanken` which both return plain text. Tags are stripped rather than stored because
    the description feeds `search_tsv`, and markup in a tsvector is thousands of junk lexemes
    per ad. Block-level closers become newlines first, so paragraph structure survives — these
    are long ads and the breaks are what make them readable.

    **Strip tags BEFORE unescaping, never after, and the order is load-bearing.** Norwegian
    employers write quotation marks as ASCII angle brackets — `&lt;løsninger&gt;`,
    `&lt;&lt;spille ball&gt;&gt;` — which appear escaped in the source. Unescaping first turns
    those into `<løsninger>`, which the tag pattern then eats, **silently deleting the quoted
    word from the ad**:

        strip -> unescape  (shipped)  "Vi selger <løsninger> som virker."
        unescape -> strip  (wrong)    "Vi selger  som virker."

    Three ads in 2 000 carry that construction. Nothing about the wrong order raises; it just
    removes words, which is the failure shape this repo keeps meeting.
    """
    if not raw:
        return None
    text = _BLOCK.sub("\n", raw)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    text = _WS.sub(" ", text)                      # horizontal runs only; newlines are content
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _scrub(text: Optional[str]) -> Optional[str]:
    """Remove direct contact details from free text.

    The structured `contactList` is simply never read — that is the primary defence. This
    covers the other half, where an employer has typed a recruiter's mobile into the ad body.
    Replaced with a marker rather than deleted so the surrounding sentence still reads as
    Norwegian, exactly as `mpsv` does it.
    """
    if not text:
        return None
    text = _EMAIL.sub("[kontaktinfo fjernet]", text)
    text = _PHONE.sub("[kontaktinfo fjernet]", text)
    return text.strip() or None


def _styrk_codes(content: dict) -> list[str]:
    return [c.get("code") for c in (content.get("categoryList") or [])
            if c.get("categoryType") == "STYRK08" and c.get("code")]


def _styrk_category(codes: list[str]) -> Optional[str]:
    """Longest prefix wins; `None` rather than a guess.

    Ads carrying several codes that disagree get **no hint at all**. 13.6% of ads carry more
    than one STYRK08 code and every one is scored 1.0, so there is no best one to take, and
    `taxonomy.classify` treats a hint as a fallback for titles its patterns cannot read — a
    wrong hint there is not a near miss, it is a category no query can correct.
    """
    answers = set()
    for code in codes:
        for n in (4, 3, 2):
            if code[:n] in STYRK_CATEGORIES:
                answers.add(STYRK_CATEGORIES[code[:n]])
                break
    return answers.pop() if len(answers) == 1 else None


def _in_scope(codes: list[str], keep: frozenset[str]) -> bool:
    """Is this ad rankable — i.e. can anything downstream file it under a category?

    **This replaced a major-group filter on 2026-08-26, and the shape of the rule is the
    point.** `frozenset("123")` refused *half the register* (2 729 of 5 445 ads on the
    refreshed answer key) to bound one real harm: under `SHORTLIST_FLOOR` the recall predicate
    is dropped entirely, so inventory nobody can filter on reaches subscribers who never asked
    for it. But "unrankable" and "major 4-9" are not the same set, and the sibling register
    already said so: `platsbanken` ingests healthcare, transport, pedagogy, hospitality,
    manufacturing, construction and social work — majors 4-9 work, all of it — gated on
    `test_a_field_is_never_ingested_without_a_way_to_rank_it`. Sweden contributes 47 083 active
    postings against Norway's 5 577, and this filter is most of that gap.

    So the test is rankability, in three parts:

      - **no code at all** → keep. The title is all there is and it may well read; refusing
        would discard ads for having less metadata, which is the wrong direction.
      - **majors 1-3** → keep, mapped or not. White-collar titles the patterns read directly.
      - **anything else** → keep only when the codes name a category unanimously. That is the
        publisher's own answer, and it is a *stronger* signal than a title, not a weaker one:
        43% of the ads this admits carry titles no pattern can read.

    The refusals that remain are work with no counterpart in `taxonomy.CATEGORIES` — cleaners,
    hairdressers, security guards, spa therapists, sailors, general office clerks: 368 ads,
    6.8% of the register. Mapping them to the nearest category is exactly what the answer
    key's first rule forbids, and it is why this returns False rather than guessing.

    **Widening this costs no requests.** The scope test runs *after* `fetch` has already paid
    for the detail call (that is what lets it re-check an ad recoded since we last looked), so
    the old rule was discarding responses it had already fetched.
    """
    if not codes:
        return True
    if codes[0][:1] in keep:
        return True
    return _styrk_category(codes) is not None


def _expired(expires: Optional[str], today: date) -> bool:
    if not expires:
        return False                               # unknown expiry is not an expired ad
    try:
        return datetime.fromisoformat(expires.replace("Z", "+00:00")).date() < today
    except (TypeError, ValueError):
        return False


def _posted(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _location(content: dict) -> tuple[Optional[str], Optional[str]]:
    """(location text, country name) from the ad's own `workLocations`.

    **The country is resolved from the posting, never assumed.** NAV is Norway's register and
    a `country_code="NO"` constant is the obvious shortcut — and it is wrong: of 2 000 live ads
    measured, 6 are in Spain, Greece, the USA and Denmark. That is the Arbeitnow mistake, where
    a source-level constant overrode the posting's own text and hid UK roles from UK
    subscribers. The country NAME is returned rather than a code so `service.geo` resolves it
    (it already knows *norge*), which keeps one definition of geography.
    """
    places = content.get("workLocations") or []
    parts: list[str] = []
    country = None
    for place in places:
        if not isinstance(place, dict):
            continue
        country = country or (place.get("country") or None)
        for field in ("city", "municipal", "county"):
            value = (place.get(field) or "").strip()
            if value and value.title() not in parts:
                parts.append(value.title())
                break
    return (", ".join(parts[:3]) or None), country


class NavSource(BaseSource):
    """Norway's public job feed, mirrored locally so the whole active set is emitted daily."""

    def __init__(self, state_dir: Optional[Path] = None,
                 styrk_major: Optional[frozenset[str]] = None,
                 max_details: Optional[int] = None) -> None:
        self._state_dir = Path(state_dir) if state_dir else STATE_DIR
        self._keep = styrk_major if styrk_major is not None else STYRK_MAJOR_KEEP
        self._max_details = MAX_DETAILS if max_details is None else max_details

    @property
    def source_name(self) -> str:
        return "nav"

    # --- the mirror ------------------------------------------------------------------------

    @property
    def _state_path(self) -> Path:
        return self._state_dir / "nav_mirror.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"cursor": None, "ads": {}, "pending": []}
        if not isinstance(data, dict) or not isinstance(data.get("ads"), dict):
            logger.warning("nav: mirror unreadable, starting cold")
            return {"cursor": None, "ads": {}, "pending": []}
        return {"cursor": data.get("cursor"), "ads": data["ads"],
                "pending": list(data.get("pending") or [])}

    def _save(self, state: dict) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._state_path)          # atomic: a half-written mirror is a cold start
        except OSError as exc:
            logger.warning("nav: could not persist the mirror (%s) — next run starts cold", exc)

    # --- transport -------------------------------------------------------------------------

    def _token(self) -> Optional[str]:
        politeness.throttle(TOKEN_URL)
        try:
            resp = requests.get(TOKEN_URL, headers=politeness.HEADERS, timeout=30)
        except requests.RequestException as exc:
            logger.warning("nav: token request failed (%s)", exc)
            return None
        if resp.status_code != 200:
            logger.warning("nav: token endpoint returned %s", resp.status_code)
            return None
        # The body is a human-readable blurb with the JWT inside it, NOT a bare token —
        # `resp.text.strip()` would send a whole sentence as a bearer credential, and every
        # later call would 401, which downstream is indistinguishable from a quiet day.
        found = re.search(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", resp.text)
        if not found:
            logger.warning("nav: no JWT in the token response")
            return None
        return found.group(0)

    def _get(self, url: str, headers: dict) -> Optional[dict]:
        politeness.throttle(url)
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            logger.warning("nav: %s failed (%s)", url, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def _walk(self, headers: dict, cursor: Optional[str]) -> Iterator[tuple[dict, Optional[str]]]:
        """Yield (page, next_cursor) forward from `cursor`, or from a cold-start window."""
        if cursor:
            url, params_headers = (BASE + cursor if cursor.startswith("/") else cursor), headers
        else:
            since = datetime.now(timezone.utc) - timedelta(days=COLD_START_DAYS)
            # MUST be format_datetime. A weekday that does not match its date is SILENTLY
            # ignored and the feed serves its 2023-06-14 head instead — a 200 with a full page
            # of real, three-year-old ads. Verified live 2026-08-11.
            url = FEED_URL
            params_headers = {**headers,
                              "If-Modified-Since": email.utils.format_datetime(since)}

        pages = 0
        first = True
        while url and pages < MAX_PAGES:
            page = self._get(url, params_headers)
            if not page:
                return
            items = page.get("items") or []
            if first and items and not cursor:
                stamp = str(items[0].get("date_modified") or "")
                if stamp[:4] == "2023":
                    logger.error("nav: feed fell back to its 2023 head — the If-Modified-Since "
                                 "weekday trap. Header must come from format_datetime.")
                    return
            first = False
            nxt = page.get("next_url")
            yield page, nxt
            if not items:
                return                             # caught up: keep this cursor for next run
            url = (BASE + nxt if nxt and nxt.startswith("/") else nxt) if nxt else None
            params_headers = headers               # the window header applies to page 1 only
            pages += 1

    # --- BaseSource ------------------------------------------------------------------------

    def fetch(self) -> list[dict]:
        if not politeness.robots_allows(FEED_URL):
            logger.warning("nav: robots.txt disallows %s — skipping", FEED_URL)
            return []
        token = self._token()
        if not token:
            return []
        headers = {**politeness.HEADERS, "Authorization": f"Bearer {token}"}

        state = self._load()
        ads: dict[str, dict] = state["ads"]
        cold = not ads
        cursor = state["cursor"]

        # **The backlog is persisted, and that is what makes a bounded cold start correct.**
        # The walk advances the cursor past every entry it reads, but only `max_details` of
        # them can be fetched in one run. Without carrying the remainder forward the cursor
        # would move past ~7 400 ads that were never mirrored, and they would be invisible
        # until an employer happened to edit one. Measured on the box: a cold start reports
        # 7 471 changed and fetches 1 200.
        changed: list[str] = list(state["pending"])
        stamps: dict[str, str] = {}
        dropped = unchanged = 0
        for page, nxt in self._walk(headers, cursor):
            for item in page.get("items", []):
                entry = item.get("_feed_entry") or {}
                uuid = entry.get("uuid")
                if not uuid:
                    continue
                if entry.get("status") == "ACTIVE":
                    # **Only fetch the detail if the ad actually changed.** The feed carries
                    # `sistEndret` on the entry itself, and an ad is re-announced whenever
                    # anything about it moves — so without this check the daily cost is one
                    # request per feed ENTRY (~1 600/day measured) rather than per genuinely
                    # changed ad. The employer may have edited the title, the location or the
                    # closing date, so a changed stamp does mean re-read.
                    stamp = entry.get("sistEndret")
                    known = ads.get(uuid)
                    if known and stamp and known.get("sistEndret") == stamp:
                        unchanged += 1
                        continue
                    if uuid not in changed:
                        changed.append(uuid)
                        stamps[uuid] = stamp
                else:
                    # The last entry for a uuid wins — this is a change log, so an ad that is
                    # created and withdrawn inside one walk must end up withdrawn.
                    if ads.pop(uuid, None) is not None:
                        dropped += 1
                    if uuid in changed:
                        changed.remove(uuid)
            cursor = nxt or cursor

        fetched = 0
        todo, backlog = changed[:self._max_details], changed[self._max_details:]
        for uuid in todo:
            detail = self._get(ENTRY_URL.format(uuid=uuid), headers)
            if not detail:
                continue
            content = detail.get("ad_content") or {}
            if detail.get("status") and detail["status"] != "ACTIVE":
                ads.pop(uuid, None)
                continue
            codes = _styrk_codes(content)
            if not _in_scope(codes, self._keep):
                ads.pop(uuid, None)                # re-coded out of scope since we last looked
                continue
            location, country = _location(content)
            # Only what a posting needs is mirrored. `contactList` is never read, so it can
            # never be persisted, which is what keeps the mirror out of the privacy policy.
            ads[uuid] = {
                "uuid": uuid,
                "title": content.get("title"),
                "employer": (content.get("employer") or {}).get("name"),
                "description": _scrub(_text(content.get("description"))),
                "location": location,
                "country": country,
                "expires": content.get("expires"),
                "published": content.get("published"),
                "link": content.get("link"),
                "styrk": codes,
                # what the feed said when we last read it, so an unchanged
                # ad costs no request next run
                "sistEndret": stamps.get(uuid),
            }
            fetched += 1

        today = datetime.now(timezone.utc).date()
        expired = [u for u, ad in ads.items() if _expired(ad.get("expires"), today)]
        for uuid in expired:
            del ads[uuid]

        state = {"cursor": cursor, "ads": ads, "pending": backlog}
        self._save(state)
        logger.info("nav: %s%d changed, %d unchanged (no call), %d detail calls, %d withdrawn, "
                    "%d expired -> %d mirrored",
                    "COLD START, " if cold else "", len(changed), unchanged, fetched,
                    dropped, len(expired), len(ads))
        if backlog:
            logger.info("nav: %d ads still queued for a detail call; they are carried to the "
                        "next run (~%d more runs at this budget)", len(backlog),
                        -(-len(backlog) // max(self._max_details, 1)))
        return list(ads.values())

    def normalize(self, raw_items: list[dict]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for ad in raw_items:
            uuid, title = ad.get("uuid"), (ad.get("title") or "").strip()
            if not uuid or not title:
                continue
            codes = ad.get("styrk") or []
            if not _in_scope(codes, self._keep):
                continue
            link = ad.get("link") or ""
            # The apply link must deep-link the ad, but `posting_id` hashes the immutable
            # `uuid`. A source that changes its URL scheme re-creates its whole inventory when
            # the id is a hash of the link; NAV has already moved host once.
            if not link.startswith(("http://", "https://")):
                link = f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
            location = ad.get("location")
            country = ad.get("country")
            # The country NAME goes into the location text so `service.geo` resolves it once,
            # rather than this adapter keeping a second, private country table.
            if country and location and country.lower() not in location.lower():
                location = f"{location}, {country.title()}"
            elif country and not location:
                location = country.title()
            postings.append(JobPosting(
                posting_id=make_posting_id(f"nav:{uuid}"),
                source=self.source_name,
                title=title,
                company=ad.get("employer") or None,
                url=link,
                description=ad.get("description"),
                location=location,
                # Resolved downstream from `location` by `service.geo`, which already knows
                # *norge* — never a source constant, because 6 of 2 000 ads are not Norwegian.
                country_code=None,
                # No structured remote field exists in this payload. Never claim one:
                # `geo.work_mode` reads the posting's own words, and inventing a signal here
                # is the 2026-07-28 false-remote bug.
                remote_signal=None,
                posted_at=_posted(ad.get("published")),
                source_category=_styrk_category(codes),
            ))
        return postings
