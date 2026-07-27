"""Tests for the per-subscriber watchdog and the `digest_runs` record behind it.

These guard the only mechanism that notices a subscriber getting nothing. Every matching
bug found on 2026-07-26 was invisible from outside — no exception, no failed timer — because
the pipeline's health and a subscriber's outcome are different things, and only the first was
ever checked. If these tests pass while the watchdog stays quiet on a starved subscriber,
they are documenting nothing.
"""

from __future__ import annotations

import pytest

from service import store, watchdog


# ------------------------------------------------------------------- diagnosis ---
# The alert has to say *which* failure it is, because the two look identical from a summary
# count and want opposite responses: retrieval starving someone is our bug, the matcher
# rejecting a fair shortlist is not.

@pytest.mark.parametrize("row,expected", [
    ({"last_run_day": None}, "NO RUN"),
    ({"last_run_day": "d", "widened": True, "shortlist_n": 120, "picks_n": 0}, "RETRIEVAL"),
    ({"last_run_day": "d", "shortlist_n": 3, "picks_n": 0}, "RETRIEVAL"),
    ({"last_run_day": "d", "shortlist_n": 120, "picks_n": 0}, "MATCHER"),
    ({"last_run_day": "d", "shortlist_n": 120, "picks_n": 5, "sendable_n": 0}, "DELIVERY"),
])
def test_each_failure_shape_is_named(row, expected):
    assert watchdog.diagnose(row).startswith(expected)


def test_a_widened_shortlist_is_called_retrieval_even_when_it_is_full():
    """The widened shortlist is large by construction — the floor filled it from the whole
    location. Reading its size as health is exactly how the fallback would hide the subscriber
    it rescued, which is the one worth investigating: the taxonomy does not model their field.
    """
    row = {"last_run_day": "d", "widened": True, "shortlist_n": 200, "picks_n": 0}
    assert watchdog.diagnose(row).startswith("RETRIEVAL")


# ---------------------------------------------------------------------- report ---

def test_a_starved_subscriber_makes_the_run_fail(monkeypatch):
    """Non-zero exit is the entire alerting mechanism: the systemd unit carries
    OnFailure=jobdigest-alert@%n.service, so a zero exit means silence."""
    monkeypatch.setattr(store, "starved_profiles", lambda days: [
        {"id": "0b9836b5-1111-2222-3333-444455556666", "email": "subscriber-a@example.com",
         "last_digest_at": None, "last_run_day": "d", "shortlist_n": 1, "picks_n": 0},
    ])
    text, n = watchdog.report(days=3)
    assert n == 1
    assert "RETRIEVAL" in text


def test_a_healthy_run_reports_nothing_to_alert_on(monkeypatch):
    monkeypatch.setattr(store, "starved_profiles", lambda days: [])
    text, n = watchdog.report(days=3)
    assert n == 0
    assert "OK" in text


def test_the_report_does_not_carry_full_addresses(monkeypatch):
    """This text leaves the box inside an alert email. Same reason `shortlists.json` carries
    no address: the profile id is enough for the operator to act on."""
    monkeypatch.setattr(store, "starved_profiles", lambda days: [
        {"id": "0b9836b5-1111-2222-3333-444455556666", "email": "subscriber-a@example.com",
         "last_digest_at": None, "last_run_day": "d", "shortlist_n": 1, "picks_n": 0},
    ])
    text, _ = watchdog.report(days=3)
    assert "subscriber-a@example.com" not in text
    assert "sub***@example.com" in text          # still recognisable
    assert "0b9836b5" in text                  # and actionable


def test_masking_survives_a_short_or_missing_address():
    assert watchdog._mask(None) == "(no address)"
    assert watchdog._mask("not-an-email") == "(no address)"
    assert watchdog._mask("ab@x.cz").endswith("@x.cz")


# ------------------------------------------------------------------ recording ---

class _FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sink.append({"sql": sql, "params": list(params or [])})


@pytest.fixture
def executed(monkeypatch):
    sink: list = []
    monkeypatch.setattr(store, "cursor", lambda commit=False: _FakeCursor(sink))
    return sink


def test_only_known_columns_reach_the_statement(executed):
    """Field names are interpolated into SQL, not bound — so the whitelist is the thing
    keeping a caller's typo (or anything worse) out of the statement."""
    store.record_digest_run("p1", shortlist_n=5, nonsense="; drop table profiles")
    assert len(executed) == 1
    assert "nonsense" not in executed[0]["sql"]
    assert "drop table" not in executed[0]["sql"]
    assert executed[0]["params"] == ["p1", 5]


def test_a_partial_update_does_not_reset_the_other_columns(executed):
    """Three stages write this row at different times — export knows shortlist_n, import
    knows picks_n, the pipeline knows sent. An upsert that listed every column would have
    each stage overwrite the others' numbers with defaults."""
    store.record_digest_run("p1", picks_n=3)
    sql = executed[0]["sql"]
    assert "picks_n = excluded.picks_n" in sql
    assert "shortlist_n" not in sql
    assert "sendable_n" not in sql


def test_nothing_to_record_is_not_a_statement(executed):
    store.record_digest_run("p1")
    store.record_digest_run(None, sent=True)
    assert executed == []


def test_recording_failure_never_reaches_the_caller(monkeypatch):
    """Diagnostics must not be able to break a send. A failure to record that a subscriber
    got their digest would otherwise become the reason they don't get it."""
    def boom(commit=False):
        raise RuntimeError("db is down")
    monkeypatch.setattr(store, "cursor", boom)
    store.record_digest_run("p1", sent=True)          # must not raise
