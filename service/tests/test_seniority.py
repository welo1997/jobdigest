"""`search_jobs.seniority()` — six levels and an honest null.

**Every title in this file is real production text**, sampled from the live corpus on
2026-08-12 (128 080 active postings). The false-positive cases are not invented: each one is a
title the *previous* three-value classifier got wrong, at a measured volume. That is the same
rule `test_education.py` follows, and for the same reason — a guard written from imagination
tests the imagination.

The measurements this file encodes, in descending order of how much they cost:

  62.2%   of active titles contain no level word at all. That is what NULL is for; the old
          scheme stored them as `mid`, which is why `mid` was 64.4% of the corpus. The column
          came out at **70.7% NULL** after the backfill rather than 62.2%, because the
          `manager`-only and `associate`-only rows below also stopped claiming a level.
  11 210  active titles (8.8%) carried `manager` as their ONLY level signal and were all
          stored `senior` — hiding *Assistant Manager* and *Account Manager* from every
          junior/mid filter.
   8 165  carry a genuine leadership word and no senior word: `lead` is a real bucket.
   1 663  carry `staff`. Suspected of the UK "Staff Nurse" false positive; the corpus refuted
          it — all of them are senior IC roles. The token stays, and this file pins that the
          refutation was checked rather than assumed.
"""

import pytest

from search_jobs import SENIORITY_LEVELS, seniority, seniority_stated


# --------------------------------------------------------- the six rungs ---------

@pytest.mark.parametrize("title,expected", [
    # intern — a placement. The boundary against entry_level is contract shape, not experience.
    ("Werkstudent Data Analytics (m/w/d)", "intern"),
    ("Praktikum im Bereich Softwareentwicklung", "intern"),
    ("Stáž — datový analytik", "intern"),
    ("Software Engineering Intern, Summer 2026", "intern"),
    ("Internship - Business Intelligence", "intern"),
    ("Stagiaire développeur back-end", "intern"),
    ("Apprentice Electrician", "intern"),

    # entry_level — a first *permanent* job.
    ("Graduate Data Engineer", "entry_level"),
    ("Entry Level Sales Representative", "entry_level"),
    ("Absolvent — junior kontrolor kvality", "entry_level"),
    ("Trainee Program Finance 2026", "entry_level"),
    ("Berufseinsteiger Vertrieb (m/w/d)", "entry_level"),

    # junior
    ("Junior Data Analyst", "junior"),
    ("Jr. Frontend Developer", "junior"),

    # mid — explicitly stated, which is ~1% of the corpus and that is the honest number.
    ("Mid Level Software Developer", "mid"),
    ("Medior Java Developer", "mid"),
    ("Machine Learning Engineer II (Servicing ML)", "mid"),
    ("Software Engineer II, Messaging Infrastructure", "mid"),

    # senior — individual contributor.
    ("Senior Data Engineer", "senior"),
    ("Staff Software Engineer", "senior"),
    ("Member of Technical Staff", "senior"),
    ("Principal Product Designer", "senior"),
    ("Solutions Architect - Cloud", "senior"),
    ("Software Engineer III", "senior"),

    # lead — people leadership, a different axis from senior IC.
    ("Lead Backend Engineer", "lead"),
    ("Team Lead, Email Deliverability", "lead"),
    ("Head of Data", "lead"),
    ("Director SW Quality - Consumer Applications", "lead"),
    ("Vice President, POM Product Management", "lead"),
    ("Vedoucí oddělení logistiky", "lead"),
    ("Engineering Manager, Payments", "lead"),
])
def test_the_six_levels(title, expected):
    assert seniority(title) == expected


# ------------------------------------------- what the old scheme got wrong -------

@pytest.mark.parametrize("title", [
    # 11 210 active rows carried `manager` as their only level word and were stored `senior`.
    # A bare "manager" names a job, not a rung — and the third of these is the clearest proof:
    # an *assistant* manager was being filtered as a senior hire.
    "Account Manager",
    "Product Manager",
    "Territory Sales Manager (Mid-Market)",
    "Data Scientist - Assistant Manager",
    "International Key Account Manager",
    "Service Delivery Manager",
    "Manager, Employee Listening",

    # `associate` was in the junior pattern and junior was tested first, so this classified as
    # junior. In banking and consulting an Associate is mid-level, in retail it is entry — it
    # is ambiguous, so it now says nothing rather than guessing.
    "Associate Consultant, Strategy",

    # "Mid-Market" and "Mid-Enterprise" are sales *segments*. Same class of trap as the
    # `georgia` rule in CLAUDE.md: a level word that is also an ordinary business word.
    "Account Executive, Mid Market",
    "Mid-Market Account Executive - DACH",
    "Account Executive, Mid-Enterprise",

    # `graduate` also names a *field* of work. 70% of titles say nothing at all, and these
    # must join them rather than be read as junior hires.
    "Graduate Recruitment Consultant",
    "Grad Hiring Partner",

    # And the ordinary majority: titles that simply never mention a level.
    "2D Grafik marketing",
    "Butikssäljare, fackhandel",
    "Sjuksköterska till akutmottagningen",
    "Účetní",
    "Data Engineer",
])
def test_titles_that_name_no_level_are_null(title):
    """NULL is the majority answer and is not a failure — it means "the title did not say".

    The AI matcher is told explicitly that a missing level is not a mismatch, so these reach a
    subscriber's shortlist and are judged on overall fit. What must never happen is a *guess*
    being stored here, because the display filters on /jobs and /matches treat the stored value
    as something the employer asserted."""
    assert seniority(title) is None


def test_associate_no_longer_drags_a_title_down_to_junior():
    """The two production titles that exposed the ordering bug: `associate` sat in the junior
    pattern and junior was tested first, so a Director and a level-2 role both classified as
    junior hires.

    Note the second one resolves to `mid` rather than to NULL — dropping `associate` does not
    mean dropping the title, it means letting the *other* signal in it be heard. "II" is a
    real stated level (854 active rows carry a roman numeral and nothing else), and it is now
    the one doing the work here instead of being overruled."""
    assert seniority("Associate Director, FSQA") == "lead"
    assert seniority("Production Associate II") == "mid"


def test_staff_nurse_suspicion_was_measured_not_assumed():
    """`staff` was suspected of a UK "Staff Nurse means an ordinary nurse" false positive and
    the corpus refuted it: all 1 663 occurrences are senior IC roles, so the token stays.

    This test exists to record that the check happened. If a future corpus does start carrying
    clinical titles, this is where the evidence goes — and the fix is a scoped exclusion, not
    deleting a token that is carrying 1 663 correct rows."""
    assert seniority("Staff Software Engineer") == "senior"
    assert seniority("Staff+ Software Engineer, Inference Runtime") == "senior"


# ------------------------------------------------------------- precedence --------

def test_leadership_outranks_every_other_signal():
    """Big buckets must not leak into small ones. `lead` and `senior` together are ~19% of the
    corpus while `entry_level` is ~0.3%, so a title mentioning both has to resolve to the
    former or the small bucket fills with rows from the large one."""
    assert seniority("Head of Graduate Recruitment") == "lead"
    assert seniority("Senior Engineering Manager") == "lead"
    assert seniority("Director of Internship Programs") == "lead"


def test_below_leadership_the_lowest_stated_rung_wins():
    """"Junior/Medior" is an employer stating a floor, and the floor is the bar they set."""
    assert seniority("Cloud Engineer (Junior/Medior) - Near-Site (Belgium)") == "junior"
    assert seniority("Junior / Mid-level Scala Developer") == "junior"


def test_an_empty_or_missing_title_is_null_not_a_crash():
    assert seniority(None) is None
    assert seniority("") is None


# ------------------------------------------------------ the shape of the API -----

def test_seniority_only_ever_returns_a_known_level_or_none():
    """The stored value reaches SQL filters, eight message catalogues and the matcher prompt.
    A seventh string appearing here would render as a raw code on the site and match nothing."""
    titles = ["Senior Data Engineer", "Werkstudent", "Head of Data", "Account Manager",
              "Graduate Analyst", "Engineer II", "Účetní", "", None]
    for t in titles:
        assert seniority(t) is None or seniority(t) in SENIORITY_LEVELS


def test_seniority_stated_agrees_with_the_classifier_by_construction():
    """It used to be a second regex pass that could disagree with the first; it is now the
    trivial consequence of the column being able to say "I don't know"."""
    for t in ["Senior Data Engineer", "2D Grafik marketing", "Werkstudent", "", None]:
        assert seniority_stated(t) is (seniority(t) is not None)


def test_null_is_not_offered_as_a_level():
    """"Unstated" is the absence of an answer, not a seventh rung. If it ever enters
    `SENIORITY_LEVELS` it becomes a chip inviting a visitor to search for silence, and a value
    `profiles.seniorities` could store as a preference."""
    assert None not in SENIORITY_LEVELS
    assert "unstated" not in SENIORITY_LEVELS
    assert list(SENIORITY_LEVELS) == ["intern", "entry_level", "junior", "mid", "senior", "lead"]
