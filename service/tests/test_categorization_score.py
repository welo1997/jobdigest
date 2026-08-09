"""The scorer is the gate, so the gate itself has to be un-gameable.

`scripts/categorization_score.py` decides whether a categorisation change ships. Its whole
job is to stop the loop optimising "percent uncategorised", which is trivially improved by
assigning everything to something — a change that would make the product strictly worse while
every reported number got better.

Three properties keep it honest, and each is pinned here:

  - **The answer key is independent of the classifier.** Truth comes from the publisher's
    occupation code; the classifier sees only the title. If truth were ever derived from
    `taxonomy.classify`, accuracy would be 100% by construction and the gate would be
    decorative.
  - **A field with no matching category is excluded, never mapped to the nearest one.**
    Swedish social work, sanitation, agriculture and security have no counterpart in
    `CATEGORIES`. Mapping them somewhere close would grade the classifier against a wrong
    answer and reward guessing — precisely the behaviour the gate exists to prevent.
  - **Every mapped value is a real category.** A typo in the map silently grades a whole
    category as 0%, which reads as a classifier regression and would send the loop chasing a
    bug that does not exist. Same failure as the `source_category` hint guard: a value that
    no consumer can name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "categorization_score.py"


def _load():
    spec = importlib.util.spec_from_file_location("categorization_score", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["categorization_score"] = module
    spec.loader.exec_module(module)
    return module


cs = _load()


# --- the map must only ever name real categories ------------------------------------------


def test_every_mapped_value_is_a_real_category():
    """A typo grades that category as 0% and reads as a regression that never happened."""
    mapped = set(cs.FIELD_MAP.values()) | set(cs.GROUP_MAP.values())
    unknown = mapped - set(taxonomy.CATEGORIES)
    assert not unknown, f"answer key names categories that do not exist: {sorted(unknown)}"


def test_uncategorised_is_never_an_expected_answer():
    """`uncategorised` is what the classifier says when it declines. Grading against it would
    reward declining, which is the opposite of what the gate is for."""
    assert taxonomy.UNCATEGORISED not in set(cs.FIELD_MAP.values())
    assert taxonomy.UNCATEGORISED not in set(cs.GROUP_MAP.values())


def test_out_of_scope_fields_are_excluded_rather_than_mapped():
    """The honest answer for a Swedish social-work ad is 'this taxonomy has no category',
    not 'healthcare, near enough'. Mapping it would score the classifier against a wrong key."""
    for field in cs.OUT_OF_SCOPE:
        assert field not in cs.FIELD_MAP, f"{field} is both out of scope and mapped"
        assert cs.truth_for({"field": field, "group": ""}) is None


def test_a_group_mapping_beats_its_field():
    """"Data/IT" spans software, IT support and platform work. Field-level truth would grade
    three distinct categories as one and report the classifier wrong when it was right."""
    assert cs.truth_for({"field": "Data/IT",
                         "group": "Supporttekniker, IT"}) == "customer_support"
    assert cs.truth_for({"field": "Data/IT",
                         "group": "Systemadministratörer"}) == "devops_platform"


# --- scoring arithmetic --------------------------------------------------------------------


def test_score_counts_only_graded_rows():
    rows = [
        {"title": "Sjuksköterska", "field": "Hälso- och sjukvård", "group": ""},
        {"title": "Socialsekreterare", "field": "Yrken med social inriktning", "group": ""},
        {"title": "Något helt annat", "field": "Ingen aning", "group": ""},
    ]
    s = cs.score(rows)
    assert s["graded"] == 1, "out-of-scope and unmapped rows must not be graded"
    assert s["out_of_scope"] == 1
    assert s["unmapped"] == 1


def test_declining_counts_as_a_miss_not_a_pass():
    """The dangerous regression is a classifier that answers `uncategorised` everywhere and
    is scored as correct for it."""
    rows = [{"title": "zzzz nothing matches this", "field": "Hälso- och sjukvård", "group": ""}]
    s = cs.score(rows)
    assert s["graded"] == 1
    assert s["correct"] == 0
    assert s["missed_as_uncategorised"] == 1


def test_accuracy_is_reported_per_category():
    rows = [{"title": "Sjuksköterska", "field": "Hälso- och sjukvård", "group": ""},
            {"title": "Lärare", "field": "Pedagogik", "group": ""}]
    s = cs.score(rows)
    assert set(s["per_cat"]) == {"healthcare", "education"}


def test_coverage_needs_no_answer_key():
    uncat, total = cs.coverage_of(["Sjuksköterska", "zzzz", "Data Engineer"])
    assert total == 3 and uncat == 1


# --- coverage sees hints, accuracy must not -----------------------------------------------


def test_coverage_counts_a_hint_classified_row_as_covered():
    """The register sources are classified by the publisher's occupation code, not the title.
    A title-only coverage number reported the 1 534 Czech vacancies recovered on 2026-08-09 as
    zero improvement — the change looked worthless and the loop would have moved on."""
    uncat, total = cs.coverage_of([("Neklasifikovatelný název", "healthcare")])
    assert (uncat, total) == (0, 1)


def test_accuracy_never_reads_the_hint():
    """Grading a hint-classified row against the code that produced the hint is circular —
    100% by construction, and the gate becomes decorative for the sources it matters most for.

    `score` takes rows of {title, field, group} and must classify from the title alone.
    """
    import inspect

    src = inspect.getsource(cs.score)
    assert "classify(row.get(\"title\"))" in src, (
        "score() must call classify with the title only; passing a hint here would make "
        "accuracy circular for every register source"
    )
