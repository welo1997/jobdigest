"""The role taxonomy — one definition, several consumers.

`role_category` is the spine of this dataset. It decides what lands in a subscriber's
shortlist, what the digest subject line claims the email is about, what a CV upload
prefills, and how the analysis marts segment. It used to be written out five times — the
ingest regexes, the shortlist keywords, the CV rules, the digest subject words, and the
dbt `accepted_values` test — so adding or renaming a category silently worked in some
places and not others.

Everything in Python now imports from here. Two consumers can't:

  - ``dbt/models/staging/stg_job_postings.sql`` + ``.yml`` run in Snowflake
  - ``web/app/page.tsx`` runs in the browser

so ``service/tests/test_taxonomy.py`` asserts those two stay in step and goes red if they
drift. Short of code generation that is the best available guarantee, and it converts a
silent inconsistency into a failing test.

Adding a category: add it to ``PATTERNS`` (order matters — see below), give it a
``SUBJECT_WORDS`` label, add it to the dbt ``accepted_values`` list, and run the tests.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- classification --
# Ordered most-specific first, and the order is load-bearing:
#   - data / ML / devops must precede the broad software "engineer" catch-all
#   - product must precede design, so a "Product Designer" lands in `design`
# Patterns carry English and Czech/Slovak terms, because a large share of jobs.cz and
# profesia titles are localised ("Java vývojář", "Produktový manažer", "Účetní"). Without
# the CZ/SK terms those fall through to `uncategorised` and never match a role filter.
PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # --- whole-labour-market sectors ---------------------------------------------------
    # Added 2026-08-09, when the taxonomy stopped being tech-only. These come FIRST and the
    # order is load-bearing in one direction only: a nurse's title never contains a tech
    # word, but plenty of tech patterns are broad enough to swallow a sector title
    # (`\bengineer\b` would take "Automationsingenjör", `\banalyst\b` would take "Financial
    # Analyst"). Specific-before-general is the whole rule in this file.
    #
    # Sized against production before being written, per CLAUDE.md's "grow the taxonomy from
    # the report *and* real inventory": these are the sectors with measurable rows in the
    # 42 532 `uncategorised` postings, not a guess at what a job board ought to have.
    ("healthcare", re.compile(
        r"\bnurse|\bdoctor\b|physician|dentist|physiotherap|pharmacist|paramedic|"
        r"caregiver|midwife|\bgp\b|surgeon|radiolog|"
        r"sestra|sestry|zdravotn|lékař|lékárn|zubní|fyzioterapeut|ošetřovatel|pečovat|"
        r"sjuksköterska|undersköterska|läkare|tandläkare|barnmorska|vårdbiträde", re.I)),
    ("education", re.compile(
        r"teacher|lecturer|professor|educator|\btutor\b|kindergarten|preschool|"
        r"teaching assistant|"
        r"učitel|učitelka|vychovatel|pedagog|lektor(?!ov)|docent|vysokoškolsk[ýá] uči|"
        r"lärare|förskollärare|barnskötare|studie- och yrkesvägledare", re.I)),
    # "chef" is deliberately absent: in Swedish it means *manager* (Restaurangchef, IT-chef,
    # Ekonomichef), so a bare match would misfile every Swedish leadership title into
    # hospitality. Only the French-derived "chef de cuisine" is unambiguous.
    ("hospitality", re.compile(
        r"chef de cuisine|\bbarista\b|waiter|waitress|bartender|receptionist|housekeep|"
        r"restaurant manager|hotel manager|"
        r"kuchař|kuchařk|číšník|servírk|recepční|"
        r"\bkock\b|servitör|servitris|restaurangchef|hotellchef|bartender", re.I)),
    ("skilled_trades", re.compile(
        r"electrician|welder|plumber|\bmechanic\b|locksmith|\bfitter\b|hvac|"
        r"maintenance technician|"
        r"elektrikář|elektrikár|svářeč|zámečník|instalatér|montér|údržbář|automechanik|"
        r"elektriker|svetsare|rörmokare|mekaniker|montör|elkonstruktör", re.I)),
    ("construction", re.compile(
        r"construction|site manager|bricklayer|carpenter|surveyor|"
        r"stavbyvedoucí|stavební|zedník|tesař|"
        r"byggledare|byggnadsarbetare|snickare|murare|platschef", re.I)),
    ("logistics_transport", re.compile(
        r"warehouse|forklift|truck driver|delivery driver|courier|dispatcher|"
        r"logistics coordinator|freight|"
        r"skladník|řidič|kurýr|spediter|logistik(?:a|y)?\b|"
        r"lagerarbetare|truckförare|chaufför|lastbilsförare", re.I)),
    ("manufacturing_production", re.compile(
        r"production (?:operator|technician|planner|manager)|machine operator|"
        r"assembly|quality (?:inspector|technician)|cnc|"
        r"operátor výroby|seřizovač|výrobní|montážní|"
        r"produktionstekniker|processoperatör|montör i produktion|maskinoperatör", re.I)),
    # --- tech --------------------------------------------------------------------------
    ("data_engineering", re.compile(
        r"data engineer|analytics engineer|dataops|data platform|data warehouse|\betl\b|"
        r"datov[ýá] inžen|dátový inžinier|data inžinier", re.I)),
    ("machine_learning", re.compile(
        r"machine learning|\bml engineer|\bai engineer|data scientist|mlops|"
        r"deep learning|computer vision|\bnlp\b|strojové uč|"
        r"umělá inteligence|umelá inteligencia|datov[ýá] v[ěe]dec", re.I)),
    # Before `data_analysis` on purpose: that pattern ends in a bare `\banalyst\b`, so
    # "Financial Analyst" and "Credit Analyst" were landing in data analysis — a subscriber
    # asking for data work got finance roles, and one asking for finance got nothing.
    ("finance_accounting", re.compile(
        r"\bfinanc(?:e|ial)\b|account(?:ant|ing)|\bcontroller\b|bookkeep|\btreasury\b|"
        r"\baudit(?:or)?\b|payroll|tax (?:advisor|manager|specialist)|"
        r"účetní|účtovník|mzdová účetní|daňov|finanční|"
        r"revisor|redovisning|ekonomiassistent|lönespecialist", re.I)),
    ("data_analysis", re.compile(
        r"data analyst|bi analyst|business intelligence|power bi|\btableau\b|\banalyst\b|"
        r"analytics|analytik|analytičk|analytičc", re.I)),
    ("devops_platform", re.compile(
        r"devops|platform engineer|site reliability|\bsre\b|cloud engineer|"
        r"infrastructure engineer|\bkubernetes\b|cloud architect|"
        r"správce systém|správca systémov|systémov[ýá] administr|"
        r"administrátor (?:is|it|systém|sít|server)|síťov[ýá] administr", re.I)),
    ("product", re.compile(
        r"product manager|product owner|product lead|product management|\btpm\b|program manager|"
        r"produktov\w*\s+manaž|produktov\w*\s+vlastník", re.I)),
    ("design", re.compile(
        r"designer|\bux\b|\bui\b|user experience|user interface|design lead|"
        r"designér|dizajnér|grafik|grafičk|návrhá[řr]", re.I)),
    ("software_engineering", re.compile(
        r"software engineer|software developer|back[- ]?end|front[- ]?end|full[- ]?stack|"
        r"web developer|mobile developer|\bios\b|android|\bdeveloper\b|programmer|"
        r"\bengineer(?:ing)?\b|qa engineer|\bsdet\b|"
        r"vývojá[řr]|vývojárk|programátor|programátork|softwarov|softvérov", re.I)),
    # Must precede other_tech_function: nearly every social title also says "marketing" or
    # "content", so without this it lands in the catch-all and a subscriber who asked for
    # social media gets the whole marketing/sales/finance/HR bucket instead.
    ("social_media", re.compile(
        r"social[ -]?media|\bsmm\b|community manager|influencer|content creator|"
        r"paid social|social ads|"
        r"sociáln\w*\s+(?:sít|siet|médi|medi)", re.I)),
    # --- business functions -----------------------------------------------------------
    # Split out of `other_tech_function` on 2026-08-09. That catch-all held 15 004 active
    # postings and six distinct professions inside it — sales 6 290, operations 2 602,
    # marketing 2 395, finance 2 028, HR 708, legal 589 — so a subscriber asking for sales
    # got the whole bucket. `other_tech_function` is KEPT below as the residual, because
    # subscribers already hold it in `profiles.role_categories` and removing a stored value
    # is a migration, not a taxonomy edit.
    ("marketing", re.compile(
        r"marketing|marketingov|marketér|\bseo\b|\bsem\b|growth|brand manager|"
        r"copywriter|content marketing|\bpr\b|public relations|kampan", re.I)),
    ("sales", re.compile(
        r"\bsales\b|account executive|account manager|key account|business development|"
        r"säljare|obchodní zástupce|obchodní manaž|obchodník|prodejce|predajca", re.I)),
    ("hr_recruiting", re.compile(
        r"recruit|talent acquisition|people ops|human resources|\bhr\b|rekryter|"
        r"personalist|nábor|náborář|mzdov[áý] účetní", re.I)),
    ("legal", re.compile(
        r"\blegal\b|counsel|paralegal|compliance officer|"
        r"právník|právnik|advokát|jurist|koncipient", re.I)),
    ("customer_support", re.compile(
        r"customer (?:support|service|care)|help ?desk|technical support|support specialist|"
        r"it support|service desk|zákaznick|kundtjänst|kundservice|podpora zákazn", re.I)),
    ("operations", re.compile(
        r"\boperations\b|customer success|supply chain|procurement|office manager|"
        r"provozn|nákupčí|nákupca|inköpare", re.I)),
    # Residual for a business function at a tech company that none of the above names.
    # Deliberately last of the business group and much narrower than it was.
    ("other_tech_function", re.compile(
        r"business analyst|\bcontent\b|community|partnerships|strategy|"
        r"ekonom(?!ick)|administrativ|asistent|assistent|koordinátor|koordinator", re.I)),
)

UNCATEGORISED = "uncategorised"

#: Every valid value of `role_category`, including the fallback. Rows are never dropped for
#: being `uncategorised` — it is a first-class value and useful for auditing the taxonomy.
CATEGORIES: tuple[str, ...] = tuple(c for c, _ in PATTERNS) + (UNCATEGORISED,)

#: Membership set for the guard in `classify`. Derived, never a second list — a copy is how
#: the value written to `postings.role_category` and the value a subscriber may ask for drift
#: apart, which is the whole reason this module is the single definition.
_VALID: frozenset[str] = frozenset(CATEGORIES)


def classify(title: str | None, hint: str | None = None) -> str:
    """Classify a job title into a role_category.

    Title patterns win. When none match, fall back to the source-provided profession
    `hint` (e.g. a jobs.cz "Marketing" field), which rescues localised CZ/SK titles the
    regexes still miss. Only `uncategorised` when neither fires.

    **The hint must already be a value in `CATEGORIES`; anything else is discarded.** It used
    to be returned verbatim, and five adapters were passing a raw third-party string —
    platsbanken's Swedish SSYK label, workable's employer-typed department, startupjobs' field
    slug, recruitee's `category_code`, oraclecloud's `JobFamily`. That put 14 135 of 98 858
    active postings (14%, measured 2026-08-08) into a category no subscriber can select and no
    query can match, reachable only through the keyword half of the recall predicate. Nothing
    reported it: the dbt `accepted_values` test runs against `stg_job_postings.sql`'s own SQL
    `case`, which never sees a hint.

    Mapping a raw label to a real category is a per-source curation job (see
    `smartrecruiters.FUNCTION_HINTS`, `themuse.CATEGORIES`) and belongs in the adapter, where
    the source's vocabulary is known. Here the only safe answer is `uncategorised`: not better
    at matching, but it means "unknown" to every consumer rather than naming a category that
    does not exist.
    """
    text = title or ""
    for category, pattern in PATTERNS:
        if pattern.search(text):
            return category
    return hint if hint in _VALID else UNCATEGORISED


# ------------------------------------------------------------------- retrieval ---
# Keywords for the cheap full-text prefilter that builds a subscriber's shortlist. These
# are search terms, not classifiers: recall-first, bilingual, and deliberately broader
# than the regexes above, because the AI matcher does the precision afterwards.
SHORTLIST_KEYWORDS: dict[str, list[str]] = {
    "data_engineering": ["data engineer", "analytics engineer", "datový inženýr", "etl", "dbt"],
    "data_analysis": ["data analyst", "bi analyst", "analytik", "power bi", "reporting"],
    "machine_learning": ["machine learning", "ml engineer", "data scientist", "ai engineer",
                         "strojové učení"],
    "software_engineering": ["software engineer", "developer", "vývojář", "programátor",
                             "backend", "frontend", "fullstack"],
    "devops_platform": ["devops", "sre", "platform engineer", "cloud engineer",
                        "kubernetes", "administrátor"],
    "product": ["product manager", "product owner", "produktový manažer", "produktový vlastník"],
    "design": ["designer", "designér", "ux", "ui", "grafik", "návrhář"],
    "social_media": ["social media", "sociální sítě", "sociálních sítí", "sociálne siete",
                     "community manager", "influencer", "content creator", "smm",
                     "instagram", "tiktok"],
    "marketing": ["marketing", "marketingový", "seo", "brand", "copywriter", "kampaň"],
    "sales": ["sales", "obchodní", "obchodník", "prodejce", "säljare", "account manager",
              "business development"],
    "finance_accounting": ["finance", "účetní", "účetnictví", "controller", "audit",
                           "redovisning", "ekonomiassistent"],
    "hr_recruiting": ["recruiter", "personalista", "nábor", "human resources", "hr",
                      "rekryterare"],
    "legal": ["legal", "právník", "advokát", "counsel", "compliance", "jurist"],
    "customer_support": ["customer support", "zákaznická podpora", "help desk", "it support",
                         "kundtjänst"],
    "operations": ["operations", "provozní", "supply chain", "nákup", "customer success"],
    "healthcare": ["nurse", "sestra", "zdravotní sestra", "lékař", "sjuksköterska", "läkare",
                   "fyzioterapeut", "pečovatelka"],
    "education": ["teacher", "učitel", "učitelka", "lärare", "pedagog", "lektor",
                  "förskollärare"],
    "hospitality": ["kuchař", "číšník", "recepční", "kock", "servitör", "barista",
                    "restaurant"],
    "skilled_trades": ["elektrikář", "svářeč", "instalatér", "zámečník", "elektriker",
                       "svetsare", "mekaniker", "electrician", "welder"],
    "construction": ["stavbyvedoucí", "stavební", "construction", "byggledare", "snickare",
                     "zedník"],
    "logistics_transport": ["skladník", "řidič", "logistika", "warehouse", "lagerarbetare",
                            "truckförare", "chaufför"],
    "manufacturing_production": ["výrobní", "operátor výroby", "produktionstekniker",
                                 "maskinoperatör", "cnc", "montážní"],
    "other_tech_function": ["business analyst", "koordinátor", "asistent", "administrativa"],
}

# ---------------------------------------------------------------------- display --
#: What a human calls each category in a digest subject line.
SUBJECT_WORDS: dict[str, str] = {
    "data_engineering": "data engineering",
    "data_analysis": "data",
    "machine_learning": "ML",
    "software_engineering": "engineering",
    "devops_platform": "platform",
    "product": "product",
    "design": "design",
    "social_media": "social media",
    "marketing": "marketing",
    "sales": "sales",
    "finance_accounting": "finance",
    "hr_recruiting": "HR",
    "legal": "legal",
    "customer_support": "support",
    "operations": "operations",
    "healthcare": "healthcare",
    "education": "education",
    "hospitality": "hospitality",
    "skilled_trades": "trades",
    "construction": "construction",
    "logistics_transport": "logistics",
    "manufacturing_production": "production",
    "other_tech_function": "tech",
}

# ------------------------------------------------------------------------- CV ----
# Substring rules for reading a role out of an uploaded CV. Deliberately a SUBSET of the
# categories: a CV is prose, and the broad patterns above would fire on almost any tech CV
# ("engineer" appears in most of them), producing a profile that asks for everything. These
# few are the ones a CV states clearly enough to act on. Kept here rather than in
# cvparse.py so the divergence from PATTERNS is visible next to what it diverges from.
CV_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_engineering", ("data engineer", "analytics engineer", "dataops", "etl",
                          "data platform", "data pipeline")),
    ("machine_learning", ("machine learning", "ml engineer", "ai engineer",
                          "data scientist", "mlops", "deep learning")),
    ("data_analysis", ("data analyst", "bi analyst", "business intelligence",
                       "reporting analyst", "insights analyst")),
    # Unambiguous in prose: nobody writes "social media manager" on a CV by accident, and
    # without this a social/community CV reads as no role at all (see the "Detected: r" bug).
    ("social_media", ("social media", "sociální sítě", "sociálních sítí", "sociálne siete",
                      "community manager", "influencer marketing", "content creator")),
)
