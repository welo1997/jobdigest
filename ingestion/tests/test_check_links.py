"""The link checker's sampling table is a copy of things that live in the adapters, so it is
drift-checked here.

The per-URL verdict logic that used to live in `scripts/check_links.py` moved to
`ingestion/link_probe.py` on 2026-08-20 (so `service/liveness.py` can share it); its tests moved
to `ingestion/tests/test_link_probe.py`. What stays in `check_links.py`, and here, is the
sampling/reporting harness: which boards each source is bounded to, and how a run picks the handful
of links it probes. That harness is a copy, and this repo has been bitten by exactly that before
(`pending_boards.py` grepping source text, the CI skip-check listing filenames), so `self_check`
and the `Spread`/`Trim`/`_pick` tests below exist to make a drift go red.

Everything here is offline — no network, no live sites.
"""

from __future__ import annotations

import pytest

from scripts import check_links
from scripts.check_links import Spread, Trim


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
    and Stripe alone carries hundreds of postings — eight boards paid for, one tested."""
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
