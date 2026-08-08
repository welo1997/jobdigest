"""The scaling budget must measure the real payload, and must refuse to look authoritative
about a sample that cannot represent production.

`scripts/scaling_budget.py` answers one question — *what does one subscriber cost the matcher
per day* — and every decision on the ladder in `notes/scaling/PLAN.md` is sized against its
answer. That makes two failure modes expensive, and they are the ones pinned here:

  - **Measuring something other than what ships.** The number has to come from the same field
    set `matcher._candidate_export` writes, or the budget drifts away from the file the
    routine actually reads and nothing says so.
  - **Reporting a fixture as if it were production.** The dev database holds 40 postings whose
    descriptions average 57 characters; production holds ~99k averaging 4 791, so essentially
    every real candidate hits the `DESC_CHARS` cap and a dev-shaped sample understates the
    payload by roughly half. A tool that prints a confident number off that sample is the
    "green tests, wrong answer" failure this repo keeps writing down — so an unrepresentative
    sample must be *flagged*, not silently averaged.

The token band is deliberately a band. Claude tokenisation is model-specific and this payload
is the awkward case for any rule of thumb — JSON keys, md5 hex ids, and Czech/Swedish/
Norwegian titles all tokenise worse than English prose. `--count-tokens` replaces the band
with a measurement; until then the estimate must not pretend to a precision it does not have.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "scaling_budget.py"


def _load():
    """`scripts/` is not a package, so load the file directly.

    It must be registered in `sys.modules` before `exec_module`: `@dataclass` resolves
    annotations through `sys.modules[cls.__module__]`, and an unregistered module makes that
    lookup return None.
    """
    spec = importlib.util.spec_from_file_location("scaling_budget", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["scaling_budget"] = module
    spec.loader.exec_module(module)
    return module


sb = _load()


def _payload(n_profiles: int = 2, n_candidates: int = 4, desc_len: int = 320) -> dict:
    """A shortlists.json shaped exactly like the real export."""
    return {
        "generated_at": "2026-08-08T03:00:00+00:00",
        "instructions": "…routine instructions…",
        "profiles": [
            {
                "profile_id": f"00000000-0000-0000-0000-{i:012d}",
                "profile": {"roles": ["data_analyst"], "locations": "Prague"},
                "candidates": [
                    {
                        "posting_id": f"{j:032x}",
                        "title": "Senior Data Engineer",
                        "company": "Example s.r.o.",
                        "location": "Praha",
                        "city": "cz:prague",
                        "remote": False,
                        "work_mode": "hybrid",
                        "education_min": None,
                        "seniority": "senior",
                        "work_type": "fulltime",
                        "part_time": False,
                        "salary": "60 000 – 80 000 Kč",
                        "description": "x" * desc_len,
                    }
                    for j in range(n_candidates)
                ],
            }
            for i in range(n_profiles)
        ],
    }


# --- it measures what actually ships ------------------------------------------------------


def test_candidate_fields_match_the_real_export():
    """The measured field set is the one `_candidate_export` writes.

    If the exporter grows a field and this list does not, every projection silently
    under-reports from that day on — the same shape as a source that returns a plausible
    number while being wrong.
    """
    from service import matcher

    row = {
        "posting_id": "a" * 32, "title": "t", "company": "c", "location": "l",
        "region": "r", "city": "cz:prague", "remote_signal": False, "work_mode": "hybrid",
        "education_min": None, "seniority": "senior", "work_type": "fulltime",
        "is_part_time": False, "salary_raw": "x", "description": "d" * 1000,
    }
    shipped = set(matcher._candidate_export(row).keys())
    assert shipped == set(sb.CANDIDATE_FIELDS), (
        "scaling_budget.CANDIDATE_FIELDS has drifted from matcher._candidate_export; "
        f"missing={shipped - set(sb.CANDIDATE_FIELDS)} extra={set(sb.CANDIDATE_FIELDS) - shipped}"
    )


def test_bytes_are_measured_not_assumed():
    payload = _payload(n_profiles=2, n_candidates=4)
    m = sb.measure(payload)
    assert m.n_profiles == 2
    assert m.n_candidates == 8
    # The measurement is of the real serialisation, so it must track the actual encoded size.
    encoded = len(json.dumps(payload["profiles"][0], ensure_ascii=False).encode("utf-8"))
    assert abs(m.bytes_per_profile - encoded) <= 2


def test_non_ascii_is_counted_in_bytes_not_characters():
    """Czech and Swedish titles cost more than their character count.

    `len(str)` would under-count every non-English posting — and three of the largest
    sources are not English.
    """
    ascii_payload = _payload(n_profiles=1, n_candidates=1)
    accented = _payload(n_profiles=1, n_candidates=1)
    accented["profiles"][0]["candidates"][0]["title"] = "Vývojář softwaru — Malmö"
    assert sb.measure(accented).bytes_per_profile > sb.measure(ascii_payload).bytes_per_profile


# --- it refuses to launder a fixture into a production number -----------------------------


def test_a_dev_shaped_sample_is_flagged_as_unrepresentative():
    """57-character descriptions are the dev database, not production."""
    m = sb.measure(_payload(desc_len=57))
    assert not m.representative
    assert any("description" in w.lower() for w in m.warnings)


def test_a_production_shaped_sample_is_not_flagged():
    """Full shortlists, descriptions at the cap — the shape production actually exports."""
    m = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    assert m.representative
    assert m.warnings == []


def test_a_thin_shortlist_is_flagged_even_when_descriptions_are_full():
    """Two ways to be unrepresentative, and the description check cannot see this one.

    An export filtered down to a handful of candidates per profile costs a fraction of a real
    ~120-posting shortlist, so a budget taken from it under-reports even though every
    description is at the cap.
    """
    m = sb.measure(_payload(n_candidates=4, desc_len=sb.DESC_CHARS))
    assert not m.representative
    assert any("candidates per profile" in w for w in m.warnings)


def test_an_empty_export_is_refused_rather_than_divided_by_zero():
    with pytest.raises(sb.NotMeasurable):
        sb.measure({"generated_at": "…", "profiles": []})


# --- the projection is the whole point ----------------------------------------------------


def test_projection_scales_linearly_with_subscribers():
    m = sb.measure(_payload(desc_len=sb.DESC_CHARS))
    at_10 = sb.project(m, 10)
    at_1000 = sb.project(m, 1000)
    assert at_1000.tokens_per_day_low == pytest.approx(at_10.tokens_per_day_low * 100, rel=1e-6)


def test_token_estimate_is_a_band_and_brackets_the_ratio():
    m = sb.measure(_payload(desc_len=sb.DESC_CHARS))
    p = sb.project(m, 1)
    assert p.tokens_per_day_low < p.tokens_per_day_high, "a point estimate hides the uncertainty"
    # The band must bracket what the bytes imply under the stated ratios.
    assert p.tokens_per_day_low == pytest.approx(m.bytes_per_profile / sb.CHARS_PER_TOKEN_HIGH, rel=1e-6)
    assert p.tokens_per_day_high == pytest.approx(m.bytes_per_profile / sb.CHARS_PER_TOKEN_LOW, rel=1e-6)


# --- what actually bounds a shard -----------------------------------------------------------


def test_shard_size_is_bounded_by_context_not_bytes():
    """`deploy/matcher-routine.md` has the routine read the file and iterate every subscriber
    in one pass, so a shard has to fit in one context window. Megabytes are not the limit;
    tokens are — which makes shard size fall as the per-subscriber payload grows.
    """
    small = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=40))
    large = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    assert sb.shard_plan(small, 10_000).per_shard_max > \
        sb.shard_plan(large, 10_000).per_shard_max


def test_a_partial_shard_still_counts_as_a_shard():
    """Rounding down would silently drop the remainder.

    This is the `sendable_profiles` tail problem in a new place: a subscriber who falls off
    the end of the shard plan is not an error anywhere, they simply never get matched.
    """
    m = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    plan = sb.shard_plan(m, 10_000)
    assert plan.per_shard_min >= 1
    assert plan.shards_max * plan.per_shard_min >= 10_000
    assert plan.shards_min * plan.per_shard_max >= 10_000


def test_one_subscriber_needs_one_shard_not_zero():
    m = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    assert sb.shard_plan(m, 1).shards_max == 1


def test_a_payload_too_large_for_any_context_is_refused_not_rounded_to_zero():
    """If one subscriber cannot fit a context window, sharding is not the answer and the tool
    must say so rather than reporting a shard that cannot exist."""
    m = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    m.measured_tokens_per_profile = sb.CONTEXT_TOKENS * 2
    with pytest.raises(sb.NotMeasurable):
        sb.shard_plan(m, 100)


def test_the_report_names_the_shard_count():
    m = sb.measure(_payload(n_candidates=sb.SHORTLIST_SIZE, desc_len=sb.DESC_CHARS))
    assert "shard" in sb.render(m, [10_000]).lower()


# --- it must not leak what it is measuring ------------------------------------------------


def test_the_report_carries_no_posting_content_or_ids():
    """The export is pseudonymous by design; a report that quotes it need not be.

    `shortlists.json` carries no email address, but it does carry profile ids and posting
    text. A budget report is aggregates — nothing here should ever end up in a paste.
    """
    payload = _payload()
    payload["profiles"][0]["candidates"][0]["title"] = "UNIQUE-TITLE-MARKER"
    report = sb.render(sb.measure(payload), [10, 1000])
    assert "UNIQUE-TITLE-MARKER" not in report
    assert payload["profiles"][0]["profile_id"] not in report
