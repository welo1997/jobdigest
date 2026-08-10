"""Skills facet: canonicalisation, word boundaries, and the ambiguous-token guards.

`service/skills.py` is the one definition of the skills/tech-stack search axis, and — exactly
like `test_education.py` — **most of these tests assert that something is NOT tagged.** That
is the point. A skill facet is additive and forgiving (a missed tag costs a filter hit; it
never emails anyone), so the only error worth engineering against is the *false positive*: the
everyday word "go", "excel", "swift", "spring", "rest" tagged as a technology it isn't.

Every guarded skill below is pinned by the trap sentence that would fire it without its guard,
and each is mutation-checkable: delete the guard (move the variant from `guarded` to
`variants`, or drop its `anchor`) and the corresponding "trap" test goes red. The boundary
tests are the other half — `java`⊄`javascript`, `git`⊄`github`, `sql`⊄`postgresql`.
"""

from __future__ import annotations

import pytest

from service import skills

# ------------------------------------------------------------- canonicalisation ---


@pytest.mark.parametrize(
    "text,canonical",
    [
        ("Senior Python3 engineer", "python"),
        ("Strong JavaScript / JS skills", "javascript"),
        ("Deploy on k8s", "kubernetes"),
        ("We use Postgres in production", "postgresql"),
        ("Experience with mongo", "mongodb"),
        ("sklearn for modelling", "scikit-learn"),
        ("ReactJS front end", "react"),
        ("Amazon Web Services", "aws"),
        ("Google Cloud Platform", "gcp"),
        ("PowerBI dashboards", "power_bi"),
        ("tailwindcss styling", "tailwind"),
    ],
)
def test_variants_map_to_one_canonical_skill(text, canonical):
    assert skills.extract_skills(text, None) == [canonical]


def test_a_js_framework_also_asserts_the_javascript_language():
    """The `.js` suffix in a framework name (next.js, node.js, vue.js) legitimately carries the
    JavaScript language — these frameworks *are* JavaScript, so a subscriber filtering on
    `javascript` should find them. This is intended co-occurrence, not a boundary leak.
    """
    got = skills.extract_skills("Frontend Engineer", "Building with Next.js")
    assert "nextjs" in got and "javascript" in got


def test_the_title_is_scanned_not_only_the_description():
    """Many postings are a bare title with no body — a facet that read only the description
    would tag nothing for most of the corpus."""
    assert skills.extract_skills("Python Developer", None) == ["python"]
    assert skills.extract_skills("Python Developer", "") == ["python"]


def test_no_skill_is_a_first_class_empty_list():
    """`[]` must be returned, never None, so the storage column and every future gate can treat
    'no skills named' as 'no constraint' — the same null-passes rule as education/work_mode.
    """
    assert skills.extract_skills(None, None) == []
    assert skills.extract_skills("", "") == []
    assert skills.extract_skills("Account Manager", "Join our friendly team!") == []


# --------------------------------------------------------------- word boundaries ---
# A skill token must be a whole word: the classic substring collisions, each a real hazard.


@pytest.mark.parametrize(
    "text,absent",
    [
        ("Frontend javascript developer", "java"),  # java ⊄ javascript
        ("We build scalable, high-scalability systems", "scala"),
        ("A trustworthy and robust process", "rust"),
        ("Use GitHub Actions for CI", "git"),  # git ⊄ github
        ("NoSQL and postgresql stores", "sql"),  # sql ⊄ nosql / postgresql
        ("Great with people, a real go-getter mindset", "go"),
        ("helmet and safety gear provided", "helm"),
    ],
)
def test_a_skill_never_matches_inside_a_longer_word(text, absent):
    assert absent not in skills.extract_skills(text, None)


def test_java_and_javascript_are_told_apart_in_the_same_ad():
    got = skills.extract_skills("Java and JavaScript both used", None)
    assert "java" in got and "javascript" in got


# ----------------------------------------------------- the ambiguous-token guards ---
# Each pair: the everyday-word trap must NOT tag; the technical use MUST. Removing the guard
# (its anchor, or moving the token to `variants`) makes the first assertion fail.


@pytest.mark.parametrize(
    "trap",
    [
        "We move fast, so go get results and go the extra mile.",
        "A self-starter who will go far in this role.",
    ],
)
def test_go_the_verb_is_not_the_language(trap):
    assert "go" not in skills.extract_skills("Sales Representative", trap)


@pytest.mark.parametrize(
    "real",
    [
        "Backend written in Go, comfortable with goroutines.",
        "Golang microservices with gin and gorm.",
    ],
)
def test_go_the_language_is_tagged_when_anchored(real):
    assert "go" in skills.extract_skills("Backend Engineer", real)


def test_r_the_letter_is_not_the_language():
    assert "r" not in skills.extract_skills(
        "Engineer", "Join our R&D team building products."
    )
    assert "r" not in skills.extract_skills(
        "Manager", "Section R of the handbook applies."
    )


def test_r_the_language_is_tagged_near_statistical_context():
    assert "r" in skills.extract_skills(
        "Data Analyst", "Statistical modelling in R using tidyverse and ggplot."
    )


def test_excel_the_verb_is_not_the_tool():
    assert "excel" not in skills.extract_skills(
        "Manager", "You will excel in a fast-paced, dynamic environment."
    )


def test_excel_the_tool_is_tagged_with_spreadsheet_context():
    assert "excel" in skills.extract_skills(
        "Analyst", "Advanced Excel including pivot tables and VLOOKUP."
    )


def test_swift_the_adjective_is_not_the_language():
    assert "swift" not in skills.extract_skills(
        "Support Agent", "We pride ourselves on a swift response to every customer."
    )


def test_swift_the_language_is_tagged_in_ios_context():
    assert "swift" in skills.extract_skills(
        "iOS Developer", "Swift and SwiftUI, built in Xcode."
    )


def test_spring_the_season_is_not_the_framework():
    assert "spring" not in skills.extract_skills(
        "Intern", "Starting in the spring, you will join a growing team."
    )


def test_spring_boot_and_java_spring_are_tagged():
    assert "spring" in skills.extract_skills(
        "Java Dev", "Spring Boot microservices in Java."
    )


def test_rest_the_word_is_not_the_api_style():
    assert "rest" not in skills.extract_skills(
        "Lead", "You will support the rest of the team through the transition."
    )


def test_rest_the_api_is_tagged():
    assert "rest" in skills.extract_skills(
        "Backend Dev", "Design a REST API returning JSON."
    )


def test_sap_the_word_is_not_the_erp():
    # "sap" appears as an ordinary word in DE/NL descriptions; only ERP context should tag it.
    assert "sap" not in skills.extract_skills(
        "Forester", "Managing tree sap collection routes."
    )


def test_sap_the_erp_is_tagged():
    assert "sap" in skills.extract_skills(
        "Consultant", "SAP ERP implementation with ABAP."
    )


def test_spark_the_word_is_not_apache_spark():
    assert "spark" not in skills.extract_skills(
        "Designer", "We build products that spark joy and curiosity."
    )


def test_apache_spark_is_tagged():
    assert "spark" in skills.extract_skills(
        "Data Engineer", "Big data with Apache Spark."
    )


def test_node_the_word_is_not_nodejs():
    assert "nodejs" not in skills.extract_skills(
        "Analyst", "Each node in the network graph is scored independently."
    )


def test_nodejs_is_tagged_in_js_context():
    assert "nodejs" in skills.extract_skills(
        "Backend", "Node.js API with Express and npm."
    )


# --------------------------------------------------------------- punctuation names ---


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Strong C++/CUDA experience", "cpp"),
        ("cpp and modern tooling", "cpp"),
        (".NET Core and C#-adjacent stacks", "dotnet"),
        ("asp.net web apps", "dotnet"),
    ],
)
def test_tokens_with_symbols_survive_normalisation(text, expected):
    assert expected in skills.extract_skills(text, None)


# --------------------------------------------------------------- shape guarantees ---


def test_result_is_sorted_and_deduplicated():
    got = skills.extract_skills(
        "Full-stack Engineer",
        "Python and Python3 with Django; React and ReactJS on the frontend; Docker, k8s.",
    )
    assert got == sorted(got)
    assert len(got) == len(set(got))
    assert "python" in got and got.count("python") == 1


def test_result_is_capped():
    stuffed = " ".join(skills.canonical_skills() * 2) + " python kubernetes docker aws"
    got = skills.extract_skills("Everything Engineer", stuffed)
    assert len(got) <= skills.MAX_SKILLS


# --------------------------------------------------------------- gazetteer integrity ---


def test_canonical_skills_are_unique():
    vocab = skills.canonical_skills()
    assert len(vocab) == len(set(vocab))


def test_every_canonical_has_a_category():
    for c in skills.canonical_skills():
        assert skills.SKILL_CATEGORY.get(c), c


def test_guarded_skills_declare_an_anchor_and_vice_versa():
    """The `_skill` builder enforces this at import; assert it explicitly so the invariant is
    documented and a future hand-edit that bypasses the builder is still caught here."""
    for s in skills._GAZETTEER:
        assert bool(s.guarded) == bool(s.anchor), s.canonical
        assert s.variants or s.guarded, s.canonical
