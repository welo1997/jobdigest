"""Fetch one stored job URL and decide whether it still lands on the advertised job.

This is the *pure prober* — no sampling, no source catalogue, no reporting. It was extracted
from `scripts/check_links.py` on 2026-08-20 so that two callers can share one definition of
"is this link dead", the way this repo shares `geo`, `taxonomy` and `search_jobs.seniority`:

  * `scripts/check_links.py` — the periodic, human-run audit that samples a few URLs per source
    and prints a verdict table. It keeps all of its sampling/reporting machinery; it imports
    `probe` and the marker tables from here.
  * `service/liveness.py` — the daily in-pipeline sweep that probes the postings subscribers can
    actually see (matched rows + today's shortlists) and deactivates the ones a page positively
    says are gone. It ships in the image; `scripts/` does not, which is the concrete reason this
    module lives under `ingestion/` (copied into the backend image) rather than staying in
    `scripts/`.

**The verdict taxonomy is the valuable part and it is deliberately conservative.** A 200 is not
evidence a job is live (client-rendered boards render an identical shell for a dead posting), and
— the correction that matters most — **a bare 404 is not evidence a job is dead**: `form3` answers
404 to every HTTP client and renders "Finance Manager" in full in a browser, `roblox` times out and
renders fine. So `probe` never returns a `DEAD`/gone verdict off a status code alone. The only
positive "this role is gone" signal it trusts is a **CLOSED marker** — real server-rendered text
that says, in so many words, that the posting expired or was not found. Everything ambiguous gets
its own non-committal verdict (`SHELL`, `BLOCKED`, `UNCONFIRMED`, `UNREACHABLE`, `THROTTLED`,
`MISMATCH`, `WEAK`) so a caller can choose to act only on the confident ones. `service/liveness.py`
acts on `CLOSED` and abstains on the rest, which is why a false deactivation would require a *live*
job's page to contain closed-listing prose — a very low false-positive action.

The full history behind each verdict (the MPSV fragment-route bug, the startupjobs missing-slug
bug, form3/roblox, Platsbanken's SPA shell, Himalayas' Cloudflare wall) is in
`scripts/check_links.py`'s module docstring and `docs/sources.md`; it is not repeated here.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Optional

import requests

from ingestion import politeness

TIMEOUT = 25

#: A 200 (or any status) whose *rendered* text says the role is gone. This is the ONLY positive
#: "deactivate me" signal in the whole module, so every entry has to be unambiguous: a phrase that
#: appears on a *live* posting would silently retire a working job for everyone. Kept short and
#: literal for that reason. Diacritics are handled by `_fold`, so the Czech forms carry their
#: accents here and match an accent-stripped page too.
CLOSED_MARKERS = [
    "no longer accepting applications", "no longer available", "no longer active",
    "position has been filled", "job has been filled", "this job has expired",
    "job is no longer", "position is closed", "vacancy has expired",
    # startupjobs.com renders both of these on an expired offer (the site's own template drops
    # the "up" from "up-to-date", so the reliable half is the "no longer valid" sentence).
    "the offer is no longer valid", "offer is no longer valid",
    # cocuma.cz answers 404 with real server-rendered Czech text on a taken/removed opportunity.
    "příležitost nenalezena", "příležitost již vychytal",
    "nabídka již není aktuální", "nabídka byla obsazena", "již byla obsazena",
    "tato pozice již není", "stránka nebyla nalezena", "page not found",
    "tjänsten är tillsatt", "annonsen är borttagen",
]

#: Below this many characters of visible text, the page did not render server-side and no HTTP
#: client can tell a real posting from a 404. See SHELL in `scripts/check_links.py`.
SHELL_TEXT_CHARS = 500

#: …and neither can it when the page is *mostly script*. Platsbanken answers 124 KB of HTML holding
#: 1 619 characters of navigation chrome (1.3%) and no ad text at all.
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

#: A 403 from bot protection, which is not the same thing as a broken link: the page is fine and a
#: subscriber's browser opens it. Himalayas answers 403 to *any* HTTP client and renders normally in
#: a real browser once Cloudflare's check passes.
BOT_WALL_MARKERS = ("just a moment", "security verification", "attention required",
                    "enable javascript and cookies", "checking your browser",
                    "verify you are human", "cf-chl", "__cf_chl")

#: Statuses that mean "you are going too fast", not "this link is broken".
THROTTLE_STATUS = frozenset({429, 503})

#: How long to wait out a 429 before deciding it is real.
BACKOFF_SECONDS = 8


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


def is_closed(page_text: str) -> bool:
    """True if the rendered page text carries a closed/expired/not-found marker.

    The one positive "this role is gone" test, factored out so it can be unit-tested against real
    page snippets without a network round trip.
    """
    folded = _fold(page_text)
    return any(_fold(m) in folded for m in CLOSED_MARKERS)


def evidence(page: str, title: str, company: Optional[str]) -> tuple[Optional[str], str]:
    """(verdict, why) for a 200 that is not a closed-role page.

    **The title is the test; the employer's name is not.** A link that lands on the board's own
    index carries the employer's name all over it, so accepting that as proof would pass exactly
    the failure this exists to find. Matching the title is what says *this posting* is on the page.
    """
    folded = _fold(page)
    whole = _WS.sub(" ", _fold(title)).strip()
    if len(whole) >= 5 and whole in folded:
        return "OK", f"exact title {whole[:40]!r}"
    title_tokens = _tokens(title)
    if title_tokens:
        hit = [t for t in title_tokens if t in folded]
        need = max(min(2, len(title_tokens)), round(len(title_tokens) * 0.6))
        if len(hit) >= need:
            return "OK", f"title {len(hit)}/{len(title_tokens)} words"
    if company:
        for tok in _tokens(company)[:2]:
            if tok in folded:
                return "WEAK", (f"employer '{tok}' is on the page but the title is not — "
                                "could be the board's index rather than the posting")
    return None, ""


def _get(url: str):
    """GET with one polite retry on a rate-limit. Returns a response, or a string on failure."""
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
        time.sleep(wait)
    return resp                                     # pragma: no cover - loop always returns


def probe(url: str, title: str, company: Optional[str]) -> tuple[str, str]:
    """Fetch one posting URL and decide whether it lands on the job. (verdict, note).

    Verdicts: ``OK``, ``WEAK``, ``CLOSED``, ``SHELL``, ``MISMATCH``, ``BLOCKED``, ``THROTTLED``,
    ``UNCONFIRMED``, ``UNREACHABLE``, ``ROBOTS``, ``DEAD`` (the last only for a non-http URL). The
    only verdict a caller should treat as "this role is gone" is ``CLOSED`` — see the module
    docstring for why a bare 404 is ``UNCONFIRMED`` rather than dead.
    """
    if not url.lower().startswith(("http://", "https://")):
        return "DEAD", f"not an http(s) url: {url[:40]!r}"
    if not politeness.robots_allows(url):
        return "ROBOTS", "robots.txt disallows this path"
    resp = _get(url)
    if isinstance(resp, str):                       # a transport failure, already described
        return "UNREACHABLE", f"{resp} — no answer to an HTTP client; a browser has to say"
    if resp.status_code != 200:
        # A closed marker in a non-200 body is still a confident CLOSED — cocuma answers 404 with
        # real "opportunity not found" text. Checked before the ambiguity branches below.
        if is_closed(resp.text):
            return "CLOSED", f"HTTP {resp.status_code} and the page says the role is gone"
        wall = _fold(resp.text[:4000])
        if resp.status_code in (403, 429) and any(m in wall for m in BOT_WALL_MARKERS):
            return "BLOCKED", (f"HTTP {resp.status_code} from bot protection — the page is not "
                               "necessarily broken, a browser has to say")
        if resp.status_code in THROTTLE_STATUS:
            return "THROTTLED", (f"HTTP {resp.status_code} after a retry — this is our own "
                                 "probing rate, not a broken link; lower the rate")
        if resp.url.rstrip("/") != url.rstrip("/"):
            return "BLOCKED", (f"HTTP {resp.status_code} from {resp.url.split('/')[2]} after a "
                               "redirect — the link resolved; the destination refuses bots")
        return "UNCONFIRMED", (f"HTTP {resp.status_code} — could be a dead link or bot "
                               "protection; check the board root in a browser")

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


#: The verdicts `service/liveness.py` treats as a confident "deactivate this posting". Exactly one
#: today, named as a set so the contract is explicit and a test can assert nothing else is added
#: without thought — abstaining on ambiguity is the whole safety argument.
DEACTIVATE_VERDICTS = frozenset({"CLOSED"})
