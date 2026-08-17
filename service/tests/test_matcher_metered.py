"""The metered on-box matcher: what it must and must not spend tokens on.

These pin the guarantees that keep the metered API path cheap and safe after the claude.ai
routine was retired (2026-08-17):

* `run(profiles=...)` matches **exactly** the list it is handed — it does not fall back to
  every sendable subscriber — because the pipeline hands it only the subscribers due today.
* The pipeline actually passes that *due* subset, so a weekly subscriber is not AI-matched,
  and billed, on the five days no digest goes out for them. This is the single biggest token
  saving in the move off the routine, and it is invisible if it regresses (the digest still
  looks fine; the bill quietly triples).
* `MATCHER_MAX_TOKENS` aborts a run mid-way rather than billing every subscriber when a bug
  inflates usage.
* `match_one` matches one already-resolved subscriber (the on-demand path) and nothing else.

No real API calls: `match_profile` is replaced with a fake that records the token usage the
run-level accounting reads, so the abort budget can be exercised deterministically.
"""

from datetime import datetime, timezone

import pytest

from service import matcher, pipeline


@pytest.fixture
def stub_store(monkeypatch):
    """Make every subscriber retrieve one candidate, and swallow the bookkeeping writes."""
    monkeypatch.setattr(matcher.store, "query_shortlist_meta",
                        lambda p, limit=120: ([{"posting_id": f"job-{p['id']}"}], {"n": 1, "widened": False}))
    monkeypatch.setattr(matcher.store, "already_sent_ids", lambda pid: set())
    monkeypatch.setattr(matcher.store, "record_digest_run", lambda *a, **k: None)
    upserts: list[tuple] = []
    monkeypatch.setattr(matcher.store, "upsert_match",
                        lambda pid, posting_id, score, summary: upserts.append((pid, posting_id)))
    return upserts


def _fake_match_profile(tokens_per_call: int):
    """A match_profile that bills `tokens_per_call` input tokens and returns one pick."""
    def _mp(client, profile, shortlist, usage_acc=None):
        if usage_acc is not None:
            usage_acc["input"] += tokens_per_call
            usage_acc["output"] += 1
            usage_acc["calls"] += 1
        return [{"posting_id": shortlist[0]["posting_id"], "score": 8, "summary": "fit"}]
    return _mp


def test_run_matches_exactly_the_profiles_it_is_handed(stub_store, monkeypatch):
    # If run() ever fell back to sendable_profiles() here, this stub would blow up — which is
    # the point: the caller's list is authoritative, nobody outside it is billed.
    monkeypatch.setattr(matcher.store, "sendable_profiles",
                        lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    monkeypatch.setattr(matcher, "match_profile", _fake_match_profile(100))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    given = [{"id": "a", "email": "a@x"}, {"id": "b", "email": "b@x"}]
    matcher.run(profiles=given)

    assert {pid for pid, _ in stub_store} == {"a", "b"}


def test_abort_budget_stops_the_run_before_billing_everyone(stub_store, monkeypatch):
    # The budget is checked *before* each call, so it bounds total spend to roughly the ceiling
    # plus one subscriber's worth. Each call bills 1000 tokens; a 500 ceiling admits the first
    # (0 < 500), then trips before the second (1001 >= 500) — one subscriber billed, not three.
    monkeypatch.setattr(matcher, "match_profile", _fake_match_profile(1000))
    monkeypatch.setattr(matcher, "MAX_RUN_TOKENS", 500)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    given = [{"id": "a", "email": "a@x"}, {"id": "b", "email": "b@x"}, {"id": "c", "email": "c@x"}]
    matcher.run(profiles=given)

    assert len(stub_store) == 1                     # aborted after the first subscriber


def test_match_one_matches_only_that_subscriber(stub_store, monkeypatch):
    monkeypatch.setattr(matcher, "match_profile", _fake_match_profile(100))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    n = matcher.match_one({"id": "solo", "email": "solo@x"})

    assert n == 1
    assert {pid for pid, _ in stub_store} == {"solo"}


def test_match_one_without_a_key_raises(monkeypatch):
    # A misconfigured box must fail loudly (the endpoint turns this into a 503) rather than
    # silently produce no picks.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(KeyError):
        matcher.match_one({"id": "solo", "email": "solo@x"})


def test_pipeline_matches_only_due_subscribers(monkeypatch):
    """The token-saving guarantee: `--match` reranks the due set, not every sendable profile."""
    now = datetime.now(timezone.utc)
    due = {"id": "due", "email": "due@x", "last_digest_at": None, "frequency": "daily"}
    not_due = {"id": "notdue", "email": "nd@x", "last_digest_at": now, "frequency": "daily"}

    monkeypatch.setattr(pipeline.store, "sendable_profiles", lambda: [due, not_due])

    captured = {}
    monkeypatch.setattr(matcher, "run",
                        lambda profiles=None, **k: captured.update(ids=[p["id"] for p in profiles]))
    sent_to: list[str] = []
    monkeypatch.setattr(pipeline, "send_one",
                        lambda p, **k: (sent_to.append(p["id"]), {"sent": True, "n": 1})[1])

    pipeline.run(match=True, prune=False)

    assert captured["ids"] == ["due"]               # not_due was never handed to the matcher
    assert sent_to == ["due"]                       # nor sent to
