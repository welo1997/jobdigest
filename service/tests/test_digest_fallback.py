"""Quiet-day fallback: a subscriber still gets a digest when no match clears EMAIL_MIN_SCORE.

The rule (added 2026-07-23): if there are strong (>= EMAIL_MIN_SCORE) unsent matches, the
email is those and nothing changes. If there are none, the email carries the best *weaker*
(4-5) unsent matches instead of skipping — but it is labelled honestly as a no-strong-match
day. Only a genuinely empty pick list skips the send.

These tests are DB-free: build_digest's two DB reads are monkeypatched. Each assertion is
paired with the behaviour it guards, so breaking the fallback turns one of them red.
"""
from __future__ import annotations

import pytest

from service import digest


def _job(pid: str, score: int, **extra) -> dict:
    j = {"posting_id": pid, "score": score, "title": "Role", "company": "Co",
         "url": "https://example.com/j", "summary": "s", "role_category": "data_engineering"}
    j.update(extra)
    return j


@pytest.fixture
def patched(monkeypatch):
    """Let a test declare the matcher's picks and what was already sent, no DB."""
    state: dict = {"picks": [], "already": set()}
    monkeypatch.setattr(digest.store, "matched_jobs", lambda pid, limit=50: list(state["picks"]))
    monkeypatch.setattr(digest.store, "already_sent_ids", lambda pid: set(state["already"]))
    return state


PROFILE = {"id": "p1", "email": "u@example.com", "manage_token": "t"}


def test_strong_day_returns_only_strong_matches(patched):
    patched["picks"] = [_job("a", 9), _job("b", 7), _job("c", 5), _job("d", 4)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["a", "b"]      # 5 and 4 are excluded
    assert not digest._is_weak(jobs)                          # and it's not a quiet-day digest


def test_quiet_day_falls_back_to_weaker_matches_instead_of_skipping(patched):
    # No match >= 6 — the old behaviour returned [] and the send was skipped.
    patched["picks"] = [_job("c", 5), _job("d", 4)]
    jobs = digest.build_digest(PROFILE, limit=5)
    assert [j["posting_id"] for j in jobs] == ["c", "d"]      # weaker matches DO go out now
    assert jobs, "a quiet day must still produce a digest, not an empty skip"
    assert digest._is_weak(jobs)                              # flagged as a quiet-day digest


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
    assert "No strong matches today" in digest._greeting_html(weak)

    strong = [_job("a", 9), _job("b", 8)]
    assert "new" in digest.subject_line(PROFILE, strong)      # strong day keeps its old subject
    assert not digest._is_weak(strong)


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
