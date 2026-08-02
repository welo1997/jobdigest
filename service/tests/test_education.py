"""Education requirements: classification, the widening rule, and the frontend mirror.

`service/education.py` is the one definition of what a job may demand and what a subscriber
will accept, and four consumers depend on it agreeing with itself:

  - ingest and `service/backfill_education.py`, which write `postings.education_min`;
  - `store.query_shortlist_meta`, whose SQL gate is built from the same vocabulary;
  - `service/matcher.py`, which describes the preference to the model in prose;
  - `web/lib/education.ts`, which the browser uses to offer the choices — it cannot import
    Python, so a level that exists on one side only is a form that cannot be saved.

**Most of these tests assert that something is NOT classified.** That is the point. This
column is null for ~97% of live postings and for 100% of the Czech and Slovak inventory, and
the gate keeps nulls — so the expensive failure is never a missed requirement, it is an
invented one, which silently deletes a job from somebody's digest for a qualification the ad
never demanded. The false-positive cases below are all real text taken from production
postings on 2026-08-02, and every one of them classified *wrongly* before the rule it pins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from service import education

ROOT = Path(__file__).resolve().parents[2]
WEB_EDU = ROOT / "web" / "lib" / "education.ts"


# ------------------------------------------------------------- classification ---

@pytest.mark.parametrize("text,expected", [
    ("Bachelor's degree in Computer Science is required", "bachelor"),
    ("Requirements: BSc in Engineering", "bachelor"),
    ("A degree in Computer Science or a related technical field", "bachelor"),
    ("Erfolgreich abgeschlossenes Studium der Informatik", "bachelor"),
    ("Master's degree in applied mathematics, statistics or computer science", "master"),
    ("Doctorate in molecular biology", "doctorate"),
    ("Abgeschlossene Ausbildung als Bankkaufmann", "vocational"),
    ("High school diploma or GED and a minimum of 8 years of experience", "secondary"),
])
def test_a_stated_requirement_is_read_at_its_level(text, expected):
    assert education.classify_requirement(text) == expected


def test_an_unstated_requirement_is_none_and_that_is_the_common_case():
    """Null is the answer for ~97% of live postings and is load-bearing: `education_predicate`
    keeps nulls, so this is what stops the filter deleting most of the corpus."""
    assert education.classify_requirement("We are looking for an analyst to join our team.") is None
    assert education.classify_requirement("") is None
    assert education.classify_requirement(None) is None
    # The whole Czech/Slovak inventory looks like this — a scrap, or nothing at all.
    assert education.classify_requirement("70 000 – 80 000 Kč") is None
    assert education.classify_requirement("Odpověď do 2 týdnů") is None


def test_the_lowest_stated_level_wins_because_that_is_what_the_ad_is_satisfied_by():
    """An ad naming several is satisfied by the lowest. Taking the lowest also caps the damage
    of any employer blurb the `_BLURB` rules miss."""
    assert education.classify_requirement(
        "BS, MS, PhD in computer science or a related field") == "bachelor"
    assert education.classify_requirement(
        "MS or PhD in mechanical engineering, materials science or physics") == "master"
    assert education.classify_requirement(
        "Bachelor's or Master's degree in economics") == "bachelor"


# --------------------------------------------------- what must NOT be classified ---
# Every string below is real text from a production posting. Each one produced a *wrong*
# requirement before the rule it pins, and a wrong requirement here is a job silently missing
# from a subscriber's digest.

@pytest.mark.parametrize("text,why", [
    ("Bachelor's degree or equivalent working experience and 5+ years in finance",
     "'or equivalent' — 286 of the 952 postings that name a degree soften it this way"),
    ("Bachelor's Degree in Business or related field preferred.",
     "'preferred' is a preference, not a requirement"),
    ("Bachelor's degree in Construction Management, Engineering, Architecture, "
     "or a related technical discipline, or equivalent",
     "the softener sits 90 characters past the word — the window has to reach it"),
    ("Abgeschlossene Ausbildung als KFZ-Mechatroniker oder eine ähnliche Qualifikation",
     "'oder eine ähnliche Qualifikation' is German for 'or equivalent'"),
    ("Ein bestimmter Studienabschluss ist für uns nicht entscheidend",
     "German for 'a particular degree is not decisive for us' — an explicit denial"),
    ("Nemusíš mít doktorát z matiky, ale musíš vědět co je to LLM",
     "Czech for 'you don't need a doctorate' — an explicit denial"),
    ("Currently enrolled in a computer science or related technical master's degree",
     "a working-student ad: enrolment is not a completed qualification"),
    ("we're the state's premier public urban research university with more than 100 "
     "in-demand, top-ranked bachelor's, master's, and doctoral degree programs",
     "a university describing its own degree programmes, not hiring for one"),
    ("Our diverse team hails from 42 nationalities, with backgrounds ranging from "
     "DeFi natives to PhDs",
     "an employer boasting about its staff"),
    ("knowledge is our first ingredient with 220 veterinarians, PhD nutritionists and "
     "food scientists working to develop breakthrough innovations",
     "'PhD' used as a noun for people, not as a qualification"),
    ("research engineers who have spent years inside a production stack and developed "
     "insights no PhD program teaches",
     "'PhD program' is a blurb, not a requirement"),
    ("The role demands adaptability as designs and execution plans drive it to maturity",
     "English 'maturity' — the Czech 'maturita' pattern must not swallow it"),
    ("we identify future state opportunities and recommend maturity of process",
     "same collision, second real occurrence"),
    ("Bachelor's degree or the equivalent combination of other post secondary education",
     "'post secondary' is North American for tertiary — the opposite of what it looks like"),
    ('<img style="width:60px" src="https://cbs-consulting.com/wp/uploads/ausbildung-1.svg">'
     " Aus- und Weiterbildung: SAP und Salesforce Zertifizierungen",
     "a benefits icon's filename — markup must be stripped before the text is read"),
    ("Access a mini MBA with a focus on finance via the Agicap academy",
     "an MBA offered as a perk, not demanded as a qualification"),
])
def test_a_non_requirement_is_never_read_as_one(text, why):
    assert education.classify_requirement(text) is None, why


def test_markup_is_stripped_before_the_text_is_read():
    """Normalising first would flatten a tag's attributes into prose. Pinned separately from
    the case above because it is the *mechanism*, and it protects every pattern at once."""
    assert "ausbildung" not in education.normalise('<img src="/uploads/ausbildung-1.svg">')
    assert "bachelor" in education.normalise("<li>Bachelor's degree</li>")


def test_a_softener_is_scoped_to_the_mention_not_the_whole_document():
    """Checked document-wide, "plus" and "preferred" match nearly every job ad and every
    requirement would read as optional — the filter would be silently inert."""
    far_away = ("Bachelor's degree in Computer Science is required. "
                + "We offer a great culture. " * 20
                + "A dog-friendly office is a plus.")
    assert education.classify_requirement(far_away) == "bachelor"


# ------------------------------------------------------- preference normalising ---

def test_an_empty_or_unrecognised_selection_widens_to_every_level():
    """Unticking every box is "no preference", never "nothing is acceptable". Reading it the
    other way would filter a digest to zero without a single error — the failure this codebase
    keeps meeting. Same rule as `geo.clean_work_modes`."""
    assert education.clean_levels([]) == list(education.LEVELS)
    assert education.clean_levels(None) == list(education.LEVELS)
    assert education.clean_levels(["nonsense"]) == list(education.LEVELS)


def test_levels_are_normalised_into_a_stable_order():
    assert education.clean_levels(["doctorate", "secondary"]) == ["secondary", "doctorate"]
    assert education.clean_levels(["MASTER", " bachelor "]) == ["bachelor", "master"]
    # Spellings a hand-written client might send.
    assert education.clean_levels(["phd", "bsc"]) == ["bachelor", "doctorate"]
    assert education.clean_levels(["high-school", "apprenticeship"]) == ["secondary", "vocational"]


def test_the_field_of_study_is_trimmed_and_capped_but_never_rejected():
    assert education.clean_field("  Economics  ") == "Economics"
    assert education.clean_field("a\n\tb") == "a b"
    assert education.clean_field("") is None
    assert education.clean_field(None) is None
    assert len(education.clean_field("x" * 500)) == education.MAX_FIELD_LEN


# ---------------------------------------------------------------- the SQL gate ---

def test_accepting_every_level_applies_no_filter_at_all():
    """The default, and the state every existing subscriber is migrated into — so nobody's
    digest narrows without them choosing it."""
    sql, params = education.education_predicate({"education_levels": list(education.LEVELS)})
    assert sql == "true" and params == []
    # A profile that predates migration 014 has no such key at all.
    assert education.education_predicate({})[0] == "true"


def test_a_null_requirement_always_passes_the_gate():
    """The single most important line in this module. `education_min` is null for ~97% of
    postings and for every Czech and Slovak one; a gate that dropped nulls would delete almost
    the whole corpus from the digest of anyone who unticked one box."""
    sql, params = education.education_predicate({"education_levels": ["secondary"]})
    assert "education_min is null" in sql
    assert " or " in sql, "the null check must be an OR, not an AND"
    assert params == [["secondary"]]


def test_the_gate_uses_the_caller_supplied_alias():
    sql, _ = education.education_predicate({"education_levels": ["bachelor"]}, alias="x")
    assert "x.education_min" in sql and "p.education_min" not in sql


def test_describe_levels_reads_as_a_sentence_for_the_matcher_prompt():
    assert education.describe_levels(list(education.LEVELS)) == "any education requirement"
    assert education.describe_levels(["secondary"]).startswith("only roles requiring")
    assert "Economics" in education.describe_levels(["bachelor"], "Economics")
    # An empty selection describes as "any", matching what the gate actually does.
    assert education.describe_levels([]) == "any education requirement"


# ------------------------------------------------------------------ web drift ---

def _ts_object(name: str) -> dict:
    """Pull a `const NAME: ... = {...};` object out of education.ts. It is written as JSON, so
    it parses as JSON — which is also the point: hand-editing it into non-JSON is a signal the
    two sides are being maintained separately."""
    text = WEB_EDU.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}[^=]*=\s*(\{{.*?\n\}});", text, re.S)
    assert match, f"{name} not found in {WEB_EDU.name}"
    return json.loads(match.group(1))


def test_frontend_levels_match_the_python_definition():
    """Order as well as content: the browser renders the chips in this order and the values are
    what it POSTs, and a level the API does not know is rejected outright by
    `_check_education_levels` — so drift here is a preferences page that cannot be saved."""
    text = WEB_EDU.read_text(encoding="utf-8")
    match = re.search(r"export const EDUCATION_LEVELS = \[(.*?)\]", text, re.S)
    assert match, "EDUCATION_LEVELS not found in web/lib/education.ts"
    assert tuple(re.findall(r'"([a-z]+)"', match.group(1))) == education.LEVELS


def test_frontend_labels_match_the_python_definition():
    assert _ts_object("EDUCATION_LABEL") == education.LEVEL_LABELS, (
        "web/lib/education.ts and service/education.py disagree about what to call an "
        "education level — the matcher prompt and the form the subscriber filled in would "
        "describe it differently."
    )


def test_frontend_field_cap_matches():
    text = WEB_EDU.read_text(encoding="utf-8")
    match = re.search(r"export const MAX_FIELD_LEN = (\d+)", text)
    assert match, "MAX_FIELD_LEN not found in web/lib/education.ts"
    assert int(match.group(1)) == education.MAX_FIELD_LEN


def test_every_level_has_a_label_and_a_translation_key_in_all_eight_catalogues():
    """A level with no label renders as its raw slug ("doctorate") in the picker. TypeScript
    cannot catch a missing *entry* in a `Record<string, string>` — only this can."""
    assert set(education.LEVEL_LABELS) == set(education.LEVELS)
    catalogues = sorted((ROOT / "web" / "i18n" / "messages").glob("*.ts"))
    assert len(catalogues) == 8, f"expected 8 catalogues, found {len(catalogues)}"
    for path in catalogues:
        text = path.read_text(encoding="utf-8")
        block = re.search(r"educationLevels:\s*\{(.*?)\n  \}", text, re.S)
        assert block, f"{path.name} has no educationLevels section"
        keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
        assert keys == set(education.LEVELS), (
            f"{path.name} educationLevels: missing {set(education.LEVELS) - keys}, "
            f"unexpected {keys - set(education.LEVELS)}"
        )
