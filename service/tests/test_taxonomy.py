"""The role taxonomy has one Python definition — these tests keep the rest in step.

`service/taxonomy.py` is imported by ingest, the shortlist builder, the digest subject
line and the CV parser, so those four cannot drift. Two consumers can't import Python:

  - ``dbt/models/staging/stg_job_postings.yml`` — an `accepted_values` test that runs in
    Snowflake. If a category is added here and not there, `dbt test` fails in CI on real
    data, which is a slow and confusing way to find out.
  - ``web/app/page.tsx`` — the signup form maps display labels to categories. A typo or a
    renamed category here silently produces a profile that matches nothing.

So these tests read those two files as text and assert they agree with `CATEGORIES`.
"""

import re
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
DBT_SCHEMA = ROOT / "dbt" / "models" / "staging" / "stg_job_postings.yml"
WEB_PAGE = ROOT / "web" / "app" / "page.tsx"


# ------------------------------------------------------------------ classifier ---

@pytest.mark.parametrize("title,expected", [
    ("Analytics Engineer", "data_engineering"),
    ("Senior Data Scientist", "machine_learning"),
    ("BI Analyst", "data_analysis"),
    ("Site Reliability Engineer", "devops_platform"),
    ("Product Owner", "product"),
    ("Product Designer", "design"),            # design must win over product
    ("Java vývojář", "software_engineering"),  # CZ title
    ("Účetní", "other_tech_function"),         # CZ title
    ("Backend Engineer", "software_engineering"),
    ("Social Media Manager", "social_media"),
    ("Specialista sociálních sítí", "social_media"),          # CZ
    ("Náborár pre projekty, Marketing | Sociálne siete", "social_media"),  # SK
    ("Community Manager", "social_media"),
    ("Influencer Marketing Specialist", "social_media"),
    ("Lighthouse Keeper", "uncategorised"),
])
def test_classify(title, expected):
    assert taxonomy.classify(title) == expected


def test_social_media_beats_the_catch_all():
    """Almost every social title also says "marketing" or "content", so without the ordering
    they all land in other_tech_function — and a subscriber who asked for social media gets
    the entire marketing/sales/finance/HR bucket instead. This is the same class of bug as
    test_specificity_order_holds, one layer down."""
    assert taxonomy.classify("Social Media Marketing Specialist") == "social_media"
    assert taxonomy.classify("Content Creator") == "social_media"
    assert taxonomy.classify("Marketing Specialist") == "other_tech_function"
    # ...but a designer who also runs the socials is still a designer.
    assert taxonomy.classify("Grafik a správa sociálních sítí") == "design"


def test_specificity_order_holds():
    """Data/ML/devops must beat the broad software 'engineer' catch-all. This is the
    ordering bug the single definition exists to prevent — it is invisible until a
    'Data Engineer' starts being filed as software_engineering."""
    assert taxonomy.classify("Data Engineer") == "data_engineering"
    assert taxonomy.classify("Machine Learning Engineer") == "machine_learning"
    assert taxonomy.classify("Cloud Engineer") == "devops_platform"


def test_hint_is_the_fallback_not_an_override():
    assert taxonomy.classify("Data Engineer", "other_tech_function") == "data_engineering"
    assert taxonomy.classify("Něco Divného", "other_tech_function") == "other_tech_function"
    assert taxonomy.classify(None) == "uncategorised"
    assert taxonomy.classify("") == "uncategorised"


# ------------------------------------------------------- internal consistency ----

def test_every_category_has_a_subject_word():
    """A category with no label makes the digest subject fall back to the raw enum."""
    labelled = set(taxonomy.SUBJECT_WORDS) | {taxonomy.UNCATEGORISED}
    assert set(taxonomy.CATEGORIES) - labelled == set()


def test_every_category_has_shortlist_keywords():
    """A category with no keywords silently retrieves nothing for a subscriber who
    selects it — an empty digest with no error anywhere."""
    keyed = set(taxonomy.SHORTLIST_KEYWORDS) | {taxonomy.UNCATEGORISED}
    assert set(taxonomy.CATEGORIES) - keyed == set()


def test_cv_rules_reference_real_categories():
    assert {c for c, _ in taxonomy.CV_RULES} <= set(taxonomy.CATEGORIES)


# ------------------------------------------------------------ cross-language ----

def _dbt_accepted_values() -> set[str]:
    """Pull the role_category accepted_values list out of the dbt schema YAML."""
    text = DBT_SCHEMA.read_text(encoding="utf-8")
    block = text.split("- name: role_category", 1)
    assert len(block) == 2, "role_category column not found in the dbt schema"
    match = re.search(r"values:\s*\[(.*?)\]", block[1], re.S)
    assert match, "accepted_values list not found for role_category"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_dbt_accepted_values_match_the_taxonomy():
    dbt_values = _dbt_accepted_values()
    assert dbt_values == set(taxonomy.CATEGORIES), (
        "dbt accepted_values and service/taxonomy.py disagree.\n"
        f"  only in dbt:      {sorted(dbt_values - set(taxonomy.CATEGORIES))}\n"
        f"  only in taxonomy: {sorted(set(taxonomy.CATEGORIES) - dbt_values)}"
    )


def test_frontend_maps_only_reference_real_categories():
    """The signup form maps display labels to categories. A stale value here produces a
    profile whose role filter matches nothing, with no error on any side."""
    text = WEB_PAGE.read_text(encoding="utf-8")
    referenced = set()
    for block_name in ("ROLE_CAT", "CV_ROLE_LABEL"):
        match = re.search(rf"const {block_name}[^=]*=\s*\{{(.*?)\}};", text, re.S)
        assert match, f"{block_name} not found in {WEB_PAGE.name}"
        body = match.group(1)
        # ROLE_CAT is label -> category (values); CV_ROLE_LABEL is category -> label (keys).
        referenced |= set(re.findall(r'"([a-z_]+)"', body))
        referenced |= set(re.findall(r"^\s*([a-z_]+):", body, re.M))
    unknown = referenced - set(taxonomy.CATEGORIES)
    assert not unknown, f"web/app/page.tsx references unknown role_category values: {sorted(unknown)}"
