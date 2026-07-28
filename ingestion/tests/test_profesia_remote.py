"""Profesia work-setup parsing: an occasional home office is not a remote job.

Profesia puts the arrangement inside the location string — "Bratislava, Slovensko (Pozícia
umožňuje občasnú prácu z domu)". Because `remote_signal` is what exempts a posting from the
location gate, reading that as remote sends a Bratislava office job to someone who chose
Prague and nowhere else.

The Slovak/Czech inflection is the reason this survived: the phrase is "prácu z domu" in the
accusative, so neither a search for "práce z domova" nor the equivalent jobs.cz fix would
have found it. 462 live postings on 2026-07-28.
"""

from ingestion.sources.profesia import ProfesiaSource
from ingestion.sources.profesia import _remote_from_location as _remote


def test_occasional_work_from_home_slovak_accusative_is_not_remote():
    assert _remote("Bratislava, Slovensko (Pozícia umožňuje občasnú prácu z domu)") is False


def test_occasional_work_from_home_czech_is_not_remote():
    assert _remote("Praha, Česko (Pozice umožňuje občasnou práci z domu)") is False


def test_the_english_rendering_of_the_same_field():
    """Profesia.sk serves this field in English too, so a Czech-only stem list misses it —
    320 live postings, spotted in a real subscriber's shortlist while matching by hand."""
    assert _remote("Košice, Slovakia (Job with occasional home office)") is False
    assert _remote("Bratislava, Slovakia (Job with partial home office)") is False


def test_predominantly_and_partially_are_not_remote():
    assert _remote("Košice (prevažne práca z domu)") is False
    assert _remote("Brno (částečně práce z domova)") is False


def test_genuine_remote_survives():
    # The correction must not strip real remote work; `remote_scope` subscribers depend on it.
    assert _remote("Slovensko (práca z domu)") is True
    assert _remote("Remote") is True
    assert _remote("Praha (home office)") is True


def test_a_plain_location_is_not_remote():
    assert _remote("Bratislava, Slovensko") is False
    assert _remote("") is False


def test_the_source_class_still_imports():
    """Guards against the rule being moved out from under the parser."""
    assert ProfesiaSource().source_name == "profesia"
