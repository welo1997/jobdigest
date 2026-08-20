"""The link-liveness sweep: it deactivates *only* a confidently-closed page, and never crashes.

`service/liveness.py` runs inside the daily digest job and writes to `postings.is_active`, so two
properties are load-bearing and both are pinned here to *fail* when broken, not merely to pass:

  1. **Only `CLOSED` deactivates.** Every ambiguous verdict (`SHELL`, `BLOCKED`, `UNCONFIRMED`,
     `UNREACHABLE`, `OK`, an errored probe) must leave the posting active. A bare 404 is `UNCONFIRMED`
     on purpose — `form3`/`roblox` answer 404 while rendering fine — so acting on it would delete
     live jobs. If someone widens `DEACTIVATE_VERDICTS`, `test_deactivate_verdicts_is_closed_only`
     goes red and makes them justify it.
  2. **It is non-fatal and write-gated.** A probe that raises is caught and abstains; `run()` never
     raises; and it writes only when *both* the env flag `LIVENESS_APPLY` and the caller's `apply`
     say so — a dry-run pipeline can turn writing off but never on.

Every store/probe call is monkeypatched, so this file needs no DB and no network.
"""

from __future__ import annotations

from ingestion import link_probe
from service import liveness


def _targets(verdict_by_id):
    return [{"posting_id": pid, "url": f"https://example.test/{pid}", "title": "Some Role",
             "company": "Acme", "source": "startupjobs"} for pid in verdict_by_id]


def _wire(monkeypatch, verdict_by_id, *, enabled=True, apply_flag=True):
    """Point liveness at a fake corpus: `verdict_by_id` maps posting_id -> probe verdict."""
    monkeypatch.setattr(liveness, "LIVENESS_ENABLED", enabled)
    monkeypatch.setattr(liveness, "LIVENESS_APPLY", apply_flag)
    monkeypatch.setattr(liveness.store, "matched_active_posting_ids", lambda: set(verdict_by_id))
    monkeypatch.setattr(liveness.store, "posting_targets",
                        lambda ids: _targets(verdict_by_id))
    monkeypatch.setattr(link_probe, "probe",
                        lambda url, title, company: (verdict_by_id[url.rsplit("/", 1)[1]], "note"))
    captured = {}

    def fake_deactivate(ids):
        captured["ids"] = list(ids)
        return len(captured["ids"])

    monkeypatch.setattr(liveness.store, "deactivate_postings", fake_deactivate)
    return captured


# --- property 1: only CLOSED deactivates -------------------------------------------------

def test_only_closed_is_deactivated(monkeypatch):
    verdicts = {"a": "CLOSED", "b": "SHELL", "c": "OK", "d": "UNCONFIRMED",
                "e": "BLOCKED", "f": "CLOSED", "g": "UNREACHABLE"}
    captured = _wire(monkeypatch, verdicts)
    s = liveness.run(due_profiles=None, apply=True)
    assert set(captured["ids"]) == {"a", "f"}
    assert s["closed"] == 2
    assert s["deactivated"] == 2


def test_abstains_when_nothing_is_closed(monkeypatch):
    verdicts = {"a": "SHELL", "b": "UNCONFIRMED", "c": "BLOCKED", "d": "OK"}
    captured = _wire(monkeypatch, verdicts)
    s = liveness.run(due_profiles=None, apply=True)
    assert "ids" not in captured                       # deactivate_postings never called
    assert s["closed"] == 0 and s["deactivated"] == 0


def test_deactivate_verdicts_is_closed_only():
    # The whole safety argument in one line: the action set is exactly {CLOSED}. Widening it is a
    # deliberate act that must break this test first.
    assert link_probe.DEACTIVATE_VERDICTS == frozenset({"CLOSED"})


# --- property 2: non-fatal + write-gated -------------------------------------------------

def test_disabled_does_nothing(monkeypatch):
    captured = _wire(monkeypatch, {"a": "CLOSED"}, enabled=False)
    s = liveness.run(due_profiles=None, apply=True)
    assert s == {"enabled": False}
    assert "ids" not in captured


def test_env_dry_run_probes_but_never_writes(monkeypatch):
    captured = _wire(monkeypatch, {"a": "CLOSED", "b": "CLOSED"}, apply_flag=False)
    s = liveness.run(due_profiles=None, apply=True)   # caller says apply, env flag says no
    assert "ids" not in captured
    assert s["closed"] == 2 and s["deactivated"] == 0


def test_pipeline_dry_run_forces_no_write(monkeypatch):
    captured = _wire(monkeypatch, {"a": "CLOSED"}, apply_flag=True)
    s = liveness.run(due_profiles=None, apply=False)  # env flag says yes, caller (dry pipeline) says no
    assert "ids" not in captured
    assert s["closed"] == 1 and s["deactivated"] == 0


def test_a_probe_that_raises_is_non_fatal_and_abstains(monkeypatch):
    verdicts = {"a": "CLOSED", "b": "boom"}
    captured = _wire(monkeypatch, verdicts)

    def flaky_probe(url, title, company):
        pid = url.rsplit("/", 1)[1]
        if verdicts[pid] == "boom":
            raise RuntimeError("network exploded")
        return verdicts[pid], "note"

    monkeypatch.setattr(link_probe, "probe", flaky_probe)
    s = liveness.run(due_profiles=None, apply=True)   # must not raise
    assert set(captured["ids"]) == {"a"}              # the healthy CLOSED still acted on
    assert s["verdicts"].get("ERROR") == 1            # the raiser abstained, counted as ERROR


def test_gather_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(liveness, "LIVENESS_ENABLED", True)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(liveness.store, "matched_active_posting_ids", boom)
    s = liveness.run(due_profiles=None, apply=True)   # must not raise
    assert s["probed"] == 0 and s["deactivated"] == 0


# --- the markers actually catch the real dead pages --------------------------------------

def test_real_dead_page_snippets_classify_closed():
    # Verbatim rendered text from the two pages that motivated this (2026-08-20).
    assert link_probe.is_closed(
        "Data Engineer - Snowflake/dbt | StartupJobs.com ... This offer isn't -to-date "
        "The offer is no longer valid. Medior Jobs")
    assert link_probe.is_closed(
        "Příležitost nenalezena (404) Ups! Příležitost již vychytal někdo před vámi, proto tu není.")
    # A live posting's text must not trip it.
    assert not link_probe.is_closed(
        "Senior Data Engineer at Collibra. Join our team building the data intelligence cloud. Apply now.")
