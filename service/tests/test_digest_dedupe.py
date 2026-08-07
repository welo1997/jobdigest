"""One job must reach the inbox once, even when it exists under several posting_ids.

`digest_sends` keys on posting_id, so it is blind to the two ways a single opening turns
into two rows — a cross-source duplicate (jobs.cz and cocuma carrying the same ad) and a
relist (jobs.cz re-posting an expiring ad under a fresh id). Measured 2026-07-28: 3
occurrences in 77 sends.

Two properties are worth more than the dedupe itself and are tested hardest here:

  * it happens BEFORE the limit, so a duplicate never costs the subscriber a slot;
  * it never collapses two genuinely different jobs — a missing company or title yields
    an empty key, which must be treated as unique rather than as "matches everything".

DB-free: build_digest's three reads are monkeypatched.
"""
from __future__ import annotations

import pytest

from service import digest


def _job(pid: str, score: int, company: str = "Acme", title: str = "Data Analyst",
         city: str | None = None) -> dict:
    return {"posting_id": pid, "score": score, "title": title, "company": company,
            "city": city,
            "url": "https://example.com/j", "summary": "s", "role_category": "data_analytics"}


@pytest.fixture
def patched(monkeypatch):
    state: dict = {"picks": [], "already": set(), "sent_keys": []}

    def matched_jobs(pid, limit=50, offset=0, hidden=False, exclude_sent=False):
        """Mirrors the real signature *and* what `exclude_sent` does in SQL — see the same
        fake in test_digest_fallback.py for why it honours the flag rather than ignoring it."""
        picks = list(state["picks"])
        if exclude_sent:
            picks = [j for j in picks if j["posting_id"] not in state["already"]]
        return picks[:limit]

    monkeypatch.setattr(digest.store, "matched_jobs", matched_jobs)
    monkeypatch.setattr(digest.store, "already_sent_ids", lambda pid: set(state["already"]))
    monkeypatch.setattr(digest.store, "sent_job_keys",
                        lambda pid, days=90: list(state["sent_keys"]))
    return state


PROFILE = {"id": "p1", "email": "u@example.com", "manage_token": "t"}


# ----------------------------------------------------------------- the key ----

def test_key_folds_diacritics_case_and_legal_form():
    # The cross-source case: two boards, two spellings, one job.
    assert digest.dedupe_key("Qminers s.r.o.", "Datový analytik") == \
           digest.dedupe_key("QMINERS", "Datovy Analytik")


def test_key_ignores_bracketed_trailers():
    assert digest.dedupe_key("Acme", "Data Analyst (m/f/d)") == \
           digest.dedupe_key("Acme", "Data Analyst")


def test_key_does_not_merge_different_companies_or_titles():
    assert digest.dedupe_key("Acme", "Data Analyst") != digest.dedupe_key("Globex", "Data Analyst")
    assert digest.dedupe_key("Acme", "Data Analyst") != digest.dedupe_key("Acme", "Data Engineer")


def test_a_trailing_legal_form_is_stripped():
    assert digest.dedupe_key("Acme s.r.o.", "Analyst") == digest.dedupe_key("Acme", "Analyst")
    assert digest.dedupe_key("Acme GmbH", "Analyst") == digest.dedupe_key("Acme", "Analyst")


def test_the_normal_form_is_pinned():
    # Pinned rather than compared, because the interesting property — a legal-form word
    # mid-name survives — cannot be shown by an inequality: unanchored stripping also eats
    # the preceding space, so "Nord SE Consulting" becomes "nordconsulting", which still
    # differs from "nord consulting". Only the exact form catches it.
    assert digest.dedupe_key("Nord SE Consulting", "Analyst") == "nord se consulting|analyst"
    assert digest.dedupe_key("Acme s.r.o.", "Data Analyst (m/f/d)") == "acme|data analyst"


def test_missing_company_or_title_yields_an_empty_key():
    assert digest.dedupe_key(None, "Data Analyst") == ""
    assert digest.dedupe_key("Acme", "") == ""
    assert digest.dedupe_key("!!!", "Data Analyst") == ""      # nothing survives normalising


# -------------------------------------------------------------- in a digest ---

def test_same_job_under_two_ids_is_emailed_once(patched):
    patched["picks"] = [_job("a", 9), _job("b", 8), _job("c", 7, title="Data Engineer")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "c"]       # "b" is the same job as "a"


def test_the_higher_scoring_copy_is_the_one_kept(patched):
    patched["picks"] = [_job("a", 9), _job("b", 6)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a"]
    assert jobs[0]["score"] == 9


def test_dedupe_runs_before_the_limit_so_a_duplicate_costs_no_slot(patched):
    # Without dedupe-before-limit the email would be [dup, dup, x, y, z] and the
    # subscriber would receive four distinct jobs where five were available.
    patched["picks"] = [
        _job("dup1", 9), _job("dup2", 9),
        _job("v", 8, title="V"), _job("w", 8, title="W"),
        _job("x", 8, title="X"), _job("y", 8, title="Y"), _job("z", 8, title="Z"),
    ]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert len(jobs) == 5
    assert len({j["title"] for j in jobs}) == 5               # five *different* jobs
    assert [j["posting_id"] for j in jobs] == ["dup1", "v", "w", "x", "y"]


def test_a_relisted_job_is_not_emailed_again_days_later(patched):
    # The original was emailed and has since expired; jobs.cz re-posted it under a new id,
    # so `already_sent_ids` (keyed on posting_id) cannot see it. This is the real SOFTEC
    # case from production: same company, same title, both prague, 20 and 22 Jul.
    patched["sent_keys"] = [("Acme", "Data Analyst", "prague")]
    patched["picks"] = [_job("new-id", 9, city="prague"),
                        _job("other", 8, title="Data Engineer", city="prague")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["other"]


def test_history_matches_across_spelling_too(patched):
    patched["sent_keys"] = [("Qminers s.r.o.", "Datový analytik", "prague")]
    patched["picks"] = [_job("relist", 9, company="QMINERS", title="Datovy Analytik",
                             city="prague")]
    assert digest.build_digest(PROFILE, limit=5) == []


def test_history_does_not_suppress_a_different_role_at_the_same_company(patched):
    patched["sent_keys"] = [("Acme", "Data Analyst", "prague")]
    patched["picks"] = [_job("keep", 9, title="Data Engineer", city="prague")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["keep"]


# ------------------------------------------------------------------- city ----

def test_the_same_role_in_two_known_cities_is_two_jobs(patched):
    # The ČSOB/Biedronka shape: one role advertised across many towns. 1 438 active
    # postings sit in such groups, so collapsing them would delete real openings.
    patched["picks"] = [_job("a", 9, city="prague"), _job("b", 8, city="brno")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "b"]


def test_the_same_role_in_the_same_city_is_one_job(patched):
    patched["picks"] = [_job("a", 9, city="prague"), _job("b", 8, city="prague")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a"]


def test_an_unknown_city_matches_a_known_one(patched):
    # The real Publicis Groupe case: emailed with no location on 26 Jul, re-posted with
    # "Praha – Libeň" on 27 Jul. A strict city component would have missed this.
    patched["sent_keys"] = [("Acme", "Data Analyst", None)]
    patched["picks"] = [_job("relist", 9, city="prague")]
    assert digest.build_digest(PROFILE, limit=5) == []


def test_the_unknown_city_wildcard_works_in_both_directions(patched):
    # Known first, unknown second — the wildcard must not depend on arrival order.
    patched["sent_keys"] = [("Acme", "Data Analyst", "prague")]
    patched["picks"] = [_job("relist", 9, city=None)]
    assert digest.build_digest(PROFILE, limit=5) == []


def test_rows_with_no_usable_key_are_never_collapsed(patched):
    # Two different jobs, both missing a company. An empty key must mean "unique", not
    # "matches everything" — the latter would silently delete one real job per digest.
    patched["picks"] = [_job("a", 9, company=""), _job("b", 8, company="")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "b"]


def test_dedupe_also_applies_on_a_quiet_day(patched):
    # The weak-fallback path slices too, so it needs the same protection.
    patched["picks"] = [_job("a", 5), _job("b", 5), _job("c", 4, title="Other")]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "c"]
    assert digest._is_weak(jobs)
