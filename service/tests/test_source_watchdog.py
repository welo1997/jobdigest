"""The ingestion watchdog, driven by the two incidents it exists for.

Both failure shapes are reproduced with the *measured* production numbers rather than
invented ones, because the thresholds are the whole design and a threshold tested against a
made-up corpus only proves arithmetic. The startupjobs churn is the case that matters: on
`active` as the denominator it reads 48% and no sane threshold fires; on rows-returned it
reads 100%. A first version of the check used `active` and would have missed the incident it
was written for — that mistake is pinned here, not just avoided.
"""

from __future__ import annotations

import pytest

from service import source_watchdog as sw

#: Real per-source figures from production on 2026-08-07 (`store.source_freshness`).
HEALTHY = [
    {"source": "greenhouse", "active": 16786, "seen_today": 16786, "new_today": 528,
     "age_days": 0.2},
    {"source": "workday", "active": 9179, "seen_today": 9179, "new_today": 1479,
     "age_days": 0.2},
    {"source": "mpsv", "active": 7456, "seen_today": 7456, "new_today": 111,
     "age_days": 0.2},
    {"source": "cocuma", "active": 313, "seen_today": 313, "new_today": 3, "age_days": 0.2},
]


@pytest.fixture
def patched(monkeypatch):
    """Declare a corpus and which sources gather() runs, with no DB and no adapters."""
    state = {"rows": list(HEALTHY), "expected": {r["source"] for r in HEALTHY}}
    monkeypatch.setattr(sw.store, "source_freshness", lambda: list(state["rows"]))
    monkeypatch.setattr(sw, "expected_sources", lambda: set(state["expected"]))
    return state


def problems(state) -> list[str]:
    return sw.findings()[0]


def test_a_healthy_corpus_is_silent(patched):
    assert problems(patched) == []


def test_the_startupjobs_silent_zero_is_caught(patched):
    """The 2026-08-06 incident: the API moved, `fetch` swallowed a 404 into `[]`, and the
    source's freshest row fell a day behind everything else while nothing raised. It was
    found by luck. This is the check that replaces the luck."""
    patched["rows"].append(
        {"source": "startupjobs", "active": 495, "seen_today": 0, "new_today": 0,
         "age_days": 2.4})
    patched["expected"].add("startupjobs")

    found = problems(patched)
    assert len(found) == 1 and found[0].startswith("SILENT ZERO")
    assert "startupjobs" in found[0]


def test_the_startupjobs_id_churn_is_caught(patched):
    """The 2026-08-07 incident, with the numbers as they actually were: the URL scheme
    changed, `posting_id = md5(url)` re-minted every row, and 450 "new" postings were the
    same 450 jobs. The count looked entirely ordinary."""
    patched["rows"].append(
        {"source": "startupjobs", "active": 945, "seen_today": 450, "new_today": 450,
         "age_days": 0.2})
    patched["expected"].add("startupjobs")

    found = problems(patched)
    assert len(found) == 1 and found[0].startswith("ID CHURN")
    assert "450 of the 450" in found[0]


def test_active_as_the_denominator_would_have_missed_it(patched):
    """Pins the bug the first draft of this check had.

    The superseded rows stay active for a whole staleness window, so measuring against
    `active` halves the ratio — 450/945 = 48%, under any threshold that is not also firing
    on healthy sources (workday legitimately turns over 16%). The denominator has to be what
    the run returned.
    """
    active_ratio = 450 / 945
    seen_ratio = 450 / 450
    assert active_ratio < sw.CHURN_SHARE < seen_ratio
    # And the threshold must clear the busiest genuine source by a real margin.
    assert max(r["new_today"] / r["seen_today"] for r in HEALTHY) < 0.2


def test_a_retired_source_never_alerts(patched):
    """jobscz and profesia are excluded on Alma Career's terms. They sit in the DB with old
    rows for ever, and a monitor that reports a deliberate decision daily gets muted — which
    is how the next real alert goes unread."""
    patched["rows"] += [
        {"source": "jobscz", "active": 0, "seen_today": 0, "new_today": 0, "age_days": 96.0},
        {"source": "profesia", "active": 0, "seen_today": 0, "new_today": 0,
         "age_days": 96.0},
    ]
    assert problems(patched) == []          # not in `expected`, so not our business


def test_a_small_or_brand_new_source_is_not_churn(patched):
    """A newly added board is 100% new on its first run, by definition. Below the floor the
    share carries no information."""
    patched["rows"].append(
        {"source": "teamtailor", "active": 40, "seen_today": 40, "new_today": 40,
         "age_days": 0.1})
    patched["expected"].add("teamtailor")
    assert problems(patched) == []


def test_a_source_that_vanished_entirely_is_reported(patched):
    """An absence must be a row, not a gap the reader has to notice."""
    patched["expected"].add("himalayas")     # expected, but no row in the corpus at all
    found = problems(patched)
    assert len(found) == 1 and "himalayas" in found[0]


def test_exit_code_is_the_alert(patched):
    """systemd's OnFailure is the only notification path — a healthy run must exit 0 and a
    problem must not merely print."""
    assert sw.report()[1] == 0
    patched["rows"].append(
        {"source": "startupjobs", "active": 945, "seen_today": 450, "new_today": 450,
         "age_days": 0.2})
    patched["expected"].add("startupjobs")
    assert sw.report()[1] == 1
