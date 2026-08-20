"""The pure prober must fail on the link bugs that actually shipped — and only ever call a page
`CLOSED` when the page itself says so.

`ingestion/link_probe.py` (extracted from `scripts/check_links.py` on 2026-08-20) is a guard, so
it is worth exactly as much as its ability to go red. Its reference cases are real:

* **mpsv** — a client-rendered app that answers 200 with an identical shell for any id, so the
  prober must refuse to call that a pass.
* **startupjobs / cocuma** — a page that positively says the role is gone. This is the ONE verdict
  `service/liveness.py` acts on, so the CLOSED tests below are load-bearing twice over: too loose
  and it deletes live jobs, too strict and the dead-link sweep does nothing.
* the **board's index page** — carries the employer's name everywhere, so an employer-name match is
  not evidence and must not be reported as OK.

Everything here is offline — no network, no live sites.
"""

from __future__ import annotations

import pytest

from ingestion import link_probe
from ingestion.link_probe import evidence, is_closed, probe, visible_text


class _Resp:
    headers: dict[str, str] = {}

    def __init__(self, status: int, text: str, url: str | None = None):
        self.status_code = status
        self.text = text
        #: None means "no redirect". `requests` sets `.url` to the *final* URL, so a fixture
        #: that hardcoded one made every probe look redirected — and a redirect now changes
        #: the verdict, so the fixture has to model that faithfully.
        self.url = url


def _page(body: str) -> str:
    return f"<html><head><title>x</title></head><body>{body}</body></html>"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(link_probe.politeness, "throttle", lambda *_: None)
    monkeypatch.setattr(link_probe.politeness, "robots_allows", lambda *_: True)


def _with_url(resp: _Resp, url: str) -> _Resp:
    if resp.url is None:                    # no redirect: final URL == requested URL
        resp.url = url
    return resp


def _serve(monkeypatch, resp: _Resp) -> None:
    monkeypatch.setattr(link_probe.requests, "get",
                        lambda url, *a, **k: _with_url(resp, url))


# --- the bugs that shipped -----------------------------------------------------------

def test_a_client_rendered_shell_is_never_a_pass(monkeypatch):
    """The MPSV case. `up.gov.cz` answered 200 for every id, real or invented, with a page
    holding nothing but a breadcrumb — and the link was dead for five days across ~7 300
    postings. Anything that reports this green reproduces that bug exactly."""
    _serve(monkeypatch, _Resp(200, _page("<div id='app'></div>Volná místa - hledání")))
    verdict, note = probe("https://up.gov.cz/volna-mista-v-cr?id=67251104",
                          "Datový analytik", "Firma a.s.")
    assert verdict == "SHELL"
    assert "browser" in note


def test_bot_protection_is_not_a_broken_link(monkeypatch):
    """Himalayas answers 403 to the honest agent *and* to a spoofed Chrome string, and the
    posting opens normally in a real browser (checked by hand 2026-08-08). Reporting that as
    DEAD would fail the run every day for a source that works."""
    _serve(monkeypatch, _Resp(403, _page("Just a moment... Performing security verification")))
    verdict, note = probe("https://himalayas.app/companies/x/jobs/y", "Data Engineer", "Acme")
    assert verdict == "BLOCKED"
    assert "browser" in note


def test_a_refusal_after_a_redirect_blames_the_destination_not_our_link(monkeypatch):
    """`jobs.livestorm.co` — a Recruitee board on a custom domain — forwards to
    welcometothejungle.com, which 403s every bot and serves the right job to a browser. Our
    link resolved and forwarded, which is all it is responsible for."""
    _serve(monkeypatch, _Resp(403, _page("Forbidden"),
                              url="https://www.welcometothejungle.com/en/companies/x/jobs/y"))
    verdict, note = probe("https://jobs.livestorm.co/o/account-executive-enterprise-2",
                          "Account Executive Enterprise", "Livestorm")
    assert verdict == "BLOCKED"
    assert "welcometothejungle.com" in note and "redirect" in note


def test_a_404_is_reported_but_not_called_dead_on_its_own(monkeypatch):
    """A 404 from an employer's career domain is not proof of a broken link: `form3` answers
    404 to every HTTP client and renders the job in a browser, `roblox` times out and renders
    fine, and `lever:aircall` answers 404 and is genuinely gone. The verdict says "look at
    this" rather than guessing — this is exactly why `service/liveness.py` does NOT act on a
    bare 404, only on a CLOSED marker."""
    _serve(monkeypatch, _Resp(404, _page("Something went wrong somewhere on our servers")))
    verdict, note = probe("https://careers.form3.tech/job/104109", "Backend Engineer", "Form3")
    assert verdict == "UNCONFIRMED"
    assert "404" in note and "board root" in note


def test_a_404_that_says_the_role_is_gone_is_closed(monkeypatch):
    """cocuma.cz answers 404 with real server-rendered Czech text on a taken opportunity. That
    is a confident CLOSED even though the status is 404 — the marker, not the code, decides,
    which is the whole reason the sweep can act on it safely."""
    _serve(monkeypatch, _Resp(404, _page(
        "Příležitost nenalezena (404) Ups! Příležitost již vychytal někdo před vámi.")))
    verdict, _ = probe("https://www.cocuma.cz/job/e-commerce-data-analyst/10295/",
                       "E-Commerce Data Analyst", "VAFO")
    assert verdict == "CLOSED"


def test_a_timeout_is_unreachable_not_dead(monkeypatch):
    """`careers.roblox.com` times out for every HTTP client and serves 222 postings fine to a
    browser. Dead means answered and empty, unreachable means no answer."""
    def boom(*a, **k):
        raise link_probe.requests.ConnectTimeout("timed out")
    monkeypatch.setattr(link_probe.requests, "get", boom)
    verdict, note = probe("https://careers.roblox.com/jobs/7350081", "ML Engineer", "Roblox")
    assert verdict == "UNREACHABLE"
    assert "browser" in note


# --- CLOSED: the verdict the liveness sweep acts on ----------------------------------

def test_a_filled_role_is_closed_not_broken(monkeypatch):
    """An expired posting is the site's news, not our bug — it must not read as a bad link."""
    _serve(monkeypatch, _Resp(200, _page("Datový analytik. " * 40 +
                                         "This position is no longer accepting applications.")))
    assert probe("https://example.test/job/1", "Datový analytik", "Alza")[0] == "CLOSED"


def test_startupjobs_expired_offer_is_closed(monkeypatch):
    """startupjobs.com serves a 200 for an expired offer whose body says "The offer is no
    longer valid" — the case measured live on 2026-08-20. Without this marker the sweep would
    keep emailing it."""
    _serve(monkeypatch, _Resp(200, _page(
        "Data Engineer - Snowflake/dbt. This offer isn't -to-date. The offer is no longer valid.")))
    assert probe("https://www.startupjobs.com/job/105209/data-engineer-snowflake-dbt",
                 "Data Engineer - Snowflake/dbt", "Vivantis")[0] == "CLOSED"


def test_is_closed_catches_the_real_dead_pages_and_spares_a_live_one():
    assert is_closed("...The offer is no longer valid...")
    assert is_closed("Příležitost nenalezena (404)")
    assert is_closed("This position has been filled")
    # A live posting's prose must never trip a closed marker — a false positive deletes a real job.
    assert not is_closed(
        "Senior Data Engineer at Collibra. Join us building the data intelligence cloud. Apply now.")


def test_the_action_set_is_closed_only():
    """The safety contract in one assertion: the sweep acts on exactly one verdict. Anything
    added here (a bare 404, a SHELL) must be a deliberate change that breaks this first."""
    assert link_probe.DEACTIVATE_VERDICTS == frozenset({"CLOSED"})


# --- the index-page trap -------------------------------------------------------------

def test_the_boards_index_page_is_not_evidence_that_the_posting_resolved():
    index = "Spotify Jobs Spotify careers " + "Open roles at Spotify. " * 40
    verdict, note = evidence(index, "Machine Learning Scientist II", "Spotify")
    assert verdict == "WEAK"
    assert "index" in note


def test_the_posting_page_is_a_pass_because_it_carries_the_title():
    page = "Spotify " + "Machine Learning Scientist II — Stockholm. " * 20
    verdict, _ = evidence(page, "Machine Learning Scientist II", "Spotify")
    assert verdict == "OK"


def test_a_wrong_but_wordy_page_is_a_mismatch_not_a_shell(monkeypatch):
    _serve(monkeypatch, _Resp(200, _page("Welcome to our corporate homepage. " * 40)))
    assert probe("https://example.test/job/1", "Datový analytik", "Alza.cz a.s.")[0] == "MISMATCH"


# --- matching details that have bitten this repo before ------------------------------

def test_diacritics_do_not_decide_the_verdict():
    assert evidence("Vyvojar softwaru v Praze " * 10, "Vývojář softwaru", None)[0] == "OK"
    assert evidence("Vývojář softwaru v Praze " * 10, "Vyvojar softwaru", None)[0] == "OK"


def test_script_bodies_are_not_counted_as_rendered_text():
    html = _page("<script>var x = 'Datový analytik';" + "var pad = 1;" * 400 + "</script>Hi")
    text = visible_text(html)
    assert "Datový analytik" not in text
    assert len(text) < link_probe.SHELL_TEXT_CHARS


def test_a_rate_limit_is_waited_out_and_never_reported_as_a_broken_link(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_probe.time, "sleep", slept.append)
    seq = [_Resp(429, "slow down"), _Resp(200, _page("Data Engineer at Acme. " * 30))]
    monkeypatch.setattr(link_probe.requests, "get",
                        lambda url, *a, **k: _with_url(seq.pop(0), url))

    assert probe("https://example.test/job/1", "Data Engineer", "Acme")[0] == "OK"
    assert slept, "a 429 must be waited out, not retried immediately"


def test_a_rate_limit_that_survives_the_backoff_is_still_not_dead(monkeypatch):
    monkeypatch.setattr(link_probe.time, "sleep", lambda *_: None)
    monkeypatch.setattr(link_probe.requests, "get", lambda *a, **k: _Resp(429, "slow down"))
    verdict, note = probe("https://example.test/job/1", "Data Engineer", "Acme")
    assert verdict == "THROTTLED"
    assert "our own probing rate" in note


def test_retry_after_is_honoured_but_capped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(link_probe.time, "sleep", slept.append)

    class _R(_Resp):
        headers = {"Retry-After": "3600"}

    monkeypatch.setattr(link_probe.requests, "get", lambda *a, **k: _R(429, "slow down"))
    probe("https://example.test/job/1", "x", None)
    assert slept == [30.0]


def test_a_page_that_is_mostly_script_is_a_shell_however_much_chrome_it_renders(monkeypatch):
    chrome = "Ny sökning Spara annons Om jobbet Arbetsgivare Dela annonsen Skriv ut. " * 25
    html = _page(chrome + "<script>" + "var pad = 1;" * 9000 + "</script>")
    _serve(monkeypatch, _Resp(200, html))

    assert len(visible_text(html)) > link_probe.SHELL_TEXT_CHARS, "the length floor is cleared"
    verdict, note = probe("https://example.test/job/1", "Ekonomiassistent Göteborg", "Digental")
    assert verdict == "SHELL"
    assert "%" in note, "the note must show the ratio that decided it"


def test_a_title_with_no_distinctive_tokens_still_matches_verbatim():
    for title in ("PHP Engineer", "QA Engineer"):
        page = f"{title}\nFull-time\nCompany Description. " + "Body text. " * 40
        assert evidence(page, title, "Playtech")[0] == "OK", title


def test_the_exact_title_rule_needs_more_than_a_fragment():
    assert evidence("we do qa here " * 30, "QA", None)[0] != "OK"


def test_one_word_of_a_two_word_title_is_not_evidence():
    chrome = "Ekonomi Bygg Säljare Hantverk Vård Utbildning Sök jobb Spara annons. " * 20
    assert "ekonomi" in chrome.casefold() and "göteborg" not in chrome.casefold()
    assert evidence(chrome, "Ekonomi Göteborg", None)[0] != "OK"
    assert evidence(chrome, "Ekonomi Säljare", None)[0] == "OK", (
        "both words present is still a pass — the rule is two words, not a longer title")
    assert evidence(chrome, "Ekonomi", None)[0] == "OK", (
        "a one-word title can only ever offer one word; requiring two would fail every "
        "single-token posting on every source")


def test_a_non_http_url_is_dead_before_any_request(monkeypatch):
    monkeypatch.setattr(link_probe.requests, "get",
                        lambda *a, **k: pytest.fail("must not fetch a javascript: url"))
    assert probe("javascript:alert(1)", "x", None)[0] == "DEAD"
