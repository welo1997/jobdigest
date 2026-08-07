"""The ingestion watchdog, driven by the two incidents it exists for.

Every figure here is measured production data from 2026-08-07, not invented. That matters
more than usual: the thresholds *are* the design, and the first version of this check was
tested against plausible-looking made-up numbers, passed, and would have produced three
false alerts on its first real run — `arbeitnow`, `himalayas` and `teamtailor`, all healthy.
It measured how much of a run was new, and rolling "newest N" feeds are ~100% new every day
for ever. A monitor that cries daily gets muted, which is the failure this module's own
docstring warns about, so that near-miss is pinned below rather than quietly corrected.
"""

from __future__ import annotations

import pytest

from service import source_watchdog as sw


def row(source, active, seen_today, new_today, repeat_today, age_days=0.2,
        baseline=0.0, keyed_today=None):
    return {"source": source, "active": active, "seen_today": seen_today,
            "new_today": new_today,
            "keyed_today": new_today if keyed_today is None else keyed_today,
            "repeat_today": repeat_today, "age_days": age_days,
            "baseline_share": baseline}


#: Real per-source figures from production (`store.source_freshness`, 2026-08-07). The
#: `repeat_today` column is the measured "same job as an earlier row" count.
HEALTHY = [
    row("greenhouse", 16786, 15714, 528, 44),      # 8.3%
    row("workday", 9179, 5424, 1479, 363),         # 24.5% — the busiest honest source
    row("arbeitnow", 2349, 375, 375, 47),          # 12.5% — rolling feed, 100% new daily
    row("himalayas", 1526, 300, 300, 3),           # 1.0%  — rolling feed, 100% new daily
    row("teamtailor", 110, 110, 110, 0),           # 0%    — first run ever
    row("cocuma", 313, 303, 3, 0),
    # The aggregator: the same job under many ad ids, ~69-86% repeat EVERY day. A level
    # threshold alerts on it for ever; the baseline is what keeps it silent.
    row("adzuna", 8887, 3242, 1796, 1535, baseline=0.811),
]

#: The 2026-08-07 re-mint, as it actually was: 96.7% today against ~0-3.8% on its own
#: prior days. The jump is the signal, not the level.
CHURNED = row("startupjobs", 945, 450, 450, 435, baseline=0.038)


@pytest.fixture
def patched(monkeypatch):
    """Declare a corpus and which sources gather() runs, with no DB and no adapters."""
    state = {"rows": [dict(r) for r in HEALTHY],
             "expected": {r["source"] for r in HEALTHY}}
    monkeypatch.setattr(sw.store, "source_freshness", lambda: list(state["rows"]))
    monkeypatch.setattr(sw, "expected_sources", lambda: set(state["expected"]))
    return state


def problems(state) -> list[str]:
    return sw.findings()[0]


def test_a_healthy_corpus_is_silent(patched):
    """Including the two rolling feeds that are 100% new every single day."""
    assert problems(patched) == []


def test_the_rolling_feeds_never_alert_however_new_they_look(patched):
    """The false-positive class that would have muted this monitor.

    arbeitnow and himalayas return their whole run as new rows daily and always will —
    old jobs stop being served, so there is nothing to re-see. Newness carries no signal
    here; only "is this a job we already had" does.
    """
    for name in ("arbeitnow", "himalayas"):
        r = next(r for r in patched["rows"] if r["source"] == name)
        assert r["new_today"] == r["seen_today"]        # 100% new, by design
    assert problems(patched) == []


def test_the_startupjobs_id_churn_is_caught(patched):
    """The real incident: the URL scheme changed, posting_id = md5(url) re-minted every row,
    and 450 "new" postings were the same 450 jobs behind an ordinary-looking count."""
    patched["rows"].append(dict(CHURNED))
    patched["expected"].add("startupjobs")

    found = problems(patched)
    assert len(found) == 1 and found[0].startswith("ID CHURN")
    assert "435 of the 450" in found[0]


def test_the_aggregator_is_silent_despite_a_repeat_share_above_the_level(patched):
    """The second false-positive class, and the reason the level alone is not the test.

    adzuna carries the same job under many ad ids — one role had 122 — so it sits at 69-86%
    repeat every single day. It clears CHURN_SHARE and must still never alert, because
    nothing about it changed. Only the jump against its own history distinguishes a source
    that is *always* like this from one that just became like this.
    """
    adzuna = next(r for r in patched["rows"] if r["source"] == "adzuna")
    share = adzuna["repeat_today"] / adzuna["keyed_today"]
    assert share > sw.CHURN_SHARE            # would fire on the level alone
    assert problems(patched) == []           # does not fire on the jump


def test_the_thresholds_sit_in_measured_gaps(patched):
    """A margin, not a coincidence — and if a future source lands inside one of these gaps,
    re-measure rather than nudge the constant."""
    # Level: the busiest honest non-aggregator (workday, multi-site reqs) vs the re-mint.
    honest = max(r["repeat_today"] / r["keyed_today"]
                 for r in HEALTHY if r["keyed_today"] and r["source"] != "adzuna")
    churn = CHURNED["repeat_today"] / CHURNED["keyed_today"]
    assert honest < sw.CHURN_SHARE < churn

    # Jump: adzuna's largest day-on-day move was 4.4 points; the re-mint's was ~93.
    adzuna = next(r for r in patched["rows"] if r["source"] == "adzuna")
    adzuna_jump = adzuna["repeat_today"] / adzuna["keyed_today"] - adzuna["baseline_share"]
    churn_jump = churn - CHURNED["baseline_share"]
    assert adzuna_jump < sw.CHURN_JUMP < churn_jump


def test_a_source_that_becomes_an_aggregator_overnight_still_alerts(patched):
    """The baseline must not be a blanket exemption: adzuna is silent because it did not
    change, not because it is adzuna."""
    adzuna = next(r for r in patched["rows"] if r["source"] == "adzuna")
    adzuna["baseline_share"] = 0.05          # yesterday it was clean
    found = problems(patched)
    assert len(found) == 1 and "adzuna" in found[0]


def test_the_startupjobs_silent_zero_is_caught(patched):
    """The 2026-08-06 incident: the API moved, `fetch` swallowed a 404 into `[]`, and the
    source's freshest row fell a day behind while nothing raised. Found by luck; this is
    what replaces the luck."""
    patched["rows"].append(row("startupjobs", 495, 0, 0, 0, age_days=2.4))
    patched["expected"].add("startupjobs")

    found = problems(patched)
    assert len(found) == 1 and found[0].startswith("SILENT ZERO")


def test_a_retired_source_never_alerts(patched):
    """jobscz and profesia are excluded on Alma Career's terms. They sit in the DB with old
    rows for ever, and a monitor reporting a deliberate decision daily is one that gets
    muted — which is how the next real alert goes unread."""
    patched["rows"] += [row("jobscz", 0, 0, 0, 0, age_days=96.0),
                        row("profesia", 0, 0, 0, 0, age_days=96.0)]
    assert problems(patched) == []          # not in `expected`, so not our business


def test_a_small_source_is_not_churn(patched):
    """Below the floor a handful of genuine re-posts would read as a re-mint."""
    patched["rows"].append(row("recruitee", 220, 214, 20, 20))
    patched["expected"].add("recruitee")
    assert problems(patched) == []


def test_a_source_that_vanished_entirely_is_reported(patched):
    """An absence must be a row, not a gap the reader has to notice."""
    patched["expected"].add("mpsv")          # expected, but no row in the corpus at all
    found = problems(patched)
    assert len(found) == 1 and "mpsv" in found[0]


def test_exit_code_is_the_alert(patched):
    """systemd's OnFailure is the only notification path — a healthy run must exit 0 and a
    problem must not merely print."""
    assert sw.report()[1] == 0
    patched["rows"].append(dict(CHURNED))
    patched["expected"].add("startupjobs")
    assert sw.report()[1] == 1
