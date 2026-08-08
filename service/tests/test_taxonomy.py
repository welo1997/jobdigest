"""The role taxonomy has one Python definition — these tests keep the rest in step.

`service/taxonomy.py` is imported by ingest, the shortlist builder, the digest subject
line and the CV parser, so those four cannot drift. Two consumers can't import Python:

  - ``dbt/models/staging/stg_job_postings.yml`` — an `accepted_values` test that runs in
    Snowflake. If a category is added here and not there, `dbt test` fails in CI on real
    data, which is a slow and confusing way to find out.
  - ``web/lib/options.ts`` — the vocabulary both signup forms offer, mapping each role chip
    to a category. A typo or a renamed category here silently produces a profile that
    matches nothing.

So these tests read those two files as text and assert they agree with `CATEGORIES`.

The web side moved twice and the paths below record where it landed. The pages now live
under ``web/app/(site)/[locale]/`` because the site is exported once per language, and the
chip → category maps moved out of the pages into ``web/lib/options.ts`` because a chip is
now keyed by a stable id rather than by its English label — a label that eight catalogues
rewrite cannot also be a lookup key. That refactor is precisely the kind of change these
tests exist to catch, so they follow it rather than being relaxed around it.
"""

import re
from pathlib import Path

import pytest

from service import taxonomy

ROOT = Path(__file__).resolve().parents[2]
DBT_SCHEMA = ROOT / "dbt" / "models" / "staging" / "stg_job_postings.yml"
WEB_OPTIONS = ROOT / "web" / "lib" / "options.ts"
WEB_PAGE = ROOT / "web" / "app" / "(site)" / "[locale]" / "page.tsx"

#: A title no pattern in PATTERNS can read, so the hint is the only thing left to decide the
#: answer — which is exactly the path that was writing raw source labels into the column.
OPAQUE = "Något Oklassificerbart"


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


def test_a_non_canonical_hint_is_discarded_not_stored():
    """`classify` used to end `return hint or UNCATEGORISED`, handing the source's own string
    straight into `postings.role_category`. Four adapters map their hint through a curated
    table first; five passed a raw third-party string (platsbanken's Swedish SSYK label,
    workable's employer-typed department, startupjobs' field slug, recruitee's category_code,
    oraclecloud's JobFamily).

    Measured on production 2026-08-08: 14 135 of 98 858 active postings (14%) carried a value
    outside CATEGORIES — platsbanken alone 13 561 rows across 954 distinct labels. The recall
    predicate is `role_category = any(...) OR search_tsv @@ (...)`, so every one of those rows
    was unreachable through the category half and survived on keyword alone. Nothing failed:
    the dbt `accepted_values` test runs in Snowflake against stg_job_postings.sql's own SQL
    `case`, which never sees a hint, so it structurally cannot catch this.

    `uncategorised` is the honest answer. It is not better at matching — neither value is
    selectable as a preference — but it means "unknown" to every consumer instead of meaning
    a category that does not exist."""
    assert taxonomy.classify(OPAQUE, "Systemutvecklare/Programmerare") == "uncategorised"
    assert taxonomy.classify(OPAQUE, "Greenvolt Next España, S.L.") == "uncategorised"
    # ...while a canonical hint still rescues a title the regexes cannot read.
    assert taxonomy.classify(OPAQUE, "software_engineering") == "software_engineering"
    # ...and a readable title still beats any hint, canonical or not.
    assert taxonomy.classify("Data Engineer", "Utesäljare") == "data_engineering"


@pytest.mark.parametrize("hint", [
    # platsbanken — occupation.label, the Swedish SSYK leaf label (954 distinct in production)
    "Systemutvecklare/Programmerare", "Utesäljare", "Helpdesktekniker/Supporttekniker",
    "Butikssäljare, dagligvaror/Medarbetare, dagligvaror",
    "Läkarsekreterare/Vårdadmin/Medicinsk sekreterare",
    # workable — job.function/department, employer free text. These are not job functions at all.
    "Greenvolt Next España, S.L.", "Engine by Starling", "FBS", "Wild Card", "NTG Freelancer",
    # startupjobs — the field's parent slug, and it arrives in both cases
    "sales", "Tech", "top-management",
    # recruitee — category_code
    "information_technology", "marketing_pr", "government_nonprofit",
    # oraclecloud — per-tenant HCM JobFamily/JobFunction
    "Integrated Supply Chain", "Health, Safety & Environment", "Customer/Product Support",
])
def test_classify_only_ever_returns_a_canonical_category(hint):
    """Every value here is real production data pulled from the five adapters that passed a
    raw hint. The title is deliberately unclassifiable, so the hint is the only thing that can
    decide the answer — which is exactly the path that was writing junk into the column.

    The literals live in this file rather than being passed through a shell: Swedish and Czech
    diacritics are silently mangled in transit (`Dataingenjör` -> `Datainginjor`), which has
    already produced one wrong conclusion in this repo."""
    assert taxonomy.classify(OPAQUE, hint) in taxonomy.CATEGORIES


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
    """The chip vocabulary maps role ids to categories. A stale value here produces a
    profile whose role filter matches nothing, with no error on any side.

    Each block is read with a pattern that picks out only the *category* positions. A blanket
    ``"[a-z_]+"`` sweep would also collect the chip ids and keywords that now sit alongside
    them (``product_manager``, ``data_engineer``) and report every one as an unknown category —
    a test that fails for a reason that isn't real is a test that gets deleted.
    """
    text = WEB_OPTIONS.read_text(encoding="utf-8")
    referenced = set()

    # ROLE_OPTIONS: [{ id, category, keyword }, …] — only `category:` names a category, and
    # `category: null` is the deliberate "no category models this" case, not a value.
    options = re.search(r"export const ROLE_OPTIONS[^=]*=\s*\[(.*?)\n\];", text, re.S)
    assert options, f"ROLE_OPTIONS not found in {WEB_OPTIONS.name}"
    referenced |= set(re.findall(r'category:\s*"([a-z_]+)"', options.group(1)))

    # Both of these are keyed *by* category: CV signals -> chip, and stored slug -> chip.
    for block_name in ("CV_ROLE_ID", "ROLE_ID_FOR_CATEGORY"):
        match = re.search(rf"export const {block_name}[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
        assert match, f"{block_name} not found in {WEB_OPTIONS.name}"
        referenced |= set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.M))

    assert referenced, "no categories extracted — the parse, not the frontend, is broken"
    unknown = referenced - set(taxonomy.CATEGORIES)
    assert not unknown, (
        f"web/lib/options.ts references unknown role_category values: {sorted(unknown)}"
    )


def test_skill_suggestions_are_keyed_by_real_role_ids():
    """`SKILLS_BY_ROLE` drives the skill chips the signup wizard offers for the picked roles.

    It is keyed by role **chip id**, and a key that matches no chip is the quietest possible
    bug: `suggestedSkills` looks the id up, finds nothing, and contributes nothing — so the
    role simply offers no skills and every other role still does. Nothing throws, nothing
    logs, and the column looks plausible. Renaming a chip in `ROLE_OPTIONS` without renaming
    it here does exactly that, which is why the check is on the *keys* rather than on the
    values: the words themselves are editorial, the ids are a contract.

    Not asserted: that every role has an entry. A role with no suggestions is a legitimate
    state — it falls back to handing the question to the visitor, the same way a typed role
    does — so requiring one would be a rule about copy, enforced as a test.
    """
    text = WEB_OPTIONS.read_text(encoding="utf-8")

    options = re.search(r"export const ROLE_OPTIONS[^=]*=\s*\[(.*?)\n\];", text, re.S)
    assert options, f"ROLE_OPTIONS not found in {WEB_OPTIONS.name}"
    role_ids = set(re.findall(r'id:\s*"([a-z_]+)"', options.group(1)))
    assert role_ids, "no role ids extracted — the parse, not the frontend, is broken"

    block = re.search(r"export const SKILLS_BY_ROLE[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert block, f"SKILLS_BY_ROLE not found in {WEB_OPTIONS.name}"
    keyed = set(re.findall(r"^\s*([a-z_]+):\s*\[", block.group(1), re.M))
    assert keyed, "no keys extracted — the parse, not the frontend, is broken"

    unknown = keyed - role_ids
    assert not unknown, (
        "web/lib/options.ts suggests skills for role chips that do not exist: "
        f"{sorted(unknown)} — these suggestions can never be shown"
    )


def test_every_language_names_every_category_in_its_subject_words():
    """`i18n.SUBJECT_WORDS` is what the digest subject calls a category, per language.

    A category missing from one language falls back to the English word, which produces a
    subject line that is Czech apart from one English noun — readable enough that nobody
    reports it, wrong enough to look machine-made. The English table here must also match
    `taxonomy.SUBJECT_WORDS`, which stays the single Python definition.
    """
    from service import i18n

    assert i18n.SUBJECT_WORDS["en"] == taxonomy.SUBJECT_WORDS, (
        "i18n.SUBJECT_WORDS['en'] and taxonomy.SUBJECT_WORDS disagree — the English subject "
        "word has two definitions and they have drifted."
    )
    # Compared against the English table, not `CATEGORIES`: `uncategorised` is deliberately
    # absent from both, because a digest of unclassified postings should not claim a category
    # in its subject at all. The invariant is that every language names the same set English
    # does — no more, no less.
    expected = set(taxonomy.SUBJECT_WORDS)
    assert expected <= set(taxonomy.CATEGORIES)
    for locale in i18n.LOCALES:
        missing = expected - set(i18n.SUBJECT_WORDS.get(locale, {}))
        assert not missing, f"i18n.SUBJECT_WORDS[{locale!r}] is missing: {sorted(missing)}"
        unknown = set(i18n.SUBJECT_WORDS[locale]) - expected
        assert not unknown, f"i18n.SUBJECT_WORDS[{locale!r}] names unknown categories: {sorted(unknown)}"


SIGNUP_FORMS = [WEB_PAGE, ROOT / "web" / "app" / "(plain)" / "v2" / "page.tsx"]


@pytest.mark.parametrize("form", SIGNUP_FORMS, ids=lambda p: p.parent.name)
def test_part_time_only_is_not_read_straight_off_the_chip(form):
    """The work-type chips are an inclusive multi-select — the form says "tap all that fit"
    and ships with Full-time pre-selected. So tapping Part-time means "this fits too", and
    `part_time_only: work.has("Part-time")` records the opposite of what the user was shown:
    subscribers with Full-time visibly ticked were stored as part-time-only, and the matcher
    then penalised every full-time role in their digest. It is only "only" when Full-time is
    not also selected.

    Asserted as text because this is TSX the test suite cannot import — same approach, and
    same reason, as the role_category drift tests above.

    Two chip vocabularies are accepted because the two forms are on different sides of the
    i18n refactor: the live wizard keys its chips by stable id (`fulltime`/`parttime`), while
    the unlinked `/v2` copy still keys them by English label. What is being asserted is the
    *derivation*, which is identical either way, so matching both spellings tests the same
    property rather than the spelling.
    """
    text = form.read_text(encoding="utf-8")
    assert not re.search(r'part_time_only:\s*work\.has\(', text), (
        f"{form.name} maps part_time_only straight off the chip — a subscriber who also "
        "selected Full-time is recorded as part-time-only."
    )
    assert re.search(
        r'work\.has\("(?:Part-time|parttime)"\)\s*&&\s*!work\.has\("(?:Full-time|fulltime)"\)',
        text,
    ), (
        f"{form.name} must derive part_time_only from Part-time selected AND Full-time not."
    )


ROLE_INPUT_FORMS = SIGNUP_FORMS + [
    ROOT / "web" / "app" / "(site)" / "[locale]" / "preferences" / "page.tsx"
]


@pytest.mark.parametrize("form", ROLE_INPUT_FORMS, ids=lambda p: p.parent.name)
def test_a_typed_role_is_never_silently_dropped(form):
    """"Add another role…" accepts anything, and most of what people type maps to no
    category — Sales, Cybersecurity, IT Support, all real fields the taxonomy does not model.

    Two ways to get this wrong, and both have shipped. Slugifying the label into
    `role_categories` produced `social_media_specialist`, a value no posting carries, so the
    filter matched nothing and no error was raised anywhere. Dropping it with
    `.filter(Boolean)` and nothing else is the same silence one step earlier: not stored, not
    logged, and the chip stays highlighted so the subscriber believes it took effect.

    The correct handling is to carry it into `stack`, which the shortlist full-text query
    searches — the word still steers retrieval even though nothing classified it.

    `ROLE_CAT[...]` and `roleCategory(...)` are the same lookup either side of the i18n
    refactor: the map moved into `web/lib/options.ts` and became a function when chips stopped
    being keyed by their English label.
    """
    text = form.read_text(encoding="utf-8")
    assert re.search(r"filter\(\(?\w+\)? =>\s*!(?:ROLE_CAT\[|roleCategory\()", text), (
        f"{form.name} does not separate role chips that map to no category — a typed role "
        "is either dropped or slugified into a filter that can never match."
    )
    # ...and that leftover has to reach `stack`, not be computed and then discarded. Either
    # named (`freeRoles`) or inlined into the payload — both are in use.
    assert re.search(r"stack:[^\n]*(freeRoles|\[\.\.\.roles\]\.filter)", text), (
        f"{form.name} computes the unmapped chips but does not send them as search keywords."
    )
