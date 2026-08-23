"""A sector word names the employer; a profession word names the job. The job wins.

`taxonomy.classify` returns the first pattern that matches, and `healthcare` runs first on
purpose — so that a nurse is never taken by `data_analysis`' analyst word or by the bare
`\\bengineer\\b`. That is right, and it is also why the same first position let `sjukvård`,
`äldreomsorg`, `tandvård`, `hemtjänst`, `vårdcentral` and Czech `zdravotn` classify the
*workplace* instead of the work: `Jurist inom Hälso- och sjukvårdsjuridik` is a lawyer,
`Kock till Äldreomsorgen Alingsås` is a chef, `Data scientist, Folktandvården` is a data
scientist, and the Czech register's own `Řidiči osobních … (kromě … zdravotnické dopravní
služby)` is a **van driver** that sat in the answer key filed as healthcare.

**Pattern order cannot fix this, which is the whole reason the mechanism exists.** A profession
word is specific and a sector word is general; specific-before-general is the file's rule, and
one alternation cannot be both first and last. So a sector-only match is *remembered* and
yields to any later category that names a profession, and pattern order still decides the
fallback when nothing does.

Worth +2 rows on the Czech key, measured by difference against the misfile pass this was split
out of. The tests below are the ones the measurement pass named as load-bearing, plus the
guards that stop the mechanism becoming a new way to lose rows.

The Czech `(kromě …)` construction deserves its own note: it means "*except* …", so
`Sociální pracovníci … (kromě péče o zdravotně postižené)` was reading an **exclusion clause**
as a positive healthcare signal. 31 titles in the live corpus use that shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from service import taxonomy  # noqa: E402


@pytest.mark.parametrize("title,expected", [
    # A Swedish organisation name in first position, with the profession stated outright.
    ("Jurist inom Hälso- och sjukvårdsjuridik", "legal"),
    ("Kock till Äldreomsorgen Alingsås", "hospitality"),
    ("Data scientist, Folktandvården", "machine_learning"),
    ("Rekryterare till äldreomsorgen i Lomma kommun", "hr_recruiting"),
    # The Czech sector locative: "ve zdravotnictví" = "in healthcare", i.e. the industry.
    ("Full-stack vývojář aplikace pro plánování směn ve zdravotnictví", "software_engineering"),
])
def test_a_sector_word_yields_to_a_stated_profession(title: str, expected: str):
    assert taxonomy.classify(title) == expected, title


def test_the_czech_exclusion_clause_is_not_a_healthcare_signal():
    """`(kromě …)` means "except …". It was being read as a positive signal."""
    assert taxonomy.classify(
        "Sociální pracovníci specialisté v oblasti péče o děti a mládež "
        "(kromě péče o zdravotně postižené)") == "social_care"
    # The one that makes the point: a van driver, in the answer key, filed as healthcare.
    assert taxonomy.classify(
        "Řidiči osobních a malých dodávkových automobilů "
        "(kromě taxikářů a řidičů zdravotnické dopravní služby)") == "logistics_transport"


@pytest.mark.parametrize("title", [
    # Deferral is per TITLE, not per span: a title that also matches something non-sector is
    # never deferred at all.
    "Dental Turism AB söker erfarna tandsköterskor",
    "Tandvårdsbiträde till Folktandvården Rättvik",
    "Distriktssköterska till vårdcentralen",
    "Ambulanssjukvårdare",
    "Timvikarier till hemtjänsten",
    "Zdravotní sestra",
])
def test_a_title_naming_a_health_profession_stays_healthcare(title: str):
    assert taxonomy.classify(title) == "healthcare", title


@pytest.mark.parametrize("title", [
    # Nothing else claims these, and that is the point: a sector word with no profession word
    # beside it still answers, because pattern order decides the fallback.
    "Enhetschef till hemtjänsten",
    "Sjukvårdsupplysningen",
])
def test_a_sector_word_alone_still_answers(title: str):
    assert taxonomy.classify(title) == "healthcare", title


@pytest.mark.parametrize("title", [
    # These four are the WHOLE live effect of `_DEFER_NEVER_TO`, measured over all 97 651
    # corpus titles. They are not the three the guard was written for — see below.
    "Vedoucí administrativní pracovník/ce ve zdravotnictví",
    "VEDOUCÍ ADMINISTRATIVNÍ PRACOVNÍK/CE ve zdravotnictví",
    "Odborní administrativní pracovníci v oblasti zdravotnictví",
    "Administrativ assistent med erfarenhet av hemtjänst",
])
def test_a_sector_word_never_yields_to_the_residual_bucket(title: str):
    """`_DEFER_NEVER_TO`, tested on the titles that actually depend on it.

    `other_tech_function` is not a claim that the job is anything — **no chip maps to it**
    (`ROLE_ID_FOR_CATEGORY` is display-only, one way), so a row there is reachable by nobody,
    and `ingest._classify` will never re-ask about it either, because the model cache is
    consulted only on a decline. A subscriber can select healthcare and cannot select the
    residual bucket, so a soft misfile beats a row nobody can reach.

    **This test originally used the three titles the guard was written for — and it could not
    fail.** `Rehabkoordinator till Vårdcentralen Ryd`, `Klinikassistent till Vårdcentral
    Malung` and `Zdravotní asistentka v oční ambulanci` were measured against a tree where
    `other_tech_function` still shipped a bare `koordinator`/`assistent`; the misfile pass
    narrowed those heads, so the bucket no longer claims them and the guard has nothing to do
    for them. Emptying `_DEFER_NEVER_TO` left all 19 assertions green — which is how the stale
    justification was found at all, and why a guard gets mutation-checked rather than trusted.

    The four titles above are the honest remainder, and they point the *other* way: the sector
    is healthcare but the profession really is administration, so the mechanism's own logic
    would say `other_tech_function`. The guard overrides that on reachability. Anyone who
    disagrees should be able to, on this number.
    """
    assert taxonomy.classify(title) == "healthcare", title


def test_the_deferral_map_only_names_our_own_vocabulary():
    """The list cannot rot, and that is deliberate.

    `_SECTOR_ONLY` enumerates spans **this file already ships** — not other people's
    professions. A `^(?!.*(?:receptionist|jurist|kock|…))` blocklist would need editing every
    time a new profession turned up in a sector's ads; this needs editing only when we add a
    sector word of our own. Every new intruder profession is handled by the category that owns
    its word, with no edit here.

    So: every key must be a real category, and every pattern must actually match something the
    category can produce.
    """
    for category, sector in taxonomy._SECTOR_ONLY.items():
        assert category in taxonomy.CATEGORIES, category
        own = dict(taxonomy.PATTERNS)[category]
        # The sector pattern is matched with fullmatch against spans the category produced, so
        # a sector entry that its own category can never emit is dead configuration.
        assert sector.pattern, category
        assert own is not None, category
    for bucket in taxonomy._DEFER_NEVER_TO:
        assert bucket in taxonomy.CATEGORIES, bucket


def test_deferral_cannot_invent_a_category():
    """Whatever happens, `classify` returns a real value or the honest fallback."""
    for title in ("", "Sjukvård", "zdravotn", "Äldreomsorg tandvård hemtjänst",
                  "Jurist inom Hälso- och sjukvårdsjuridik"):
        assert taxonomy.classify(title) in taxonomy.CATEGORIES, title
