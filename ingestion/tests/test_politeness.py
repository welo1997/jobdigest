"""The identify-yourself / obey-robots / don't-hammer guarantees, and the drift test.

Written to fail when the guarantee breaks rather than merely to pass: the drift test at the
bottom is the one that matters, because the failure mode here is not a bug, it is somebody
copy-pasting a browser user agent into the fourteenth adapter and nothing noticing.
"""

from __future__ import annotations

import requests

from ingestion import politeness


class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code = status
        self.text = text


def _serve(monkeypatch, resp):
    """Make every robots.txt fetch return `resp` (or raise, if it is an exception)."""
    def fake_get(url, **kwargs):
        assert url.endswith("/robots.txt")
        assert kwargs["headers"]["User-Agent"] == politeness.USER_AGENT
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(politeness.requests, "get", fake_get)
    politeness.reset_cache()


# --- the user agent ------------------------------------------------------------

def test_user_agent_identifies_us_and_says_how_to_complain():
    ua = politeness.USER_AGENT
    assert "Mozilla" not in ua, "a browser string is a disguise, not an identifier"
    assert ua.startswith(politeness.ROBOTS_AGENT + "/"), \
        "the leading token is what a robots.txt would name — keep it first"
    assert politeness.CONTACT_URL in ua and politeness.CONTACT_EMAIL in ua


# --- robots.txt ----------------------------------------------------------------

def test_disallowed_path_is_refused(monkeypatch):
    _serve(monkeypatch, _Resp(200, "User-agent: *\nDisallow: /jobs/\n"))
    assert not politeness.robots_allows("https://example.com/jobs/page/2/")
    assert politeness.robots_allows("https://example.com/prace/")


def test_a_rule_naming_us_is_obeyed(monkeypatch):
    """A board that wants to address JobDigest specifically must be able to."""
    _serve(monkeypatch, _Resp(200, f"User-agent: {politeness.ROBOTS_AGENT}\nDisallow: /\n"))
    assert not politeness.robots_allows("https://example.com/anything")


def test_missing_robots_is_allow_all(monkeypatch):
    _serve(monkeypatch, _Resp(404))
    assert politeness.robots_allows("https://example.com/jobs/")


def test_unreachable_robots_fails_open(monkeypatch):
    """A board's bad afternoon must not become our silent zero.

    The alternative — treating an error as Disallow — turns a 500 into a source that fetches
    nothing and reports no failure, which is the exact shape of every ingestion bug this repo
    has had to find the hard way.
    """
    _serve(monkeypatch, requests.RequestException("boom"))
    assert politeness.robots_allows("https://example.com/jobs/")
    _serve(monkeypatch, _Resp(503))
    assert politeness.robots_allows("https://example.com/jobs/")


def test_robots_is_fetched_once_per_host(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _Resp(200, "User-agent: *\nDisallow: /nope/\n")

    monkeypatch.setattr(politeness.requests, "get", fake_get)
    politeness.reset_cache()
    for _ in range(5):
        politeness.robots_allows("https://example.com/jobs/")
    politeness.robots_allows("https://other.example/jobs/")
    assert len(calls) == 2, "robots.txt must be cached per host, not refetched per page"


# --- throttling ----------------------------------------------------------------

def test_throttle_spaces_requests_to_one_host(monkeypatch):
    _serve(monkeypatch, _Resp(200, "User-agent: *\n"))
    slept: list[float] = []
    monkeypatch.setattr(politeness.time, "sleep", slept.append)

    politeness.throttle("https://example.com/a")     # first request: no wait
    politeness.throttle("https://example.com/b")
    politeness.throttle("https://example.com/c")

    assert slept and all(s > 0 for s in slept)
    assert sum(slept) >= politeness.DEFAULT_DELAY


def test_throttle_is_per_host(monkeypatch):
    _serve(monkeypatch, _Resp(200, "User-agent: *\n"))
    slept: list[float] = []
    monkeypatch.setattr(politeness.time, "sleep", slept.append)

    politeness.throttle("https://a.example/x")
    politeness.throttle("https://b.example/x")
    assert not slept, "a run touching two boards must not be twice as slow"


def test_declared_crawl_delay_wins_when_longer(monkeypatch):
    _serve(monkeypatch, _Resp(200, "User-agent: *\nCrawl-delay: 5\n"))
    assert politeness.crawl_delay("https://example.com/x") == 5.0
    _serve(monkeypatch, _Resp(200, "User-agent: *\nCrawl-delay: 0.1\n"))
    assert politeness.crawl_delay("https://example.com/x") == politeness.DEFAULT_DELAY


# --- drift ---------------------------------------------------------------------

def test_no_adapter_sends_a_browser_user_agent():
    """The guard that actually earns its place.

    Thirteen adapters each carried their own header dict, three of them a bare `Mozilla/5.0`.
    Consolidating them fixes today; this fails the build when the fourteenth arrives with a
    copy-pasted browser string.
    """
    import pathlib
    offenders = []
    for path in sorted(pathlib.Path("ingestion/sources").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "Mozilla" in text:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} send a browser user agent — use politeness.HEADERS / "
        "politeness.USER_AGENT so a site owner can tell who we are and how to reach us"
    )


def test_every_adapter_that_sets_a_user_agent_uses_the_shared_one():
    import pathlib
    # usajobs is the one documented exception: the USAJOBS API *requires* the User-Agent to be
    # the caller's registered email address — it is the account identifier the API authenticates
    # on, alongside Authorization-Key. That is more identifying than the shared agent, not an
    # attempt to look like a browser, so it is exempt. Any other adapter mentioning User-Agent
    # must use `politeness.USER_AGENT`.
    exempt = {"usajobs.py"}
    wrong = []
    for path in sorted(pathlib.Path("ingestion/sources").glob("*.py")):
        if path.name in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        if "User-Agent" in text and "politeness.USER_AGENT" not in text:
            wrong.append(path.name)
    assert not wrong, f"{wrong} define a User-Agent without using politeness.USER_AGENT"
