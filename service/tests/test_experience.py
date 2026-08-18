"""`service/experience.py` — the years-of-experience classifier and its gate.

Every false-positive case here is real production text (or a minimal cut of one) that
classified wrongly under an earlier draft of the patterns — the same rule as
`test_education.py`. The classifier's only safe error is a miss: a wrong number deletes a
job from someone's digest for a requirement the ad never stated.
"""

import pytest

from service import experience

# ------------------------------------------------------------- real requirements ---

@pytest.mark.parametrize("text,years", [
    # The bread-and-butter shapes, all from live greenhouse/ashby descriptions.
    ("Requirements: 5 years of professional software development experience and proficiency", 5),
    ("you have 8 years of relevant professional experience building scalable distributed", 8),
    ("Requirements 2+ years of professional experience supporting end-to-end project", 2),
    ("Minimum of 3 years of experience as vehicle technician, extensive knowledge", 3),
    # A range demands its floor: 5-7 arrives normalised as "5 7".
    ("senior product management experience: 5-7 years of product management experience", 5),
    ("must haves: 2-5 years of experience in IT engineering, platform engineering", 2),
    # Written-out number.
    ("at least three years of experience with Kubernetes in production", 3),
    # Tenure shapes without the word "experience" — the preposition or gerund arm.
    ("8 years as an HRBP or generalist, including global and multinational scope", 8),
    ("2 years in technical support or professional services consulting", 2),
    ("4 years selling to large global banking organizations", 4),
    ("10 years shipping production software with a strong track record", 10),
    # Adjective arm: "experience" sits beyond any sane window.
    ("minimum requirements: 8 years of relevant product management, engineering or", 8),
    ("you have 7 years of hands-on penetration testing, product security auditing", 7),
    # The requirements header overrides boilerplate softeners from the previous bullet —
    # greenhouse's "…is a bonus, not a requirement. Minimum requirements: 5 years…".
    ("is a bonus, not a requirement. Minimum requirements: 5 years of experience in risk", 5),
    ("bonus points for Go. Requirements: 2 years of relevant experience in backend work", 2),
    # Czech, German, French — one definition across the catalogue languages.
    ("Požadujeme min. 3 roky praxe na obdobné pozici", 3),
    ("mindestens 3 Jahre Berufserfahrung in der Logistik oder Lagerverwaltung", 3),
    ("avec une expérience d'au moins 3 ans en tant que Head of HRBP", 3),
    # Several tenures: the lowest surviving one wins.
    ("6 years of management experience including 2 years managing managers", 2),
])
def test_real_requirements_classify(text, years):
    assert experience.classify_requirement(text) == years


# ---------------------------------------------------------- not a requirement ------

@pytest.mark.parametrize("text", [
    # Employer blurbs — the grammar of a requirement, the meaning of an anniversary.
    "We have 25 years of experience delivering enterprise solutions",
    "our 20 years of experience in the industry make us a market leader",
    "the company looks back on 30 Jahre Erfahrung",
    "s 25 lety zkušeností jsme jedničkou na trhu",
    # The cap is itself a blurb guard: genuine demands above 15 do not exist here.
    "with over 20 years of experience serving customers worldwide",
    # Softened mentions demand nothing.
    "5+ years of experience preferred",
    "what we consider as an advantage: 4 years of experience in pricing",
    "ideally you have 6 years of experience in running strong backend teams",
    "MBA a plus. 5 years of experience welcome but not required",
    # Ceilings and horizons are not floors.
    "up to 5 years of relevant experience",
    "you will set the 3-5 year technical strategy and architectural blueprint",
    "an ongoing forward-looking roadmap for our next 2-3 years",
    "in the last 10 years in the UK we grew our personal banking arm",
    # Durations, ages, benefits, streaks — a number of years measuring something else.
    "a 2 year fixed term contract with possible extension",
    "3 years of warranty on all hardware we ship",
    "applicants must be 18 years or older",
    "paid month off after 4 years, every 2 years thereafter",
    "recognised with Great Place to Work certification three years in a row",
    "in 3 years we grew from 2 engineers to over 30 engineers",
    # A number+years with no tenure signal at all stays unread.
    "the programme spans 2 years across three rotations",
])
def test_non_requirements_decline(text):
    assert experience.classify_requirement(text) is None


def test_false_positive_guards_do_not_overreach():
    """The guard words must not kill requirements that merely contain them.

    All three were wrongly declined by a draft that searched `\\bbusiness\\b`, `\\bour\\b`
    and a windowed `\\bstrategy\\b` — real postings from the validation sample.
    """
    assert experience.classify_requirement(
        "required skills: 4 years in business intelligence, data analytics or similar") == 4
    assert experience.classify_requirement(
        "working with our licensing vendor. 3 years experience in primary care or urgent care") == 3
    assert experience.classify_requirement(
        "you have 8 years of experience in operations strategy and consulting") == 8


def test_empty_and_missing_text():
    assert experience.classify_requirement(None) is None
    assert experience.classify_requirement("") is None
    assert experience.classify_requirement("", title="Senior Engineer") is None


# ------------------------------------------------------------- preference values ---

@pytest.mark.parametrize("value,expected", [
    (3, 3), ("3", 3), (0, 0), (50, 50), (99, 50), (-2, 0),
    (None, None), ("", None), ("  ", None), ("abc", None), ([], None),
])
def test_clean_years(value, expected):
    assert experience.clean_years(value) == expected


def test_describe():
    assert experience.describe(None) is None
    assert experience.describe("") is None
    assert "3 years" in experience.describe(3)
    assert "1 year " in experience.describe(1)


# ---------------------------------------------------------------------- the gate ---

def test_predicate_silence_widens():
    """A profile that never stated its years gets no filter at all."""
    assert experience.experience_predicate({}) == ("true", [])
    assert experience.experience_predicate({"years_experience": None}) == ("true", [])
    assert experience.experience_predicate({"years_experience": "junk"}) == ("true", [])


def test_predicate_null_posting_passes():
    """The fragment must keep every posting whose requirement is unknown."""
    sql, params = experience.experience_predicate({"years_experience": 3})
    assert "experience_min is null" in sql
    assert params == [3]
