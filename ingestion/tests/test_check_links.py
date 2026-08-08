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
from scripts.check_links import Trim, evidence, probe, visible_text


class _Resp:
    def __init__(self, status: int, text: str, url: str = "https://example.test/job/1"):
        self.status_code = status
        self.text = text
        self.url = url


def _page(body: str) -> str:
    return f"<html><head><title>x</title></head><body>{body}</body></html>"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(check_links.politeness, "throttle", lambda *_: None)
    monkeypatch.setattr(check_links.politeness, "robots_allows", lambda *_: True)


def _serve(monkeypatch, resp: _Resp) -> None:
    monkeypatch.setattr(check_links.requests, "get", lambda *a, **k: resp)


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
