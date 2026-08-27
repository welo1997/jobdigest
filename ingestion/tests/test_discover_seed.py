"""`discover_seed` automates the identity read on `discover_ats` output — the check
`inspect_hits.py` did by eye. The one thing that must not be wrong is the verdict: a PASS is an
invitation to wire a board into a live source, and the whole history of this discovery work is
boards that returned a live 200 while belonging to someone else (`ashby:eon`, `lever:zeiss`,
Recruitee demo content). So the decision tree is tested to the letter, and every case below is
the shape of a real trap the curated adapter comments record.

The rule the tests pin:
  * REJECT is reserved for a *positive* name mismatch — an impostor caught, never mere doubt.
  * PASS needs the board's own employer name AND a posting in the target region.
  * everything else is REVIEW — a human's call, which is the safe default, not a silent drop.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

seed = importlib.import_module("scripts.discover_seed")


# --- names_match: the impostor gate --------------------------------------------------------

def test_names_match_accepts_legal_form_and_abbreviation():
    assert seed.names_match("ROI Hunter s.r.o.", "ROI Hunter")
    assert seed.names_match("Productboard, Inc.", "Productboard")
    assert seed.names_match("Škoda Digital", "Skoda Digital")          # diacritics
    assert seed.names_match("Kiwi.com", "Kiwi.com a.s.")


def test_names_match_rejects_a_different_company():
    """The ashby:eon shape: a guessed slug lands on a real board of an unrelated company."""
    assert not seed.names_match("E.ON Energie", "Eon Studio")
    assert not seed.names_match("Amber s.r.o.", "Amber Studio Bucharest")
    assert not seed.names_match("Boost Prague", "Boost New Zealand")


def test_names_match_is_false_on_empty():
    assert not seed.names_match("", "Something")
    assert not seed.names_match("Something", "")


# --- country_of ----------------------------------------------------------------------------

def test_country_of_reads_names_codes_and_leaves_unknown_none():
    assert seed.country_of("Poznań, Poland") == "PL"
    assert seed.country_of("Prague, Czech Republic") == "CZ"
    assert seed.country_of("Brno, CZ") == "CZ"
    assert seed.country_of("Milano, Italia") == "IT"
    assert seed.country_of("") is None
    assert seed.country_of("Somewhere nice") is None          # never guessed


def test_country_of_resolves_gb_and_us_so_they_can_be_excluded():
    # GB is outside the EEA target set on purpose — resolving it is how it gets excluded, the
    # same GB/EEA decoupling the rest of the repo draws.
    assert seed.country_of("London, United Kingdom") == "GB"
    assert seed.country_of("Austin, USA") == "US"


# --- identity_verdict: the decision tree ---------------------------------------------------

def _sampler(n, employer, countries):
    return lambda ats, token, extra: seed.Board(n=n, employer=employer, countries=countries)


def test_pass_when_name_matches_and_region_hits():
    v = seed.identity_verdict("workable", "roi-hunter", "", expected_name="ROI Hunter",
                              target=seed.EEA, sampler=_sampler(4, "ROI Hunter", ["CZ", "CZ"]))
    assert v.status == "PASS"
    assert v.countries == ["CZ"]


def test_pass_with_no_expected_name():
    """When the CSV carried no company to match against, a known employer name read off the feed
    plus a target-region posting is enough."""
    v = seed.identity_verdict("teamtailor", "tractivegmbh", "", expected_name=None,
                              target=seed.EEA, sampler=_sampler(2, "Tractive GmbH", ["AT"]))
    assert v.status == "PASS"


def test_reject_on_name_mismatch_even_with_region_postings():
    """The impostor must be rejected on its name, not rescued by having EEA postings — a US
    company with a Warsaw office would otherwise slip through a country-only check."""
    v = seed.identity_verdict("greenhouse", "eon", "", expected_name="E.ON Energie",
                              target=seed.EEA, sampler=_sampler(9, "Eon Studio", ["DE", "PL"]))
    assert v.status == "REJECT"
    assert "mismatch" in v.reason


def test_review_when_ats_exposes_no_employer_name():
    """lever and ashby give locations but no organisation name, so identity cannot be proven —
    that is a REVIEW (a human read), never a PASS."""
    v = seed.identity_verdict("ashby", "someslug", "", expected_name=None,
                              target=seed.EEA, sampler=_sampler(5, "", ["CZ"]))
    assert v.status == "REVIEW"


def test_review_when_no_posting_in_target_region():
    v = seed.identity_verdict("teamtailor", "acme", "", expected_name=None,
                              target={"CZ"}, sampler=_sampler(6, "Acme US", ["US", "GB"]))
    assert v.status == "REVIEW"
    assert "US" in v.reason and "GB" in v.reason


def test_country_target_narrows_to_cz():
    """A Polish-only board is a PASS EEA-wide but a REVIEW when the target is CZ specifically."""
    pl = _sampler(3, "Netguru", ["PL"])
    assert seed.identity_verdict("workable", "netguru", "", None, seed.EEA, pl).status == "PASS"
    assert seed.identity_verdict("workable", "netguru", "", None, {"CZ"}, pl).status == "REVIEW"


def test_review_when_board_has_no_postings():
    v = seed.identity_verdict("workable", "dark", "", expected_name="Dark Co",
                              target=seed.EEA, sampler=_sampler(0, "", []))
    assert v.status == "REVIEW"


# --- verify_csv: the discover_ats-CSV flow -------------------------------------------------

def test_verify_csv_triages_a_discover_ats_csv(monkeypatch, tmp_path):
    """End to end over a CSV: a matching board PASSes, an impostor REJECTs, an unsupported/dead
    row is skipped. `board_identity` is monkeypatched (resolved at call time, not bound as a
    default), so no network is touched."""
    boards = {
        "roi-hunter": seed.Board(4, "ROI Hunter", ["CZ"]),
        "eon": seed.Board(9, "Eon Studio", ["DE"]),          # impostor: probed E.ON Energie
    }
    monkeypatch.setattr(seed, "board_identity",
                        lambda ats, token, extra="": boards.get(token, seed.Board(-1, "", [])))

    csvp = tmp_path / "hits.csv"
    csvp.write_text(
        "company,domain,tier,ats,token,extra,jobs,supported,found_on,jsonld\n"
        "ROI Hunter,roihunter.com,cz,workable,roi-hunter,,4,yes,,\n"
        "E.ON Energie,eon.cz,cz,greenhouse,eon,,9,yes,,\n"
        "Somebody,x.com,cz,personio,foo,,5,no,,\n"          # unsupported ATS — skipped
        "Deadco,y.com,cz,workable,dead,,0,yes,,\n",         # jobs=0 — skipped
        encoding="utf-8")

    rows = seed.verify_csv(str(csvp), seed.EEA)
    by = {r.token: r.verdict.status for r in rows}
    assert by == {"roi-hunter": "PASS", "eon": "REJECT"}    # the other two never reach a verdict


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
