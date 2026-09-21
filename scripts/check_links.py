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
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

# The pure prober now lives in `ingestion/link_probe.py` so `service/liveness.py` can share one
# definition of "is this link dead" (the same reason geo/taxonomy/seniority are single-sourced).
# This module keeps its sampling table and reporting; only the per-URL verdict logic moved.
from ingestion.link_probe import probe, _fold  # noqa: E402,F401
from search_jobs import source_classes  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("check_links")

try:                                    # Windows consoles still default to cp1250
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                  # pragma: no cover - non-reconfigurable stream
    pass

# `TIMEOUT`, `CLOSED_MARKERS`, `SHELL_TEXT_*`, `_STOPWORDS`, `_TAG`, `_WS`, `_fold`,
# `visible_text`, `_tokens`, `evidence`, `BOT_WALL_MARKERS`, `_get` and `probe` all moved to
# `ingestion/link_probe.py` on 2026-08-20. `probe` and `_fold` are imported above.


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
    # `nav` bounds the DETAIL fan-out, not a board list: its feed walk is ~11 requests and the
    # cost is one call per changed ad. `max_details` is a constructor argument rather than a
    # module constant because the module constant bounds production, and a check-links run must
    # be able to shrink it without editing what ships. It also gets its own state directory —
    # a sampled run must never write the production mirror, which would persist a cursor past
    # ads it never fetched details for and leave them permanently missing.
    "nav": Sample(instance={"_max_details": 5},
                  note="walks ~11 feed pages; writes a throwaway mirror"),
    # `smartrecruiters` was here until 2026-08-11 and is gone for the same reason `jobscz` and
    # `profesia` never appear: a source `search_jobs.source_classes()` does not run has no link
    # to check, and `self_check()` reports the leftover row as DRIFT — correctly. That is the
    # table's whole purpose, and it caught this removal within the same commit.
    # `startupjobs` was here until 2026-08-26 and left the same way, and the same test caught
    # it the same commit — the row outlived the source by exactly one `pytest` run. Its removal
    # is worth a second line because the *link* is what killed it: the board moved its API to a
    # host whose robots refuses `*`, and the only robots-permitted host lists its offers as ids
    # without the slug `/job/{id}` requires. The 2026-08-06 entry in this module's own docstring
    # was the first act of that story.
    "workday": Sample(module={"SITES": Spread(1), "SEARCH_TERMS": Trim(1),
                              "MAX_PAGES_PER_QUERY": 1, "MAX_DETAILS": 5}),
    "adzuna": Sample(module={"COUNTRY_CONFIG": Trim(1), "SEARCH_TERMS": Trim(1)},
                     note="spends live API budget"),
    "usajobs": Sample(module={"SEARCH_TERMS": Trim(1), "MAX_PAGES_PER_TERM": 1}),
    "cocuma": Sample(module={"MAX_PAGES": 1}),
    "recruitee": Sample(module={"COMPANIES": Spread(3)}),
    "workable": Sample(module={"ACCOUNTS": Spread(3)}),
    "teamtailor": Sample(module={"TENANTS": Spread(3)}),
    # `werkenvoornederland` is N+1 (one page per vacancy), so `MAX_POSTINGS` is the only
    # bound that matters — an unbounded run is ~1 260 fetches at 1 s/host. The module
    # constant bounds production; this shrinks it for a link check without editing what ships.
    "werkenvoornederland": Sample(module={"MAX_POSTINGS": 5},
                                  note="one request per vacancy; sitemap fetch on top"),
    # `remoteineurope` sat here until 2026-09-21 and was dropped with the adapter: the board
    # now 301s to weworkremotely.com and its sitemap 404s. Removing it here is not optional —
    # `self_check()` fails on a name in SAMPLING that gather() no longer runs, which is how
    # this edit got made at all.
    # Same N+1 shape: one page per job, `MAX_POSTINGS` is the only bound.
    "goldencareers": Sample(module={"MAX_POSTINGS": 5},
                            note="one request per vacancy; sitemap fetch on top"),
    # No bounding hook: one gzipped dump, all or nothing. ~40 s.
    "mpsv": Sample(note="downloads the full 16 MB register"),
}


#: Sources whose links have been confirmed to resolve by something HTTP cannot do. Every one is
#: client-rendered, so an automated run will never do better than SHELL/WEAK/BLOCKED on them and
#: re-reporting them every run would bury the one that is actually new. The date is the point:
#: this is a record of when someone last checked, not a permanent exemption.
#:
#: **Two kinds of evidence appear here and the entry must say which.** Most are a human opening
#: the page and seeing the advertised job. `mpsv` is instead a *static trace of the
#: app's own router* — the shipped bundle read end to end, from the route table through the
#: controller to the API call it makes. That is weaker on rendering (it cannot see a login wall
#: or an empty template) and stronger on routing (it proves which URL form the router accepts,
#: which is the half that was wrong both times this repo shipped dead links). Where an entry is
#: a static trace, say so, so nobody reads it as "someone looked at the page".
BROWSER_CONFIRMED = {
    "ashby": "2026-08-08 — 8 boards spot-checked; `forto` was found dead (whole board 404s "
             "while its API still lists 12 jobs) and removed from ORGS",
    "recruitee": "2026-08-08 — `payconiq` was found dead (all offers redirect to "
                 "recruitee.com's marketing page) and removed; mailerlite renders correctly",
    "greenhouse": "2026-08-08 — full 178-board sweep. form3 (404 to every HTTP client) and "
                  "roblox (times out) both render their postings in a browser; trivago's "
                  "apply page loads 510 characters of nav and no job, and is unresolved",
    "lever": "2026-08-08 — full 49-board sweep; aircall and ledger were found dead at the "
             "board root and swapped for their live Greenhouse/Ashby boards",
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


# `_fold`, `visible_text`, `_tokens`, `evidence`, `_get` and `probe` moved to
# `ingestion/link_probe.py` (see the import near the top). This module still calls `probe`
# (in `check_source`) and `_fold` (in `_slugish`/`_url_tokens`), both imported from there.


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

    # How much of the source's board list is actually behind these links, and — the part that
    # matters at full coverage — which boards contributed **nothing**.
    #
    # A board that yields no posting is not "fine", it is *unprobed*, and at 650 boards that
    # is not a rare edge: `boards-api.greenhouse.io` starts read-timing-out under a long
    # sequential sweep, and a timed-out board is swallowed by the adapter as a warning and an
    # empty list. Counting those as checked would make "all 650 verified" false — the silent
    # zero this repo keeps re-learning, this time inside the checker itself. `ashby:forto`
    # is the standing proof that silence here cannot be read as health.
    configured = _boards_configured(cls)
    if configured is not None:
        covered = {_slugish(r["company"]) for r in rows}
        for r in rows:
            covered |= _url_tokens(r["url"])
        missing = sorted(b for b in configured if not _covered(b, covered))
        print(f"  -- {len(configured) - len(missing)} of {len(configured)} boards produced "
              f"a link", flush=True)
        if missing:
            print(f"  -- NO LINK from {len(missing)}: {', '.join(missing[:40])}"
                  + (" …" if len(missing) > 40 else ""), flush=True)
        for board in missing:
            rows.append({"source": src_name, "verdict": "NO LINK", "url": "", "title": "",
                         "company": board,
                         "note": "board contributed no posting — unprobed, not verified"})
    return rows


_SLUG_TAIL = re.compile(r"\d+$")


def _slugish(text: Optional[str]) -> str:
    """Lowercase alphanumerics only — the one form a slug and a display name can share.

    A board list holds slugs (`critical-manufacturing`, `hellasdirect`) while a posting
    carries the employer's display name ("Critical Manufacturing", "Hellas Direct"), so a
    literal comparison reports working boards as unprobed. It did: the first full-coverage
    run called `skroutz` uncovered while an OK link from that very board sat three lines
    above. A checker whose *accounting* is wrong is worse than one that checks less.
    """
    return re.sub(r"[^a-z0-9]", "", _fold(text or ""))


def _url_tokens(url: str) -> set[str]:
    """Host labels plus the first path segment — the places a board slug shows up in a link.

    Slugifying the whole URL does not work: Isabel Group's board is `isabelgroup` but its
    postings live on `careers.isabel.eu`, and neither string contains the other once the
    whole URL is mashed together. Splitting the host into labels gives `isabel`, which *is*
    inside `isabelgroup`. Without this the run reported a board with six live postings as
    unprobed.
    """
    if "://" not in url:
        return set()
    rest = url.split("://", 1)[1]
    host, _, path = rest.partition("/")
    parts = [p for p in host.split(".") if p not in ("www", "com", "org", "net", "io", "eu")]
    parts += path.split("/")[:1]
    return {_slugish(p) for p in parts if len(_slugish(p)) >= 2}


def _covered(board: str, seen: set[str]) -> bool:
    slug = _slugish(board)
    if not slug:
        return True                                   # nothing to reconcile against
    # Matching runs **both ways**, and each direction is load-bearing:
    #   slug inside the seen string — `viva` inside `apply.workable.com/viva/j/...`
    #   seen string inside the slug — `fenergo` (the employer's display name) inside the slug
    #                                 `fenergocareers`; likewise `plum` inside `withplum`
    # A one-directional test reported all three as unprobed while their boards were serving
    # 10, 87 and 34 jobs. `booksy-1` needs the trailing-number trim on top: Workable and
    # Recruitee both hand out numbered slugs when a name is taken.
    trimmed = _SLUG_TAIL.sub("", slug)
    for s in seen:
        if not s:
            continue
        # Exact at any length, substring only from four characters up. Both halves are
        # needed: `tet` and `lmt` are real Teamtailor tenants and a length floor alone
        # reported them unprobed, while a substring rule that short would match anything.
        if s == slug or s == trimmed:
            return True
        if len(s) >= 4 and (slug in s or s in slug
                            or (trimmed and (trimmed in s or s in trimmed))):
            return True
    return False


def _boards_configured(cls: type) -> Optional[list[str]]:
    """The board/tenant identifiers this source is configured with, as plain strings.

    Only the sources whose list is a flat list of slugs can be reconciled against the
    employer names on their postings; Workday and Oracle carry tuples whose slug is not what
    `company` ends up holding, so they report None and are covered by the totals alone.
    """
    import importlib

    module = importlib.import_module(cls.__module__)
    for attr in ("ORGS", "TENANTS", "COMPANIES", "ACCOUNTS"):
        value = getattr(module, attr, None)
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return list(value)
    tokens = getattr(cls(), "_board_tokens", None)      # greenhouse loads its list per-instance
    if isinstance(tokens, list) and all(isinstance(t, str) for t in tokens):
        return tokens
    return None


def _board_count(cls: type) -> Optional[int]:
    """How many boards/tenants this source is configured with, or None if not that shape."""
    import importlib

    module = importlib.import_module(cls.__module__)
    for attr in ("ORGS", "SITES", "TENANTS", "COMPANIES", "ACCOUNTS"):
        value = getattr(module, attr, None)
        if isinstance(value, (list, tuple)):
            return len(value)
    tokens = getattr(cls(), "_board_tokens", None)      # greenhouse loads its list per-instance
    return len(tokens) if isinstance(tokens, list) else None


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
                         "number in SAMPLING; 0 means every one). Applies to `Spread` entries "
                         "only — widening a search-term or tag list multiplies requests "
                         "without exercising any new URL construction, which is the thing "
                         "being tested.")
    ap.add_argument("--details", type=int,
                    help="override MAX_DETAILS on the N+1 adapter (workday). "
                         "Required with --boards 0: that budget is global, so the default 5 "
                         "would leave most boards with no posting to probe.")
    ap.add_argument("--json", help="write the full result table here")
    ap.add_argument("--self-check", action="store_true",
                    help="verify the sampling table still matches the adapters, then exit")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    if args.boards is not None:
        # 0 means every board. `Spread.of` already returns the whole list when it is shorter
        # than `n`, so a number above any real list is exactly "all" with no special case.
        n = 10_000 if args.boards == 0 else args.boards
        for sample in SAMPLING.values():
            for overrides in (sample.module, sample.instance):
                for marker in overrides.values():
                    if isinstance(marker, Spread):
                        marker.n = n
    if args.details:
        # `MAX_DETAILS` is a *global* budget in the N+1 adapters, not per tenant: at the
        # default 5 a 74-site Workday sweep would describe five postings and leave 69 boards
        # with nothing to probe — full board coverage that silently is not.
        for sample in SAMPLING.values():
            if "MAX_DETAILS" in sample.module:
                sample.module["MAX_DETAILS"] = args.details

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

    unprobed = [r for r in rows if r["verdict"] == "NO LINK"]
    if unprobed:
        by_source: dict[str, int] = {}
        for r in unprobed:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        print("UNPROBED boards (contributed no posting — NOT verified): "
              + ", ".join(f"{s} {n}" for s, n in sorted(by_source.items())))

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
