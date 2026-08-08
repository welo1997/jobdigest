"""The link checker must fail on the two link bugs that actually shipped.

`scripts/check_links.py` is a guard, so it is worth exactly as much as its ability to go red.
Its two reference cases are real:

* **mpsv** — a client-rendered app that answers 200 with an identical shell for any id, so
  the checker must refuse to call that a pass.
* **startupjobs** — a clean 404 on every posting, which is the easy half.

The third case is the one this file exists to pin: a link that resolves to the *board's index
page* instead of the posting. That page carries the employer's name everywhere, so an
employer-name match is not evidence and must not be reported as OK.

Everything here is offline — no network, no live sites.
"""

from __future__ import annotations

import pytest

from scripts import check_links
from scripts.check_links import Spread, Trim, evidence, probe, visible_text


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
    monkeypatch.setattr(check_links.politeness, "throttle", lambda *_: None)
    monkeypatch.setattr(check_links.politeness, "robots_allows", lambda *_: True)


def _with_url(resp: _Resp, url: str) -> _Resp:
    if resp.url is None:                    # no redirect: final URL == requested URL
        resp.url = url
    return resp


def _serve(monkeypatch, resp: _Resp) -> None:
    monkeypatch.setattr(check_links.requests, "get",
                        lambda url, *a, **k: _with_url(resp, url))


# --- the two bugs that shipped -------------------------------------------------------

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
    DEAD would fail the run every day for a source that works, which is how a red check stops
    being read — the same reason CLOSED and THROTTLED are separate verdicts."""
    _serve(monkeypatch, _Resp(403, _page("Just a moment... Performing security verification")))
    verdict, note = probe("https://himalayas.app/companies/x/jobs/y", "Data Engineer", "Acme")
    assert verdict == "BLOCKED"
    assert "browser" in note


def test_a_refusal_after_a_redirect_blames_the_destination_not_our_link(monkeypatch):
    """`jobs.livestorm.co` — a Recruitee board on a custom domain — forwards to
    welcometothejungle.com, which 403s every bot and serves the right job to a browser.
    Our link resolved and forwarded, which is all it is responsible for; reporting DEAD
    would blame the adapter for a third party's bot policy."""
    _serve(monkeypatch, _Resp(403, _page("Forbidden"),
                              url="https://www.welcometothejungle.com/en/companies/x/jobs/y"))
    verdict, note = probe("https://jobs.livestorm.co/o/account-executive-enterprise-2",
                          "Account Executive Enterprise", "Livestorm")
    assert verdict == "BLOCKED"
    assert "welcometothejungle.com" in note and "redirect" in note


def test_a_404_is_dead(monkeypatch):
    """The startupjobs case: `/job/{id}` without the slug, on every posting, for a day."""
    _serve(monkeypatch, _Resp(404, _page("Page not found")))
    assert probe("https://www.startupjobs.com/job/104109", "Backend vývojář", "Shoptet")[0] == "DEAD"


# --- the case that is easy to get wrong ----------------------------------------------

def test_the_boards_index_page_is_not_evidence_that_the_posting_resolved():
    """A link that lands on `jobs.lever.co/spotify` instead of the requested posting shows
    the employer's name on every row. Counting that as proof would pass the exact failure
    this checker is for, so employer-only is WEAK and reported for a human."""
    index = "Spotify Jobs Spotify careers " + "Open roles at Spotify. " * 40
    verdict, note = evidence(index, "Machine Learning Scientist II", "Spotify")
    assert verdict == "WEAK"
    assert "index" in note


def test_the_posting_page_is_a_pass_because_it_carries_the_title():
    page = "Spotify " + "Machine Learning Scientist II — Stockholm. " * 20
    verdict, _ = evidence(page, "Machine Learning Scientist II", "Spotify")
    assert verdict == "OK"


def test_a_wrong_but_wordy_page_is_a_mismatch_not_a_shell(monkeypatch):
    """Server-rendered and substantial, but somebody else's content. Distinguishing this
    from SHELL matters: one needs an adapter fix, the other needs a human with a browser."""
    _serve(monkeypatch, _Resp(200, _page("Welcome to our corporate homepage. " * 40)))
    assert probe("https://example.test/job/1", "Datový analytik", "Alza.cz a.s.")[0] == "MISMATCH"


def test_a_filled_role_is_closed_not_broken(monkeypatch):
    """An expired posting is the site's news, not our bug — it must not read as a bad link."""
    _serve(monkeypatch, _Resp(200, _page("Datový analytik. " * 40 +
                                         "This position is no longer accepting applications.")))
    assert probe("https://example.test/job/1", "Datový analytik", "Alza")[0] == "CLOSED"


# --- matching details that have bitten this repo before ------------------------------

def test_diacritics_do_not_decide_the_verdict():
    """Czech and Swedish titles must match a page that spells them either way — otherwise
    the checker would report the entire mpsv and platsbanken inventory as broken."""
    assert evidence("Vyvojar softwaru v Praze " * 10, "Vývojář softwaru", None)[0] == "OK"
    assert evidence("Vývojář softwaru v Praze " * 10, "Vyvojar softwaru", None)[0] == "OK"


def test_script_bodies_are_not_counted_as_rendered_text():
    """A SPA ships hundreds of KB of JavaScript. Counting that as page text would push every
    client-rendered shell over the threshold and turn SHELL into a silent pass."""
    html = _page("<script>var x = 'Datový analytik';" + "var pad = 1;" * 400 + "</script>Hi")
    text = visible_text(html)
    assert "Datový analytik" not in text
    assert len(text) < check_links.SHELL_TEXT_CHARS


def test_a_rate_limit_is_waited_out_and_never_reported_as_a_broken_link(monkeypatch):
    """Found on the first wide run: Working Nomads answered 429 to the fourth of six
    requests and the link was reported DEAD. It was not — we were.

    A checker that manufactures failures as you widen it is worse than none: the run goes
    red, the red is wrong, and the next person stops reading it. Same reason BLOCKED exists.
    """
    slept: list[float] = []
    monkeypatch.setattr(check_links.time, "sleep", slept.append)
    seq = [_Resp(429, "slow down"), _Resp(200, _page("Data Engineer at Acme. " * 30))]
    monkeypatch.setattr(check_links.requests, "get",
                        lambda url, *a, **k: _with_url(seq.pop(0), url))

    assert probe("https://example.test/job/1", "Data Engineer", "Acme")[0] == "OK"
    assert slept, "a 429 must be waited out, not retried immediately"


def test_a_rate_limit_that_survives_the_backoff_is_still_not_dead(monkeypatch):
    monkeypatch.setattr(check_links.time, "sleep", lambda *_: None)
    monkeypatch.setattr(check_links.requests, "get", lambda *a, **k: _Resp(429, "slow down"))
    verdict, note = probe("https://example.test/job/1", "Data Engineer", "Acme")
    assert verdict == "THROTTLED"
    assert "our own probing rate" in note


def test_retry_after_is_honoured_but_capped(monkeypatch):
    """A board asking for an hour must not hang the run; a board asking for 3 s should get 3."""
    slept: list[float] = []
    monkeypatch.setattr(check_links.time, "sleep", slept.append)

    class _R(_Resp):
        headers = {"Retry-After": "3600"}

    monkeypatch.setattr(check_links.requests, "get", lambda *a, **k: _R(429, "slow down"))
    probe("https://example.test/job/1", "x", None)
    assert slept == [30.0]


def test_a_page_that_is_mostly_script_is_a_shell_however_much_chrome_it_renders(monkeypatch):
    """Platsbanken answers 124 KB of HTML holding 1 619 characters of navigation chrome —
    over three times the character floor, so a length test alone called it MISMATCH and would
    have had someone hunting a bug in a working adapter. The ratio is what settles it."""
    chrome = "Ny sökning Spara annons Om jobbet Arbetsgivare Dela annonsen Skriv ut. " * 25
    html = _page(chrome + "<script>" + "var pad = 1;" * 9000 + "</script>")
    _serve(monkeypatch, _Resp(200, html))

    assert len(visible_text(html)) > check_links.SHELL_TEXT_CHARS, "the length floor is cleared"
    verdict, note = probe("https://example.test/job/1", "Ekonomiassistent Göteborg", "Digental")
    assert verdict == "SHELL"
    assert "%" in note, "the note must show the ratio that decided it"


def test_a_title_with_no_distinctive_tokens_still_matches_verbatim():
    """"PHP Engineer" and "QA Engineer" reduce to *no* usable tokens — `php` and `qa` are
    under the length floor and `engineer` is a stopword — so both were reported WEAK against
    pages that render the title in their first line. Two false alarms in one run, on two
    different sources (recruitee:mailerlite, smartrecruiters:Playtech), each costing a
    browser check to clear. The exact title is the strongest evidence available and is now
    tried first."""
    for title in ("PHP Engineer", "QA Engineer"):
        page = f"{title}\nFull-time\nCompany Description. " + "Body text. " * 40
        assert evidence(page, title, "Playtech")[0] == "OK", title


def test_the_exact_title_rule_needs_more_than_a_fragment():
    """A two-character title would match almost any page. The floor keeps the strongest rule
    from also being the loosest."""
    assert evidence("we do qa here " * 30, "QA", None)[0] != "OK"


def test_one_word_of_a_two_word_title_is_not_evidence():
    """Two of platsbanken's three links passed on a *single* title word matched against its
    own navigation menu — a coin toss reported as a working link, on the very source whose
    pages carry no ad text at all."""
    chrome = "Ekonomi Bygg Säljare Hantverk Vård Utbildning Sök jobb Spara annons. " * 20
    # Exactly one of the two title words is on the page — the boundary the rule exists for.
    assert "ekonomi" in chrome.casefold() and "göteborg" not in chrome.casefold()
    assert evidence(chrome, "Ekonomi Göteborg", None)[0] != "OK"
    assert evidence(chrome, "Ekonomi Säljare", None)[0] == "OK", (
        "both words present is still a pass — the rule is two words, not a longer title")
    assert evidence(chrome, "Ekonomi", None)[0] == "OK", (
        "a one-word title can only ever offer one word; requiring two would fail every "
        "single-token posting on every source")


def test_a_non_http_url_is_dead_before_any_request(monkeypatch):
    """`safe_url` renders these as '#' in the email, so they are broken links by definition."""
    monkeypatch.setattr(check_links.requests, "get",
                        lambda *a, **k: pytest.fail("must not fetch a javascript: url"))
    assert probe("javascript:alert(1)", "x", None)[0] == "DEAD"


# --- the sampling table is a copy, so it is drift-checked ----------------------------

def test_self_check_passes_against_the_current_adapters():
    """`SAMPLING` names attributes that live in the adapters. A rename does not raise — it
    silently stops bounding that source, and the next run takes 40 minutes. Same lesson as
    `pending_boards.py` grepping source text and the CI skip-check listing filenames."""
    assert check_links.self_check() == 0


def test_trim_refuses_a_type_it_cannot_truncate():
    with pytest.raises(TypeError):
        Trim(1).of(7, "somesource.MAX_PAGES")
    assert Trim(2).of([1, 2, 3], "x") == [1, 2]
    assert Trim(1).of({"a": 1, "b": 2}, "x") == {"a": 1}


def test_board_lists_are_sampled_across_the_list_not_off_the_head():
    """The head of a board list is the worst possible sample. Greenhouse carries 149 tokens
    and the first is Stripe, which serves postings from `stripe.com/careers` rather than
    `job-boards.greenhouse.io` — so a head sample exercised one URL shape and called the
    whole source green. Even spacing walks the list as it was actually built."""
    boards = [f"board-{i}" for i in range(149)]
    picked = Spread(4).of(boards, "greenhouse")
    assert picked[0] == "board-0" and picked[-1] == "board-148", "both ends must be covered"
    assert len(set(picked)) == 4
    assert picked == Spread(4).of(boards, "greenhouse"), (
        "sampling must be deterministic, or a regression is not attributable to a board")


def test_links_are_picked_across_employers_not_off_the_head_of_the_list():
    """Widening the board list is not enough on its own. With eight Greenhouse boards fetched,
    all six probed links were still Stripe's, because `normalize` concatenates board by board
    and Stripe alone carries hundreds of postings — eight boards paid for, one tested. Once
    fixed, the same run exercised five distinct Greenhouse URL shapes (`stripe.com`,
    `boards.greenhouse.io`, `job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io`,
    `careers.toasttab.com`), which is the variation this source actually has."""
    class _P:
        def __init__(self, company, url):
            self.company, self.url = company, url

    postings = ([_P("Stripe", f"https://stripe.com/jobs/{i}") for i in range(50)]
                + [_P("Wolt", "https://job-boards.greenhouse.io/wolt/jobs/1")]
                + [_P("Raisin", "https://job-boards.eu.greenhouse.io/raisin/jobs/2")])
    picked = check_links._pick(postings, 3)
    assert {p.company for p in picked} == {"Stripe", "Wolt", "Raisin"}


def test_the_same_url_is_never_probed_twice():
    """Workable listed one job twice in a single run; re-testing a link already checked
    spends a probe and reports the same verdict twice as if it were two links."""
    class _P:
        def __init__(self, company, url):
            self.company, self.url = company, url

    dupes = [_P("Acme", "https://apply.workable.com/j/EE18833873")] * 4
    assert len(check_links._pick(dupes, 4)) == 1


def test_spread_never_invents_or_drops_when_the_list_is_short():
    assert Spread(8).of(["a", "b", "c"], "x") == ["a", "b", "c"]


def test_every_board_list_is_spread_and_every_query_dimension_is_not():
    """`--boards N` widens `Spread` entries only. Widening a search-term or tag list
    multiplies requests without exercising any new URL construction, which is the thing
    under test — Workday's 9 terms against 1 site cost 9× for the same URL shape."""
    board_attrs = {"ORGS", "SITES", "TENANTS", "COMPANIES", "ACCOUNTS", "_board_tokens"}
    query_attrs = {"TAGS", "QUERIES", "SEARCH_TERMS", "LOCATIONS", "OCCUPATION_FIELDS",
                   "_categories"}
    for name, sample in check_links.SAMPLING.items():
        for attr, marker in {**sample.module, **sample.instance}.items():
            if attr in board_attrs:
                assert isinstance(marker, Spread), f"{name}.{attr} is a board list, use Spread"
            if attr in query_attrs:
                assert not isinstance(marker, Spread), (
                    f"{name}.{attr} is a query dimension — --boards must not widen it")
