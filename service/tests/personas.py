"""Fifty synthetic subscribers, for asking "who does this system fail?" before they sign up.

Every matching defect this project has found was found by reading **one real subscriber's**
data end to end (2026-07-26: five bugs; 2026-07-27: the off-taxonomy problem). The failure
shape is always the same — no exception, no failed timer, every check green, and a real
person receiving the wrong jobs or none. `service/watchdog.py` catches that, but only after
the fact, in production, on someone real, three days late.

This is the same review done fifty times over, offline, on people who do not exist.

These are **data, not tests**. `service/evaluate.py` drives them; a future pytest case can
import the same list. Deliberately spanning the axes that have actually broken:

  * all nine `taxonomy.CATEGORIES`, including the starved ones (`design` 212 active postings,
    `social_media` 163, `data_engineering` 138 — against `other_tech_function`'s 10 073)
  * eight subscribers the taxonomy models **no category for** — sales, cybersecurity, IT
    support, chef, teacher, nurse, logistics, legal. Their typed role must land in `stack`
    and steer retrieval as a keyword; several *should* legitimately end up `widened`
  * city-level geography, including the "on-site Brno job to a Prague subscriber" failure
  * seniority shapes `cvparse._seniority` actually emits (it always includes the adjacent
    lower level, so singletons are rare in the wild but must still work)
  * the `part_time_only` shape that bug #6 mis-recorded
  * CV-derived profiles, including the known `cvparse._SKILLS` gap (Figma is offered as a
    UI chip and cannot be detected by the parser)
  * degenerate profiles that produce **no recall predicate at all**, where the retrieval
    floor deliberately does *not* fire

`expect` states what a correct system should do, not what it currently does. Where the two
disagree, that is the finding — `widened_ok=True` marks a persona for whom widening is the
honest outcome rather than a defect.

Addresses use the `.invalid` TLD (RFC 2606): reserved, guaranteed never resolvable, so no
mail can reach a real inbox even if a send path is misconfigured. `pydantic.EmailStr` is not
asked to accept them — `evaluate.py` validates payload shape through `webapp.SubscribeIn`
with a substitute address, because the address is not what that check is for.

Do NOT set `regions`: it is derived from `countries`/`remote_scope` on every write
(`store._location_prefs`). Setting it here would be silently overwritten and would document
a contract that does not exist.
"""

from __future__ import annotations

from typing import Any

DOMAIN = "jobdigest-eval.invalid"

# Cities that carry real inventory, so a persona pinned to one is testing the gate rather
# than an empty set. Counts measured 2026-07-27 against 19 872 active postings.
#   prague 4483 · bratislava 1046 · brno 968 · ostrava 407 · berlin 271 · plzen 262
#   kosice 218 · munich 212 · pardubice 184 · zilina 143 · paris 133
# Note 9 973 of 19 872 have a NULL city — half the inventory reaches the model through the
# "unknown is kept" rule, which is why that rule is load-bearing rather than a nicety.


def _p(key: str, note: str, *, expect: dict[str, Any] | None = None, **payload: Any) -> dict:
    """One persona. `payload` is what a signup would POST; `expect` is the guarantee."""
    base: dict[str, Any] = {
        "label": "My digest",
        "stack": [],
        "seniorities": ["junior", "mid"],
        "role_categories": [],
        "countries": ["CZ"],
        "cities": [],
        "remote_scope": "eu",
        "work_types": ["permanent", "freelance/contract"],
        "part_time_only": False,
        "eligible_only": True,
        "sectors": [],
        "min_score": 6,
        "frequency": "daily",
    }
    base.update(payload)
    exp = {"min_shortlist": 20, "widened_ok": False, "off_taxonomy": False}
    exp.update(expect or {})
    return {"key": key, "email": f"{key}@{DOMAIN}", "note": note,
            "payload": base, "expect": exp}


# --------------------------------------------------------------- the nine categories ---
# One well-formed subscriber per category. The small categories are the point: `design`,
# `social_media` and `data_engineering` each hold under 220 active postings and lost every
# shortlist slot to `other_tech_function` before the round-robin partition landed.

CATEGORY_PERSONAS = [
    _p("p01-data-engineer-prague",
       "Bread-and-butter case. 138 active postings in category — the smallest of the nine.",
       label="Data engineering", role_categories=["data_engineering"],
       stack=["dbt", "snowflake", "python", "sql", "airflow"],
       seniorities=["mid", "senior"], cities=["cz:prague"], sectors=["fintech"],
       years_experience=5, has_cv=False),

    _p("p02-data-analyst-brno",
       "Second-city analyst. Brno has 968 postings; Prague roles must NOT leak in.",
       label="Data analysis", role_categories=["data_analysis"],
       stack=["sql", "power bi", "excel"], seniorities=["junior", "mid"],
       cities=["cz:brno"], sectors=["ecommerce"]),

    _p("p03-ml-engineer-prague",
       "182 active postings. Narrow but real.",
       label="Machine learning", role_categories=["machine_learning"],
       stack=["python", "pytorch", "scikit-learn"], seniorities=["mid", "senior"],
       cities=["cz:prague"]),

    _p("p04-devops-ostrava",
       "Third-city infra. 407 postings in Ostrava across all categories.",
       label="Platform engineering", role_categories=["devops_platform"],
       stack=["kubernetes", "terraform", "aws", "docker"], seniorities=["mid", "senior"],
       cities=["cz:ostrava"]),

    _p("p05-product-prague",
       "'roadmapping' is not in SKILL_NAMES or any keyword list — it should surface in the "
       "unmet-demand report, and still steer full-text retrieval as a stack term.",
       label="Product", role_categories=["product"],
       stack=["roadmapping", "jira", "sql"], seniorities=["mid"], cities=["cz:prague"]),

    _p("p06-designer-prague",
       "212 active postings. Figma is a UI chip the CV parser cannot detect — see p42.",
       label="Design", role_categories=["design"],
       stack=["figma", "ux", "ui"], seniorities=["junior", "mid"], cities=["cz:prague"]),

    _p("p07-software-engineer-brno",
       "3 200 postings — the second-largest category, should never starve.",
       label="Software engineering", role_categories=["software_engineering"],
       stack=["typescript", "react", "postgres"], seniorities=["mid", "senior"],
       cities=["cz:brno"]),

    _p("p08-social-media-prague",
       "163 postings. The category added on 2026-07-26 after it turned out every social "
       "title also says 'marketing' and so landed in the catch-all.",
       label="Social media", role_categories=["social_media"],
       stack=["canva", "capcut", "instagram"], seniorities=["junior"], cities=["cz:prague"]),

    _p("p09-other-tech-prague",
       "10 073 postings spanning marketing/sales/finance/HR/legal in one bucket. The "
       "round-robin exists because this category used to win every slot on volume.",
       label="Marketing", role_categories=["other_tech_function"],
       stack=["marketing", "hubspot"], seniorities=["mid"], cities=["cz:prague"]),

    _p("p10-data-analyst-cz-anywhere",
       "No cities named — 'any city in Czechia'. Must be strictly wider than p02.",
       label="Data analysis anywhere in CZ", role_categories=["data_analysis"],
       stack=["sql", "excel"], seniorities=["junior", "mid"], cities=[]),
]

# ------------------------------------------------------------------------ geography ---
# `geo.location_predicate` is assembled SQL and its failures are semantic, not syntactic.
# Half the inventory has a null city and passes the gate unconditionally — these personas
# check that the *known* half is filtered correctly without collapsing recall.

GEO_PERSONAS = [
    _p("p11-prague-only-country-scope",
       "Strictest shape: one city, remote confined to CZ. A Berlin remote role is out.",
       label="Prague only", role_categories=["software_engineering"], stack=["python"],
       cities=["cz:prague"], remote_scope="country"),

    _p("p12-brno-only-design",
       "The reported bug, inverted: a Brno subscriber must not be sent Prague on-site work. "
       "Small category + second city is the thinnest realistic combination.",
       label="Brno design", role_categories=["design"], stack=["figma"],
       cities=["cz:brno"], expect={"widened_ok": True}),

    _p("p13-prague-and-brno",
       "Two cities in one country — both must be admitted, Ostrava must not.",
       label="Prague or Brno", role_categories=["software_engineering"],
       stack=["java", "spring"], cities=["cz:prague", "cz:brno"]),

    _p("p14-cz-de-pl-multi-country",
       "Cities named for CZ only. Naming none for DE/PL means ANY city there — the mixed "
       "case where a per-country city filter is easy to apply too broadly.",
       label="Central Europe", role_categories=["data_analysis"], stack=["sql", "python"],
       countries=["CZ", "DE", "PL"], cities=["cz:prague"], remote_scope="eu"),

    _p("p15-germany-only",
       "1 307 active DE postings. No CZ inventory should reach this subscriber on-site.",
       label="Germany", role_categories=["software_engineering"], stack=["golang", "aws"],
       countries=["DE"], cities=["de:berlin", "de:munich"], seniorities=["mid", "senior"]),

    _p("p16-netherlands-only",
       "42 active NL postings across ALL categories. Expected to fall under the floor and "
       "widen — this is the honest 'we have no inventory for you' case, and the report "
       "must say RETRIEVAL rather than pretend the large widened shortlist is health.",
       label="Netherlands", role_categories=["data_engineering"], stack=["dbt", "airflow"],
       countries=["NL"], cities=["nl:amsterdam"],
       expect={"widened_ok": True, "min_shortlist": 0}),

    _p("p17-slovakia",
       "3 778 active SK postings — the second-biggest country and easy to forget.",
       label="Slovensko", role_categories=["software_engineering"], stack=["php", "sql"],
       countries=["SK"], cities=["sk:bratislava", "sk:kosice"]),

    _p("p18-remote-worldwide",
       "No city constraint at all; every remote role on earth is in scope.",
       label="Remote anywhere", role_categories=["software_engineering"],
       stack=["python", "django"], countries=["CZ"], cities=[], remote_scope="worldwide"),

    _p("p19-remote-eu-from-prague",
       "On-site Prague + remote anywhere in the EU. The most common real shape.",
       label="Prague or EU remote", role_categories=["data_analysis"], stack=["sql"],
       cities=["cz:prague"], remote_scope="eu"),

    _p("p20-remote-country-only",
       "Remote, but only from inside CZ. A US-remote posting must not appear.",
       label="Remote CZ", role_categories=["devops_platform"], stack=["kubernetes"],
       cities=[], remote_scope="country"),
]

# ------------------------------------------------------------------------ seniority ---
# `search_jobs.seniority()` returns "mid" both for a stated mid-level role and for the
# majority of titles naming no level at all; `seniority_stated()` exists so the model is
# told "unstated" instead of a default dressed as a fact. On the live shortlist that was
# 102 of 120 candidates — if that share collapses, the fix regressed.

SENIORITY_PERSONAS = [
    _p("p21-junior-only",
       "Junior alone. Senior/lead roles are a hard exclusion; unstated ones are NOT.",
       label="Junior analyst", role_categories=["data_analysis"], stack=["sql", "excel"],
       seniorities=["junior"], cities=["cz:prague"]),

    _p("p22-senior-only",
       "The mirror: graduate/intern roles must be excluded for a senior-only subscriber.",
       label="Senior engineer", role_categories=["software_engineering"],
       stack=["typescript", "kubernetes"], seniorities=["senior"], cities=["cz:prague"],
       years_experience=11),

    _p("p23-mid-senior",
       "The shape cvparse emits for 6+ years — it always adds the adjacent lower level.",
       label="Platform", role_categories=["devops_platform"], stack=["terraform", "aws"],
       seniorities=["mid", "senior"], cities=["cz:prague"], years_experience=8),

    _p("p24-all-three-levels",
       "No effective seniority filter. Should be the widest of this group.",
       label="Product anywhere", role_categories=["product"], stack=["jira"],
       seniorities=["junior", "mid", "senior"], cities=[]),

    _p("p25-junior-only-design",
       "Junior + a 212-posting category + one city. Three narrowings stacked — the most "
       "likely legitimate starvation in the whole cohort.",
       label="Junior designer", role_categories=["design"], stack=["figma", "canva"],
       seniorities=["junior"], cities=["cz:prague"], expect={"widened_ok": True}),
]

# ----------------------------------------------------------------- work arrangements ---
# `part_time_only` is deliberately NOT a SQL gate: part-time is ~2% of inventory, so a hard
# filter empties the digest. The shortlist sorts part-time first and the prompt caps a
# full-time posting at 5, keeping it off the email but on /matches.

WORK_PERSONAS = [
    _p("p26-part-time-only",
       "part_time_only true. Part-time rows must sort FIRST — without that the freshest-120 "
       "window fills with full-time before one part-time row appears.",
       label="Part-time analyst", role_categories=["data_analysis"], stack=["excel", "sql"],
       seniorities=["junior"], cities=["cz:prague"], part_time_only=True,
       work_types=["part-time"]),

    _p("p27-part-time-and-full-time",
       "Bug #6's exact shape: someone who ticked Part-time with Full-time still selected. "
       "part_time_only MUST be false here — recording it as true demoted nearly every role "
       "in two real people's digests on the strength of a checkbox that never meant that.",
       label="Flexible", role_categories=["product"], stack=["jira"],
       cities=["cz:prague"], part_time_only=False,
       work_types=["permanent", "part-time"]),

    _p("p28-freelance-only",
       "Contract work only.",
       label="Freelance dev", role_categories=["software_engineering"],
       stack=["react", "node"], cities=[], work_types=["freelance/contract"],
       remote_scope="worldwide"),

    _p("p29-permanent-only",
       "The conservative default.",
       label="Permanent role", role_categories=["data_analysis"], stack=["sql"],
       cities=["cz:prague"], work_types=["permanent"]),

    _p("p30-part-time-off-taxonomy",
       "Part-time AND unmodelled. Two independent narrowings that have never been combined "
       "in a real subscriber — the interaction is untested.",
       label="Part-time sales", role_categories=[], stack=["sales", "b2b", "crm"],
       seniorities=["junior"], cities=["cz:prague"], part_time_only=True,
       work_types=["part-time"], expect={"off_taxonomy": True, "widened_ok": True}),
]

# ------------------------------------------------------- subscribers we model nothing for ---
# The taxonomy has nine categories. Subscribers are not obliged to be one of them. A closed
# vocabulary is fine for ranking and fatal for admission: a typed role that maps to no
# category must become a `stack` keyword (the shortlist full-text query searches those), NOT
# be slugified into role_categories (that produced `social_media_specialist`, a value no
# posting carries) and NOT be dropped by `.filter(Boolean)`.
#
# Every one of these SHOULD widen. That is not a defect — it is the fingerprint the retrieval
# floor exists to leave, and `widened` is why the watchdog can name them.

OFF_TAXONOMY_PERSONAS = [
    _p("p31-sales",
       "Named in CLAUDE.md as unmodelled. Sales titles do exist in other_tech_function.",
       label="Sales", role_categories=[], stack=["sales", "obchodní zástupce", "b2b"],
       cities=["cz:prague"], expect={"off_taxonomy": True, "widened_ok": True}),

    _p("p32-cybersecurity",
       "Named in CLAUDE.md as unmodelled. Real tech roles with real inventory, but no "
       "category — so recall depends entirely on the keyword half of the predicate.",
       label="Cybersecurity", role_categories=[],
       stack=["cybersecurity", "penetration testing", "siem"], cities=["cz:prague"],
       expect={"off_taxonomy": True, "widened_ok": True}),

    _p("p33-it-support",
       "Named in CLAUDE.md as unmodelled. Note 'administrátor' IS a devops keyword, so this "
       "may partially match through a category the subscriber never selected.",
       label="IT support", role_categories=[],
       stack=["it support", "helpdesk", "servicedesk"], cities=["cz:prague"],
       expect={"off_taxonomy": True, "widened_ok": True}),

    _p("p34-chef",
       "Outside ingestion scope entirely — we index tech companies. An honest zero is the "
       "correct outcome; the failure would be silently emailing them something else.",
       label="Chef", role_categories=[], stack=["kuchař", "gastronomie"],
       cities=["cz:prague"], expect={"off_taxonomy": True, "widened_ok": True,
                                     "min_shortlist": 0}),

    _p("p35-teacher",
       "Also outside scope. Included because the UI does not stop anyone signing up.",
       label="Teacher", role_categories=[], stack=["učitel", "lektor", "vzdělávání"],
       cities=["cz:prague"], expect={"off_taxonomy": True, "widened_ok": True,
                                     "min_shortlist": 0}),

    _p("p36-nurse",
       "Healthcare is a listed *sector* but there is no clinical role category — a subscriber "
       "could plausibly read the sector chip as meaning we carry these jobs.",
       label="Nurse", role_categories=[], stack=["zdravotní sestra", "nurse"],
       sectors=["healthcare"], cities=["cz:prague"],
       expect={"off_taxonomy": True, "widened_ok": True, "min_shortlist": 0}),

    _p("p37-logistics-coordinator",
       "Logistics is a sector chip with no matching role category — same trap as p36.",
       label="Logistics", role_categories=[], stack=["logistics", "supply chain", "spedice"],
       sectors=["logistics"], cities=["cz:prague"],
       expect={"off_taxonomy": True, "widened_ok": True}),

    _p("p38-legal-counsel",
       "Legal sits inside other_tech_function's 10k bucket without being a tech function — "
       "the same criticism made of 'Administrativa' and not yet acted on.",
       label="Legal", role_categories=[], stack=["právník", "legal counsel", "compliance"],
       cities=["cz:prague"], expect={"off_taxonomy": True, "widened_ok": True}),
]

# ------------------------------------------------------------------------- CV-derived ---
# The raw CV is discarded at upload by design; only three columns survive. `cv_summary` is
# free text and reaches the model verbatim, so its content matters more than its shape.

CV_PERSONAS = [
    _p("p39-cv-data-engineering",
       "A CV the parser handles well — every tool is in the 38-entry _SKILLS list.",
       label="Data engineer", role_categories=["data_engineering"],
       stack=["python", "sql", "dbt", "snowflake"], seniorities=["mid", "senior"],
       cities=["cz:prague"], has_cv=True, years_experience=6,
       cv_summary="Detected: data engineering · python, sql, dbt (+4) · ~6 yrs · fintech"),

    _p("p40-cv-social-media",
       "social_media is one of only four categories CV_RULES can read out of a CV.",
       label="Social media", role_categories=["social_media"],
       stack=["instagram", "tiktok"], seniorities=["junior"], cities=["cz:prague"],
       has_cv=True, years_experience=2,
       cv_summary="Detected: social media · excel (+1) · ~2 yrs · marketing"),

    _p("p41-cv-detected-nothing",
       "The real fallback string when extraction finds nothing. The model receives an "
       "explicit statement of ignorance rather than a confident wrong claim.",
       label="Career changer", role_categories=["data_analysis"], stack=["excel"],
       seniorities=["junior"], cities=["cz:prague"], has_cv=True,
       cv_summary="We couldn't detect much — add details yourself."),

    _p("p42-cv-figma-designer",
       "The known _SKILLS gap, from the live demand report: 3x figma, 1x canva, 1x capcut. "
       "The UI offers Figma as a chip and the parser cannot find it, so a designer's CV "
       "comes back near-empty. This is the case for opening up _SKILLS.",
       label="Product designer", role_categories=["design"],
       stack=["figma", "canva", "sketch"], seniorities=["mid"], cities=["cz:prague"],
       has_cv=True, years_experience=4,
       cv_summary="Detected: ~4 yrs"),

    _p("p43-cv-senior-15-years",
       "Long career. years_experience is exported to the model and should not be read as a "
       "seniority filter on its own.",
       label="Principal engineer", role_categories=["software_engineering"],
       stack=["java", "kafka", "kubernetes"], seniorities=["senior"], cities=["cz:prague"],
       has_cv=True, years_experience=15,
       cv_summary="Detected: software engineering · java, kafka, aws (+9) · ~15 yrs · banking"),
]

# ------------------------------------------------------------- degenerate and adversarial ---

EDGE_PERSONAS = [
    _p("p44-empty-everything",
       "No categories, no stack, default label → `_shortlist_terms` returns NOTHING, so no "
       "recall predicate is applied and the floor deliberately does NOT fire (the widened "
       "query would be identical, so retrying is pure cost). Pins that branch.",
       label="My digest", role_categories=[], stack=[], cities=[],
       expect={"widened_ok": False}),

    _p("p45-label-carries-the-signal",
       "Nothing selected, but the label is meaningful. `_shortlist_terms` harvests words "
       "from the label (len>2, not generic) — so this must retrieve differently from p44.",
       label="Kubernetes platform reliability", role_categories=[], stack=[], cities=[]),

    _p("p46-oversized-stack",
       "60 stack terms against a hard cap of 40. Which 20 are silently discarded is not "
       "specified anywhere, and the discarding is invisible to the subscriber.",
       label="Everything", role_categories=["software_engineering"],
       stack=[f"skill{i:02d}" for i in range(40)] + [
           "python", "java", "scala", "rust", "golang", "typescript", "kotlin", "swift",
           "elixir", "haskell", "clojure", "erlang", "perl", "ruby", "php", "c++",
           "dotnet", "spark", "flink", "beam"],
       cities=["cz:prague"]),

    _p("p47-czech-diacritics",
       "Full-text uses the 'simple' dictionary (no stemming/stopwords) precisely so CZ/SK "
       "tokenises cleanly. Diacritics must survive the round trip into plainto_tsquery.",
       label="Vývojář", role_categories=["software_engineering"],
       stack=["vývojář", "programátor", "účetní", "sociální sítě"], cities=["cz:prague"]),

    _p("p48-eligible-only-false",
       "Drops the eligibility clause. Should be strictly wider than the same profile with "
       "it on — the one predicate that survives widening alongside location.",
       label="Open to visa sponsorship", role_categories=["software_engineering"],
       stack=["python"], cities=[], eligible_only=False, remote_scope="worldwide"),

    _p("p49-uncategorised-hunter",
       "4 247 active postings are `uncategorised` — a real stored value that is deliberately "
       "NOT selectable, because as a *search* it means 'postings we failed to classify'. "
       "Reachable only through keywords, which is the point.",
       label="Open to anything technical", role_categories=[],
       stack=["technik", "specialista", "koordinátor"], cities=["cz:prague"]),

    _p("p50-every-category-at-once",
       "All nine selected. The round-robin must give each a share — before it existed, a "
       "four-category subscriber got 117 rows from one bucket and 3 from the rest, and the "
       "matcher correctly returned zero picks from it.",
       label="Everything technical", role_categories=[
           "data_engineering", "machine_learning", "data_analysis", "devops_platform",
           "product", "design", "software_engineering", "social_media",
           "other_tech_function"],
       stack=["sql", "python"], seniorities=["junior", "mid", "senior"], cities=[]),
]


PERSONAS: list[dict] = (
    CATEGORY_PERSONAS + GEO_PERSONAS + SENIORITY_PERSONAS + WORK_PERSONAS
    + OFF_TAXONOMY_PERSONAS + CV_PERSONAS + EDGE_PERSONAS
)

assert len(PERSONAS) == 50, f"expected 50 personas, got {len(PERSONAS)}"
assert len({p["key"] for p in PERSONAS}) == 50, "persona keys must be unique"
assert len({p["email"] for p in PERSONAS}) == 50, "persona emails must be unique"
assert all(p["email"].endswith(f"@{DOMAIN}") for p in PERSONAS), (
    "every persona address must sit under the .invalid domain — that is the safety net "
    "that makes a misconfigured send path undeliverable rather than embarrassing"
)


# Negative cases: these must NOT produce a second live profile. Driven separately by
# `evaluate.py` because "was rejected" is the assertion, not "got a good shortlist".
NEGATIVE_CASES = [
    {"key": "n01-duplicate-email",
     "note": "A second live subscription for an address that already has one must be "
             "refused by the partial unique index `uq_profiles_live_email`. "
             "`create_email_subscription` returns None rather than raising.",
     "email": PERSONAS[0]["email"], "payload": PERSONAS[0]["payload"],
     "expect": "none"},
    {"key": "n02-unknown-role-category",
     "note": "`social_media_specialist` is the exact value that got into a live profile by "
             "slugifying a free-text chip, and became a filter matching nothing with no "
             "error anywhere. All four request models now 422 on it.",
     "email": f"n02-unknown-role-category@{DOMAIN}",
     "payload": {**PERSONAS[0]["payload"], "role_categories": ["social_media_specialist"]},
     "expect": "422"},
    {"key": "n03-uncategorised-not-selectable",
     "note": "`uncategorised` is a real stored value but must not be selectable as a search.",
     "email": f"n03-uncategorised@{DOMAIN}",
     "payload": {**PERSONAS[0]["payload"], "role_categories": ["uncategorised"]},
     "expect": "422"},
]
