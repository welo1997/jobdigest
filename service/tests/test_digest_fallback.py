"""Quiet-day fallback: a subscriber still gets a digest when no match clears EMAIL_MIN_SCORE.

The rule (added 2026-07-23): if there are strong (>= EMAIL_MIN_SCORE) unsent matches, the
email is those and nothing changes. If there are none, the email carries the best *weaker*
(4-5) unsent matches instead of skipping — but it is labelled honestly as a no-strong-match
day. Only a genuinely empty pick list skips the send.

These tests are DB-free: build_digest's three DB reads are monkeypatched. Each assertion is
paired with the behaviour it guards, so breaking the fallback turns one of them red.
"""
from __future__ import annotations

import pytest

from service import digest, matcher, store


def _job(pid: str, score: int, **extra) -> dict:
    # Titles are per-posting so these fallback tests exercise the fallback only. A shared
    # title would now collapse under the (company, title) dedupe and every count here would
    # silently be measuring that instead — see test_digest_dedupe.py.
    j = {"posting_id": pid, "score": score, "title": f"Role {pid}", "company": "Co",
         "url": "https://example.com/j", "summary": "s", "role_category": "data_engineering"}
    j.update(extra)
    return j


@pytest.fixture
def patched(monkeypatch):
    """Let a test declare the matcher's picks and what was already sent, no DB."""
    state: dict = {"picks": [], "already": set(), "sent_keys": []}

    def matched_jobs(pid, limit=50, offset=0, hidden=False, exclude_sent=False,
                     holdable_from=None):
        """Mirrors the real signature *and* what `exclude_sent` / `holdable_from` do in SQL.

        A fake that merely tolerated the kwarg would keep passing while the real query
        stopped filtering — the fake would be testing itself. See test_digest_window_sql.py
        for the behaviour executed against a real Postgres."""
        picks = list(state["picks"])
        if exclude_sent:
            picks = [j for j in picks if j["posting_id"] not in state["already"]]
        if holdable_from:
            # Mirrored through `matcher._reach_for_model` rather than reimplemented: it and
            # `geo.reach_predicate` are one rule in two languages, and a third copy here would be
            # a third thing to drift.
            countries = {c.upper() for c in holdable_from}
            picks = [j for j in picks
                     if matcher._reach_for_model(j, countries) != "reach=other-country-only"]
        return picks[:limit]

    monkeypatch.setattr(digest.store, "matched_jobs", matched_jobs)
    monkeypatch.setattr(digest.store, "already_sent_ids", lambda pid: set(state["already"]))
    monkeypatch.setattr(digest.store, "sent_job_keys",
                        lambda pid, days=90: list(state["sent_keys"]))
    return state


PROFILE = {"id": "p1", "email": "u@example.com", "manage_token": "t"}


def test_strong_day_returns_only_strong_matches(patched):
    patched["picks"] = [_job("a", 9), _job("b", 7), _job("c", 5), _job("d", 4)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "b"]      # 5 and 4 are excluded
    assert not digest._is_weak(PROFILE, jobs)                 # and it's not a quiet-day digest


def test_quiet_day_falls_back_to_weaker_matches_instead_of_skipping(patched):
    # No match >= 6 — the old behaviour returned [] and the send was skipped.
    patched["picks"] = [_job("c", 5), _job("d", 4)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["c", "d"]      # weaker matches DO go out now
    assert jobs, "a quiet day must still produce a digest, not an empty skip"
    assert digest._is_weak(PROFILE, jobs)                     # flagged as a quiet-day digest


def test_fallback_is_capped(patched):
    patched["picks"] = [_job(str(i), 5) for i in range(10)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert len(jobs) == digest.FALLBACK_LIMIT                 # not the full weak pile


def test_truly_empty_still_skips(patched):
    patched["picks"] = []
    assert digest.build_digest(PROFILE, limit=5) == []        # nothing new -> genuine skip


def test_already_sent_weak_matches_are_not_resent(patched):
    patched["picks"] = [_job("c", 5), _job("d", 4)]
    patched["already"] = {"c", "d"}
    assert digest.build_digest(PROFILE, limit=5) == []        # dedup still holds for weak picks


def test_quiet_day_subject_and_greeting_do_not_oversell(patched):
    weak = [_job("c", 5), _job("d", 4)]
    subject = digest.subject_line(PROFILE, weak)
    assert "No strong matches" in subject
    assert "new" not in subject                               # never "N new roles for you"
    assert "No strong matches today" in digest._greeting_html(PROFILE, weak)

    strong = [_job("a", 9), _job("b", 8)]
    assert "new" in digest.subject_line(PROFILE, strong)      # strong day keeps its old subject
    assert not digest._is_weak(PROFILE, strong)


# --------------------------------------------------- per-subscriber strong-fit bar ---

def test_min_score_raises_the_strong_bar_per_subscriber(patched):
    # At the default bar (6) a 7 headlines. A subscriber who set min_score=8 gets that same 7
    # as a quiet-day (weak) pick instead — but STILL gets an email, because the fallback never
    # goes silent. min_score is a quality dial, not an off switch.
    patched["picks"] = [_job("a", 7), _job("b", 5)]
    default_jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in default_jobs] == ["a"]     # 7 is strong at the default bar
    assert not digest._is_weak(PROFILE, default_jobs)

    strict = {**PROFILE, "min_score": 8}
    strict_jobs = digest.build_digest(strict, limit=5)
    # Nothing clears 8, so the quiet-day fallback surfaces the best weaker picks — an email, not
    # a skip. (Mutation check: pin build_digest back to the constant and this returns ["a"].)
    assert [j["posting_id"] for j in strict_jobs] == ["a", "b"]
    assert digest._is_weak(strict, strict_jobs)


def test_min_score_is_clamped_on_read():
    assert store.profile_min_score({}) == 6                     # absent -> default
    assert store.profile_min_score({"min_score": None}) == 6    # NULL column -> default
    assert store.profile_min_score({"min_score": 99}) == 10     # clamped to the ceiling
    assert store.profile_min_score({"min_score": 0}) == 4       # clamped to the floor
    assert store.clean_min_score("7") == 7                      # a stringy body value coerces
    assert store.clean_min_score("junk") == 6                   # unparseable -> default


# ------------------------------------------------------------------ work-setup tag ---

def test_a_hybrid_job_is_tagged_hybrid_and_never_remote():
    """Before migration 012 these carried no work tag at all — a Prague office job with two
    days from home read identically in the email to one with five days in the office. The
    `elif` matters as much as the tag: `region in ('eu','worldwide')` alone would have added
    "Remote" to a hybrid EU role, which is the claim that started all of this."""
    assert "Hybrid" in digest._tags({"work_mode": "hybrid", "region": "cz"})
    assert "Remote" not in digest._tags({"work_mode": "hybrid", "region": "eu"})
    assert "Remote" not in digest._tags(
        {"work_mode": "hybrid", "region": "eu", "remote_signal": True})


def test_remote_and_unstated_jobs_tag_as_they_always_did():
    assert "Remote" in digest._tags({"work_mode": "remote", "remote_signal": True})
    assert "Remote" in digest._tags({"region": "worldwide"})
    # Unknown work_mode adds no work tag of its own — we did not learn anything, so we do not
    # claim anything.
    assert "Hybrid" not in digest._tags({"region": "cz", "seniority": "junior"})
