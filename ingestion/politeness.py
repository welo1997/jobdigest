"""One user agent, one robots.txt gate, one crawl delay — for every source.

Three things live here because each of them was previously either absent or copy-pasted
thirteen times, and all three are the difference between "a bot reading public pages" and
"something that disguised itself":

* **`USER_AGENT` names us and says where to complain.** Every adapter used to send a browser
  string — the scrapers sent a bare ``Mozilla/5.0``. That buys nothing technically (no board
  here gates on it) and is the single worst fact to have to explain: a site owner reading
  their logs cannot tell you from a scraper worth blocking, and cannot reach you without
  guessing. An honest agent is also what makes a `Disallow: JobDigest` line *possible*, which
  is the polite exit a board should have.

* **`robots_allows()` is checked before fetching.** All four scraped hosts permit our paths
  today (verified 2026-08-03), so this changes nothing now — which is the point. It is a
  standing check so that a board that *adds* a Disallow gets obeyed on the next run rather
  than whenever someone next reads their robots.txt by hand.

* **`throttle()` spaces requests per host.** Nothing in `ingestion/` slept before, so jobs.cz
  took up to 30 page requests per field back to back. No board here declares a `Crawl-delay`,
  so the default is ours to choose; one second is far below anything that would register as
  load and costs a scraper run about half a minute.

**Fail-open, deliberately.** An unreachable or unparseable robots.txt allows the fetch and
logs it. Treating a 500 as "disallow everything" would turn a board's bad afternoon into our
silent zero — the exact failure this repo keeps finding (a source returning a plausible
number, or nothing, with no error anywhere). An explicit `Disallow` is obeyed and logged
loudly; anything else is a warning.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

#: Where a site owner can read what we do and how to stop us.
CONTACT_URL = "https://jobdigest.eu/en/terms/"
CONTACT_EMAIL = "hello@jobdigest.eu"

#: Sent by every adapter. The token before the slash is what a robots.txt would name.
USER_AGENT = f"JobDigest/1.0 (+{CONTACT_URL}; {CONTACT_EMAIL})"
#: The robots.txt agent token, i.e. what `User-agent: JobDigest` would match.
ROBOTS_AGENT = "JobDigest"

HEADERS = {"User-Agent": USER_AGENT}

#: Seconds between requests to the same host when robots.txt declares no Crawl-delay.
DEFAULT_DELAY = 1.0

_ROBOTS_TIMEOUT = 10

_lock = threading.Lock()
_robots: dict[str, RobotFileParser | None] = {}      # host -> parser, None = fetch failed
_last_request: dict[str, float] = {}                 # host -> monotonic seconds


def _host(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _parser(url: str) -> RobotFileParser | None:
    """The cached robots.txt parser for this URL's host, or None if it could not be read."""
    host = _host(url)
    with _lock:
        if host in _robots:
            return _robots[host]
    parser: RobotFileParser | None = None
    try:
        resp = requests.get(f"{host}/robots.txt", headers=HEADERS, timeout=_ROBOTS_TIMEOUT)
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
        elif 400 <= resp.status_code < 500:
            # No robots.txt is an allow-all, per the standard and every major crawler.
            parser = RobotFileParser()
            parser.parse([])
        else:
            logger.warning("robots.txt for %s returned HTTP %s — proceeding",
                           host, resp.status_code)
    except requests.RequestException as exc:
        logger.warning("robots.txt for %s could not be read (%s) — proceeding", host, exc)
    with _lock:
        _robots[host] = parser
    return parser


def robots_allows(url: str) -> bool:
    """May we fetch this URL, per the host's robots.txt? Unknown answers are a yes."""
    parser = _parser(url)
    if parser is None:
        return True
    # Checked under our own token first: a board that wants to address us specifically should
    # be able to, and `*` is the fallback the parser applies when no JobDigest block exists.
    return parser.can_fetch(ROBOTS_AGENT, url)


def crawl_delay(url: str) -> float:
    """The host's declared Crawl-delay, or `DEFAULT_DELAY`. Never less than the default."""
    parser = _parser(url)
    declared = None
    if parser is not None:
        try:
            declared = parser.crawl_delay(ROBOTS_AGENT)
        except Exception:                      # noqa: BLE001 - older parsers lack the method
            declared = None
    return max(DEFAULT_DELAY, float(declared or 0))


def throttle(url: str) -> None:
    """Sleep so that requests to this host are at least `crawl_delay` apart.

    Per host, not global: a run touching four boards is not four times slower, and the one
    thing being protected is any single site's server.
    """
    host = _host(url)
    delay = crawl_delay(url)
    with _lock:
        last = _last_request.get(host)
        now = time.monotonic()
        wait = 0.0 if last is None else max(0.0, delay - (now - last))
        _last_request[host] = now + wait
    if wait:
        time.sleep(wait)


def reset_cache() -> None:
    """Forget cached robots.txt and timing state. For tests."""
    with _lock:
        _robots.clear()
        _last_request.clear()
