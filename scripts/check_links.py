"""Fetch a few of each source's own URLs and prove they land on the advertised job.

Why this exists
---------------
Nothing in this repo has ever fetched a URL it stored. `posting_id = md5(url)` and the link
goes straight into an email `href`, so a dead link is invisible to every check we run: the
adapter returns the right number of rows, `source_watchdog` sees fresh ids and no churn, the
country split is right, and the tests pass. It surfaces when a subscriber clicks.

That has now happened twice in three days:

* **startupjobs, 2026-08-06** — a site rewrite moved the canonical link and the adapter
  emitted `/job/{id}` without the slug. Clean 404 on every posting for a day; one was emailed.
* **mpsv, 2026-08-08** — `up.gov.cz` routes on the *fragment*, so `?id={portalId}` was never a
  route at all. Every Úřad práce link this product ever sent was dead, for five days, across
  ~7 300 postings. The adapter's own comment said the link was "unverified".

Both were caught by a human clicking. This script is the check that should have caught them.

What it actually proves, and what it cannot
-------------------------------------------
A 200 is not evidence: `up.gov.cz` returns an identical shell for a bogus id, and so do most
client-rendered boards. So each URL is fetched and the page is then asked whether *this job*
is on it — the stored title's words, or the employer's name, in the rendered text. Verdicts:

    OK          the page carries the posting's own title. The link works.
    WEAK        the page names the employer but not the job — which is what a board's index
                page looks like. Accepting this as a pass would miss the failure being
                hunted, so it is called out for a human instead.
    DEAD        no 200 (404, 410, connection refused, timeout).
    BLOCKED     403/429 from bot protection. Not the same as broken — Himalayas answers this
                to every HTTP client, honest agent and spoofed Chrome alike, and the page
                opens normally in a real browser.
    THROTTLED   429/503 that survived a backoff. Our own probing rate, not the link: widen
                the run far enough and a board will rate-limit you. Never a failure.
    CLOSED      200, but the page says the role is filled or expired.
    MISMATCH    200 with real server-rendered text that mentions neither the job nor the
                employer — a shell, a redirect to a board's front page, or the wrong posting.
    SHELL       200 but almost no text: a client-rendered app. **Not a pass.** This is
                exactly what MPSV looked like, and it cannot be settled without a browser.
    ROBOTS      the site's robots.txt disallows fetching the page. Not checked, not a failure.

`SHELL`, `WEAK` and `BLOCKED` are deliberately their own verdicts rather than being folded
into either side. Calling them green would reproduce the MPSV bug; calling them red would
fail the run for sources that are fine. They mean *a human with a browser has to look once*,
and `BROWSER_CONFIRMED` records who looked and when — so a run flags only the sources nobody
has ever checked, which is the one thing worth a person's attention.

Six sources are unprovable over HTTP by construction (Workday, Oracle CX, Platsbanken and
up.gov.cz render client-side; Himalayas sits behind Cloudflare; Working Nomads is a
redirector into the employer's own ATS). All six were opened by hand on 2026-08-08 and all
six were correct. That is the honest ceiling of an HTTP checker, stated rather than hidden.

Running it
----------
    python scripts/check_links.py                 # every source, 3 links each
    python scripts/check_links.py -s mpsv -s lever
    python scripts/check_links.py -n 5 --json out.json

Sampling is bounded per source (see `SAMPLING`) so a run is minutes, not the 40 minutes a
real export takes: one Workday site and one search term rather than 13 × 9, three Greenhouse
boards rather than 149. That bounding is *only* about cost — the URLs are built by the real
`normalize`, so what is probed is exactly what would have been stored.

**Not wired into CI**, and deliberately: it depends on two dozen third-party sites being up,
so it would fail for reasons that are not ours and get muted. Run it after touching an
adapter's URL construction, and periodically. `--self-check` verifies the sampling table
still matches the adapters, which is the one part that can rot silently.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

from ingestion import politeness  # noqa: E402
from search_jobs import source_classes  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("check_links")

try:                                    # Windows consoles still default to cp1250
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                  # pragma: no cover - non-reconfigurable stream
    pass

TIMEOUT = 25

#: A 200 that says the role is gone. Kept short and unambiguous on purpose: a false CLOSED
#: reads as a broken adapter and sends someone hunting a bug that is not there.
CLOSED_MARKERS = [
    "no longer accepting applications", "no longer available", "no longer active",
    "position has been filled", "job has been filled", "this job has expired",
    "job is no longer", "position is closed", "vacancy has expired",
    "nabídka již není aktuální", "nabídka byla obsazena", "již byla obsazena",
    "tato pozice již není", "stránka nebyla nalezena", "page not found",
    "tjänsten är tillsatt", "annonsen är borttagen",
]

#: Below this many characters of visible text, the page did not render server-side and no
#: HTTP client can tell a real posting from a 404. See SHELL in the module docstring.
SHELL_TEXT_CHARS = 500

#: …and neither can it when the page is *mostly script*. Platsbanken answers 124 KB of HTML
#: holding 1 619 characters of navigation chrome (1.3%) and no ad text at all — well over the
#: character floor above, so a length test alone called it MISMATCH and would have had someone
#: hunting a bug in a working adapter. A server-rendered posting sits far above this; Workday
#: renders literally nothing.
SHELL_TEXT_RATIO = 0.03

#: Words that appear in every job title and prove nothing if matched.
_STOPWORDS = frozenset({
    "senior", "junior", "medior", "lead", "principal", "staff", "intern", "manager",
    "engineer", "developer", "specialist", "analyst", "consultant", "officer",
    "with", "and", "for", "the", "remote", "hybrid", "full", "time", "part",
    "praha", "prague", "brno", "stockholm", "london", "berlin",
})

_TAG = re.compile(r"(?is)<(script|style|noscript|template)\b.*?</\1>|<[^>]+>")
_WS = re.compile(r"\s+")


class Trim:
    """Marker: truncate the existing list (or dict) to `n` rather than replacing it.

    Keeps the sampling table honest — it never invents board tokens or search terms, it only
    takes fewer of the ones the adapter actually carries.
    """

    def __init__(self, n: int) -> None:
        self.n = n

    def of(self, current: Any, label: str) -> Any:
        if isinstance(current, dict):
            return dict(list(current.items())[:self.n])
        if isinstance(current, (list, tuple)):
            return current[:self.n]
        raise TypeError(f"{label} is {type(current).__name__}, not a list or dict")


class Spread(Trim):
    """Marker: take `n` evenly-spaced entries instead of the first `n`.

    For a **board list** the head is the worst possible sample. Greenhouse carries 149 board
    tokens and the first one is Stripe, which serves its postings from `stripe.com/careers`
    rather than `job-boards.greenhouse.io` — so probing the head exercised one URL shape and
    reported the whole source green. Even spacing walks the list as it was actually built:
    the ATS-native boards, the custom-domain ones, and whatever was appended in the last
    country sweep. Deterministic, so two runs probe the same boards and a regression is
    attributable.

    Query dimensions (search terms, tags, occupation fields) stay `Trim` — widening those
    multiplies requests without touching URL construction, which is what is being tested.
    """

    def of(self, current: Any, label: str) -> Any:
        if isinstance(current, dict):
            items = list(current.items())
            return dict(items[i] for i in _spread_indices(len(items), self.n))
        if isinstance(current, (list, tuple)):
            return [current[i] for i in _spread_indices(len(current), self.n)]
        raise TypeError(f"{label} is {type(current).__name__}, not a list or dict")


def _spread_indices(length: int, n: int) -> list[int]:
    if length <= n:
        return list(range(length))
    step = (length - 1) / (n - 1) if n > 1 else 0
    return sorted({round(i * step) for i in range(n)})


@dataclass
class Sample:
    """How to make one source produce a handful of postings cheaply."""

    module: dict[str, Any] = field(default_factory=dict)
    instance: dict[str, Any] = field(default_factory=dict)
    note: str = ""


#: Per-source bounding. Attribute names are checked by `--self-check`, so a rename fails
#: loudly instead of quietly reverting a source to a full production-sized run.
SAMPLING: dict[str, Sample] = {
    "remotive": Sample(),
    "weworkremotely": Sample(),
    "workingnomads": Sample(),
    "remoteok": Sample(module={"TAGS": Trim(1)}),
    "himalayas": Sample(module={"MAX_PAGES": 1}),
    "jobicy": Sample(module={"QUERIES": Trim(1)}),
    "arbeitnow": Sample(module={"MAX_PAGES": 1}),
    "greenhouse": Sample(instance={"_board_tokens": Spread(3)}),
    "ashby": Sample(module={"ORGS": Spread(3)}),
    "lever": Sample(module={"ORGS": Spread(3)}),
    # `_pages` is set from a default argument, which Python evaluates at def time — patching
    # the module constant would look like it worked and change nothing. Set the instance.
    "themuse": Sample(module={"LOCATIONS": Trim(1)},
                      instance={"_categories": Trim(1), "_pages": 1}),
    "oraclecloud": Sample(module={"SITES": Spread(1), "MAX_PAGES": 1}),
    "platsbanken": Sample(module={"OCCUPATION_FIELDS": Trim(1), "MAX_WINDOWS": 1,
                                  "MAX_PAGES_PER_WINDOW": 1}),
    "smartrecruiters": Sample(module={"TENANTS": Spread(1), "MAX_LIST_PAGES": 1,
                                      "MAX_DETAILS": 5}),
    "workday": Sample(module={"SITES": Spread(1), "SEARCH_TERMS": Trim(1),
                              "MAX_PAGES_PER_QUERY": 1, "MAX_DETAILS": 5}),
    "adzuna": Sample(module={"COUNTRY_CONFIG": Trim(1), "SEARCH_TERMS": Trim(1)},
                     note="spends live API budget"),
    "usajobs": Sample(module={"SEARCH_TERMS": Trim(1), "MAX_PAGES_PER_TERM": 1}),
    "startupjobs": Sample(module={"MAX_PAGES": 1}),
    "cocuma": Sample(module={"MAX_PAGES": 1}),
    "recruitee": Sample(module={"COMPANIES": Spread(3)}),
    "workable": Sample(module={"ACCOUNTS": Spread(3)}),
    "teamtailor": Sample(module={"TENANTS": Spread(3)}),
    # No bounding hook: one gzipped dump, all or nothing. ~40 s.
    "mpsv": Sample(note="downloads the full 16 MB register"),
}


#: A 403 from bot protection, which is not the same thing as a broken link: the page is fine
#: and a subscriber's browser opens it. Himalayas answers 403 to *any* HTTP client — the
#: honest agent and a spoofed Chrome string alike — and renders the posting normally in a real
#: browser once Cloudflare's check passes. Calling that DEAD would fail the run daily for a
#: source that works, which is how a red check gets ignored.
BOT_WALL_MARKERS = ("just a moment", "security verification", "attention required",
                    "enable javascript and cookies", "checking your browser",
                    "verify you are human", "cf-chl", "__cf_chl")

#: Sources whose links a human has opened in a real browser and seen the advertised job on.
#: Every one of these is client-rendered, so HTTP will never do better than SHELL/WEAK/BLOCKED
#: on them and re-reporting them every run would bury the one that is actually new. The date
#: is the point: this is a record of when someone last looked, not a permanent exemption.
BROWSER_CONFIRMED = {
    "ashby": "2026-08-08 — 8 boards spot-checked; `forto` was found dead (whole board 404s "
             "while its API still lists 12 jobs) and removed from ORGS",
    "recruitee": "2026-08-08 — `payconiq` was found dead (all offers redirect to "
                 "recruitee.com's marketing page) and removed; mailerlite renders correctly",
    "smartrecruiters": "2026-08-08 — Playtech QA Engineer renders in full; the WEAK verdict "
                       "was a short-title matching gap, since fixed",
    "workday": "2026-08-08 — NVIDIA JR2022638 renders in full; Workday ships an empty shell",
    "oraclecloud": "2026-08-08 — Vertiv 20267130 renders the right title; Oracle CX is a SPA",
    "platsbanken": "2026-08-08 — annons 31330844 renders in full; the ad pages are a SPA",
    "himalayas": "2026-08-08 — Cloudflare 403s every HTTP client; the page itself is fine",
    "workingnomads": "2026-08-08 — /job/go/{id}/ is a redirector and lands on the employer's "
                     "own application page for the right role; the title is theirs, not ours",
    "mpsv": "2026-08-08 — the app's router selects VOLNA-MISTA-DETAIL-VIEW for the fragment "
            "route and selects nothing for the old `?id=` form",
}

#: Sources `gather()` only adds when credentials are present. They are checked against their
#: modules by `--self-check` regardless, so the table cannot rot on a machine without keys —
#: which is every developer machine, i.e. exactly where the rot would go unnoticed.
CONDITIONAL = {"adzuna": "ingestion.sources.adzuna",
               "usajobs": "ingestion.sources.usajobs"}


def _name_of(cls: type) -> str:
    """`source_name` without paying for a second construction of an expensive adapter."""
    cached = getattr(cls, "_check_links_name", None)
    if cached is None:
        cached = cls().source_name
        cls._check_links_name = cached
    return cached


def _fold(text: str) -> str:
    """Casefold and strip diacritics, so 'Vývojář' matches 'vyvojar' in a mangled page."""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return _WS.sub(" ", stripped.casefold())


def visible_text(html: str) -> str:
    """Rendered text, roughly. Script and style bodies removed, not merely their tags."""
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{4,}", _fold(text)) if t not in _STOPWORDS]


def evidence(page: str, title: str, company: Optional[str]) -> tuple[Optional[str], str]:
    """(verdict, why) for a 200 that is not a closed-role page.

    **The title is the test; the employer's name is not.** A link that lands on the board's
    own index — `jobs.lever.co/spotify` instead of the requested posting — carries the
    employer's name all over it, so accepting that as proof would pass exactly the failure
    this script exists to find. Matching the title is what says *this posting* is on the page.

    Deliberately generous about *how* it matches: the question is whether the link resolved,
    not whether the page is byte-identical to the feed, so 60% of the title's distinctive
    words is enough and word order is ignored.
    """
    folded = _fold(page)
    # The whole title, verbatim. This is the strongest evidence there is, and it is checked
    # first because the token rules below can leave a real title with nothing to match on:
    # "PHP Engineer" and "QA Engineer" reduce to *no* usable tokens (`php` and `qa` are under
    # the length floor, `engineer` is a stopword), so both were reported WEAK against pages
    # that render the title in their first line and their `<title>` tag. Two false alarms in
    # one run, on two different sources.
    whole = _WS.sub(" ", _fold(title)).strip()
    if len(whole) >= 5 and whole in folded:
        return "OK", f"exact title {whole[:40]!r}"
    title_tokens = _tokens(title)
    if title_tokens:
        hit = [t for t in title_tokens if t in folded]
        # Two distinct words, not one. A single word out of a two-word title is a coin toss
        # against a page of navigation chrome: platsbanken's SPA shell "matched" *Ekonomi*
        # and *Säljare* out of its own menu and read as a working link three times over.
        need = max(min(2, len(title_tokens)), round(len(title_tokens) * 0.6))
        if len(hit) >= need:
            return "OK", f"title {len(hit)}/{len(title_tokens)} words"
    if company:
        # "Alza.cz a.s." -> "alza". Only ever a WEAK pass; see the docstring.
        for tok in _tokens(company)[:2]:
            if tok in folded:
                return "WEAK", (f"employer '{tok}' is on the page but the title is not — "
                                "could be the board's index rather than the posting")
    return None, ""


#: Statuses that mean "you are going too fast", not "this link is broken".
THROTTLE_STATUS = frozenset({429, 503})

#: How long to wait out a 429 before deciding it is real. `politeness.throttle` spaces
#: requests 1 s per host, which is fine for an export walking many hosts and *not* fine for
#: this script, which deliberately hits one board's host `-n` times in a row.
BACKOFF_SECONDS = 8


def _get(url: str):
    """GET with one polite retry on a rate-limit. Returns a response, or a string on failure.

    Found on the first wide run (`--boards 8 -n 6`): Working Nomads answered 429 to the
    fourth of six requests and the link was reported **DEAD**. It was not — we were. A
    checker that manufactures its own failures when you widen it is worse than no checker,
    because the run goes red, the red is wrong, and the next person stops reading it. So a
    rate-limit is waited out once, `Retry-After` honoured if the server sends one, and only
    reported if it survives that.
    """
    for attempt in (1, 2):
        politeness.throttle(url)
        try:
            resp = requests.get(url, headers=politeness.HEADERS, timeout=TIMEOUT,
                                allow_redirects=True)
        except requests.RequestException as exc:
            return type(exc).__name__
        if resp.status_code not in THROTTLE_STATUS or attempt == 2:
            return resp
        try:
            wait = min(float(resp.headers.get("Retry-After", BACKOFF_SECONDS)), 30.0)
        except ValueError:                          # Retry-After can also be an HTTP date
            wait = BACKOFF_SECONDS
        logger.info("%s: HTTP %d, waiting %.0fs", url[:60], resp.status_code, wait)
        time.sleep(wait)
    return resp                                     # pragma: no cover - loop always returns


def probe(url: str, title: str, company: Optional[str]) -> tuple[str, str]:
    """Fetch one posting URL and decide whether it lands on the job. (verdict, note)."""
    if not url.lower().startswith(("http://", "https://")):
        return "DEAD", f"not an http(s) url: {url[:40]!r}"
    if not politeness.robots_allows(url):
        return "ROBOTS", "robots.txt disallows this path"
    resp = _get(url)
    if isinstance(resp, str):                       # a transport failure, already described
        return "DEAD", resp
    if resp.status_code != 200:
        wall = _fold(resp.text[:4000])
        if resp.status_code in (403, 429) and any(m in wall for m in BOT_WALL_MARKERS):
            return "BLOCKED", (f"HTTP {resp.status_code} from bot protection — the page is not "
                               "necessarily broken, a browser has to say")
        if resp.status_code in THROTTLE_STATUS:
            return "THROTTLED", (f"HTTP {resp.status_code} after a retry — this is our own "
                                 "probing rate, not a broken link; lower -n or --boards")
        if resp.url.rstrip("/") != url.rstrip("/"):
            # We were redirected and *then* refused, so the link resolved and forwarded — the
            # refusal belongs to the destination. `jobs.livestorm.co` (a Recruitee board on a
            # custom domain) forwards to welcometothejungle.com, which 403s every bot and
            # serves the right job to a browser. Reporting that DEAD blames our adapter for a
            # third party's bot policy.
            return "BLOCKED", (f"HTTP {resp.status_code} from {resp.url.split('/')[2]} after a "
                               "redirect — the link resolved; the destination refuses bots")
        return "DEAD", f"HTTP {resp.status_code}"

    page = visible_text(resp.text)
    rendered = len(page) / max(len(resp.text), 1)
    folded = _fold(page)
    for marker in CLOSED_MARKERS:
        if _fold(marker) in folded:
            return "CLOSED", f"page says: {marker!r}"
    verdict, why = evidence(page, title, company)
    if verdict:
        if resp.url.rstrip("/") != url.rstrip("/"):
            why += f" (redirected to {resp.url[:70]})"
        return verdict, why
    if len(page) < SHELL_TEXT_CHARS or rendered < SHELL_TEXT_RATIO:
        return "SHELL", (f"{len(page)} chars of text in {len(resp.text)} of HTML "
                         f"({rendered:.1%}) — client-rendered, needs a browser")
    return "MISMATCH", f"{len(page)} chars of text, no sign of the job or the employer"


def _apply(target: Any, overrides: dict[str, Any], label: str) -> None:
    for attr, value in overrides.items():
        if not hasattr(target, attr):
            raise AttributeError(
                f"{label}.{attr} is gone — the sampling table in check_links.py has drifted "
                f"from the adapter. Fix the table; do not delete the entry.")
        current = getattr(target, attr)
        setattr(target, attr, value.of(current, f"{label}.{attr}")
                if isinstance(value, Trim) else value)


def postings_for(cls: type, want: int) -> list[Any]:
    """Run one adapter under its sampling bounds and return up to `want` postings.

    Module overrides are applied **before** the source is constructed: several adapters read
    their module constants in `__init__` (The Muse's locations, Greenhouse's board tokens),
    so patching afterwards would silently do nothing and quietly run the full list.
    """
    import importlib

    sample = SAMPLING.get(_name_of(cls), Sample())
    module = importlib.import_module(cls.__module__)
    _apply(module, sample.module, cls.__module__)
    src = cls()
    _apply(src, sample.instance, cls.__name__)
    return _pick(src.normalize(src.fetch()), want)


def _pick(postings: list, want: int) -> list:
    """`want` postings spread across employers, not the head of the list.

    Widening the *board* list is not enough on its own, and the first wide run proved it:
    with eight Greenhouse boards fetched, all six probed links were still Stripe's, because
    `normalize` concatenates board by board and Stripe alone carries hundreds of postings.
    Eight boards were paid for and one was tested.

    Round-robin by employer fixes it without assuming anything about ordering, and it also
    drops duplicate URLs — Workable listed one job twice in the same run, which would have
    spent a probe re-testing a link already checked.
    """
    groups: dict[str, list] = {}
    seen: set[str] = set()
    for p in postings:
        if p.url in seen:
            continue
        seen.add(p.url)
        # Employer where there is one; otherwise the host, which at least separates boards.
        key = (p.company or "").casefold().strip() or p.url.split("/", 3)[2]
        groups.setdefault(key, []).append(p)

    out: list = []
    while len(out) < want and any(groups.values()):
        for key in list(groups):
            if groups[key]:
                out.append(groups[key].pop(0))
                if len(out) >= want:
                    break
    return out


def check_source(cls: type, want: int) -> list[dict]:
    src_name = _name_of(cls)
    try:
        postings = postings_for(cls, want)
    except Exception as exc:                       # an adapter that cannot run is a finding
        logger.warning("%s: could not sample (%s)", src_name, exc)
        return [{"source": src_name, "verdict": "NO SAMPLE", "note": f"{type(exc).__name__}: {exc}",
                 "url": "", "title": ""}]
    if not postings:
        return [{"source": src_name, "verdict": "NO SAMPLE", "note": "adapter returned nothing",
                 "url": "", "title": ""}]

    rows = []
    for p in postings:
        verdict, note = probe(p.url, p.title or "", p.company)
        rows.append({"source": src_name, "verdict": verdict, "note": note,
                     "url": p.url, "title": p.title, "company": p.company})
        print(f"  {verdict:9} {p.url[:96]}\n            {note}", flush=True)
    return rows


def self_check() -> int:
    """Fail if the sampling table names an attribute an adapter no longer has.

    The table is a copy of things that live in the adapters, and this repo has been bitten by
    exactly that before (`pending_boards.py` grepping source text, the CI skip-check listing
    filenames). A stale entry here does not error at run time — it silently stops bounding a
    source, and the next run takes 40 minutes instead of 4.
    """
    import importlib

    bad = []
    known = {_name_of(cls): cls for cls in source_classes(include_cz=True)}
    for name, sample in SAMPLING.items():
        if name in known:
            module_path = known[name].__module__
        elif name in CONDITIONAL:
            # Absent from gather() only because this machine has no key for it.
            module_path = CONDITIONAL[name]
        else:
            bad.append(f"{name}: in SAMPLING but not in search_jobs.source_classes()")
            continue
        module = importlib.import_module(module_path)
        for attr, value in sample.module.items():
            if not hasattr(module, attr):
                bad.append(f"{name}: module attribute {attr} is gone")
            elif isinstance(value, Trim):
                try:
                    value.of(getattr(module, attr), f"{name}.{attr}")
                except TypeError as exc:
                    bad.append(str(exc))
    for name in known:
        if name not in SAMPLING:
            bad.append(f"{name}: runs in gather() but has no SAMPLING entry (unbounded run)")
    for line in bad:
        print(f"DRIFT  {line}")
    print("sampling table matches the adapters" if not bad else f"{len(bad)} problems")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-s", "--source", action="append", default=[],
                    help="source name (repeatable); default is every source in gather()")
    ap.add_argument("-n", "--links", type=int, default=3, help="links per source (default 3)")
    ap.add_argument("--boards", type=int,
                    help="how many boards/tenants to sample per ATS (default: the small "
                         "number in SAMPLING). Applies to `Spread` entries only — widening a "
                         "search-term or tag list multiplies requests without exercising any "
                         "new URL construction, which is the thing being tested.")
    ap.add_argument("--json", help="write the full result table here")
    ap.add_argument("--self-check", action="store_true",
                    help="verify the sampling table still matches the adapters, then exit")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    if args.boards:
        for sample in SAMPLING.values():
            for overrides in (sample.module, sample.instance):
                for marker in overrides.values():
                    if isinstance(marker, Spread):
                        marker.n = args.boards

    classes = source_classes(include_cz=True)
    if args.source:
        wanted = set(args.source)
        classes = [c for c in classes if _name_of(c) in wanted]
        missing = wanted - {_name_of(c) for c in classes}
        if missing:
            print(f"unknown source(s): {', '.join(sorted(missing))}")
            return 2

    rows: list[dict] = []
    for cls in classes:
        name = _name_of(cls)
        note = SAMPLING.get(name, Sample()).note
        print(f"\n{name}{f'  ({note})' if note else ''}", flush=True)
        rows.extend(check_source(cls, args.links))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("\n" + "=" * 72)
    print("  ".join(f"{v}={n}" for v, n in sorted(counts.items())))

    broken = sorted({r["source"] for r in rows if r["verdict"] in ("DEAD", "MISMATCH")})
    unproven = sorted({r["source"] for r in rows
                       if r["verdict"] in ("SHELL", "WEAK", "BLOCKED", "THROTTLED",
                                           "NO SAMPLE")})
    # Split by whether anyone has ever looked. Re-listing the same six client-rendered
    # sources every run is how the one that is genuinely new gets missed.
    known = [s for s in unproven if s in BROWSER_CONFIRMED]
    fresh = [s for s in unproven if s not in BROWSER_CONFIRMED]
    if broken:
        print(f"BROKEN LINKS: {', '.join(broken)}")
    if fresh:
        print(f"NOT PROVEN, AND NOBODY HAS LOOKED — open one in a browser: {', '.join(fresh)}")
    for src in known:
        print(f"  not provable over HTTP; {BROWSER_CONFIRMED[src]}  [{src}]")
    return 1 if broken or fresh else 0


if __name__ == "__main__":
    raise SystemExit(main())
