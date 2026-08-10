"""The skills / tech-stack facet — one definition, several consumers.

This is a **fifth search axis**, alongside role (`taxonomy.py`), geography (`geo.py`),
education (`education.py`) and seniority. Where those answer *what kind of job* and *where*,
this answers *what tools it uses* — so a search can build a "Python" filter chip and a card
can show extracted skills. It is deliberately **additive and low-risk, because it is not
role**: a wrong `role_category` pushes a job into the wrong inbox (the digest emails real
people); a slightly-off skill tag on a search result is forgiving and never emails anyone.
That difference is why "classify as well as we can" is the right posture *here* in a way it is
not for role.

**What this is NOT** (the same null-passes discipline as `work_mode`, `education_min`, city):

  * Not a change to the digest matcher. Skills are already *searchable* today via full-text
    over `title || description` (`store.query_shortlist` and `/preview` match a subscriber's
    `stack` that way) and that stays. This facet adds *filtering + display*, not matching.
  * Not a hard gate. A posting with no extracted skills must never be hidden — an empty list
    is a first-class value that passes everything, exactly like a null `education_min`.
  * Not a role guess, and not AI at runtime.

**Deterministic gazetteer match, not AI extraction.** Consistent with the 2026-08-10
regex-primary decision for roles (`notes/2026-08-10-taxonomy-expansion-and-skills-scoping.md`):
a controlled dictionary is the right tool, and it is an *even better* fit for skills than for
roles — tool names are universal proper nouns ("Python" is Python in Swedish, Czech and
German), so the multilingual tax that keeps role regex ongoing barely applies. The model /
description-mining is used only to **grow this gazetteer**, never at query time; that is the
same discovery-feeds-deterministic pattern, and for a *facet* a controlled vocabulary is what
you want anyway (free-form AI gives "Python", "python3", "Python programming" as three chips).

**Precision on ambiguous tokens is the whole risk, and it is the `education.py` lesson: a
false positive is the harm.** "We move fast, so *go* get results", "you will *excel* in this
role", "a *swift* response", "in the *spring* we launch", "the *rest* of the team" — every one
of these contains a skill token used as an ordinary word. The guards below (see `_GAZETTEER`)
are, in order of importance:

  1. **Alphanumeric word boundaries**, so `java` never fires inside `javascript`, `git` never
     inside `github`, `sql` never inside `postgresql`, `rust` never inside `trust`.
  2. **A guarded-variant mechanism**: short or ambiguous tokens (`go`, `r`, `swift`, `spark`,
     `spring`, `node`, `excel`, `sap`, `rest`, ...) are counted **only when a co-occurring
     anchor** proves the technical sense (`go` needs `goroutine|golang|gin|gorm`; `excel`
     needs `spreadsheet|pivot|vlookup|vba|...`). Unambiguous variants (`python`, `kubernetes`,
     `postgresql`) match freely.

Every guard has a test in `service/tests/test_skills.py` written to fail if the guard is
removed (the `test_education.py` method), and each is mutation-checked. Because a miss is the
safe error, when in doubt this module drops the skill rather than inventing one.

This module is the **one live definition**. `dbt/seeds/skill_taxonomy.csv` is the dormant
marts copy (the Snowflake side is decommissioned) and is deliberately *not* imported — reading
a CSV out of `dbt/seeds/` at runtime is the `.dockerignore`/`target_companies.csv` trap named
in CLAUDE.md. When the web/search facet lands (a later phase) its chip vocabulary mirrors
`canonical_skills()` and is drift-tested, the same rule as `geo` and `options`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------- normalisation --

#: Markup is stripped before matching, same reason as `education.normalise`: descriptions
#: arrive as raw HTML, and a tag's attributes (`<img src=".../react-logo.svg">`) would
#: otherwise read as prose and tag the whole posting "react". URLs go for the same reason —
#: a link to `react.dev` in a benefits blurb is not a skill requirement.
_MARKUP = re.compile(r"<[^>]*>|&[a-z]+;|&#\d+;|https?://\S+|\bwww\.\S+", re.IGNORECASE)


def _normalise(text: str | None) -> str:
    """Lower-case, fold diacritics, strip markup, collapse whitespace — **keeping punctuation**.

    This is the one place skills normalisation diverges from `education`/`geo`, and it is
    load-bearing. Those modules reduce every non-alphanumeric run to a space, which would turn
    "c++" into "c", "node.js" into "node js" and ".net" into " net" — erasing exactly the
    characters that distinguish a tool name. So punctuation is preserved and the boundary
    matching below (see `_boundary`) does the work `\\b` would otherwise do, but in a way that
    treats `+ # .` as literal parts of a token rather than as separators.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", _MARKUP.sub(" ", str(text)))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = (
        ascii_only.replace("ł", "l")
        .replace("Ł", "l")
        .replace("ø", "o")
        .replace("Ø", "o")
        .replace("đ", "d")
        .replace("ß", "ss")
    )
    return re.sub(r"\s+", " ", ascii_only.lower()).strip()


def _boundary(variant: str) -> re.Pattern[str]:
    """Compile a variant as a whole token, with **alphanumeric** boundaries.

    ``(?<![a-z0-9])x(?![a-z0-9])`` matches ``x`` only when it is not touching a letter or
    digit on either side, while treating any ``+ # . -`` inside ``x`` as literal. So "c++"
    (``c\\+\\+``) matches "C++/CUDA" but not "c+++"; "java" matches "Java" but not the "java"
    inside "javascript"; "go" matches the standalone word but not "google" or "argon".
    Plain ``\\b`` cannot do this — it treats ``+`` as a boundary, so ``\\bc++\\b`` would match
    the "c" in "basic++" nonsense and mishandle "c++" itself.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])")


# --------------------------------------------------------------- the gazetteer --


@dataclass(frozen=True)
class Skill:
    """One canonical skill and every way it is spelled.

    ``variants`` match freely (they are unambiguous proper nouns). ``guarded`` variants match
    **only** when ``anchor`` is also found somewhere in the text — they are the short or
    word-like tokens (`go`, `r`, `excel`) that carry a technical meaning and an everyday one.
    ``anchor`` is a raw regex source, searched over the whole normalised text.
    """

    canonical: str
    category: str
    variants: tuple[str, ...] = ()
    guarded: tuple[str, ...] = ()
    anchor: str = ""

    # compiled forms, built once at construction
    _safe_re: tuple[re.Pattern[str], ...] = field(default=(), compare=False, repr=False)
    _guarded_re: tuple[re.Pattern[str], ...] = field(
        default=(), compare=False, repr=False
    )
    _anchor_re: re.Pattern[str] | None = field(default=None, compare=False, repr=False)

    def matches(self, text: str) -> bool:
        if any(p.search(text) for p in self._safe_re):
            return True
        if (
            self._guarded_re
            and self._anchor_re is not None
            and self._anchor_re.search(text)
        ):
            return any(p.search(text) for p in self._guarded_re)
        return False


def _skill(
    canonical: str,
    category: str,
    variants: tuple[str, ...],
    guarded: tuple[str, ...] = (),
    anchor: str = "",
) -> Skill:
    if guarded and not anchor:
        raise ValueError(f"guarded variants for {canonical!r} need an anchor")
    if anchor and not guarded:
        raise ValueError(f"anchor for {canonical!r} but no guarded variants")
    return Skill(
        canonical=canonical,
        category=category,
        variants=variants,
        guarded=guarded,
        anchor=anchor,
        _safe_re=tuple(_boundary(v) for v in variants),
        _guarded_re=tuple(_boundary(v) for v in guarded),
        _anchor_re=re.compile(anchor) if anchor else None,
    )


# The gazetteer, grouped by category for readability only — order does not matter (unlike
# `taxonomy.PATTERNS`), because a posting collects *every* skill it names, not the first.
#
# Ported wholesale from `dbt/seeds/skill_taxonomy.csv` (the tech-heavy 92-row seed), then
# lightly expanded with high-value, low-ambiguity tech tokens. Cross-sector growth
# (marketing / finance / design / office / certifications) is deliberately deferred to the
# mining phase — see the module docstring and the design note. Ambiguous additions carry a
# guard + anchor and a test built from the trap sentence.
_GAZETTEER: tuple[Skill, ...] = (
    # --- languages -----------------------------------------------------------------------
    _skill(
        "python",
        "language",
        ("python", "python3"),
        guarded=("py",),
        anchor=r"python|pandas|numpy|django|flask|jupyter|data science",
    ),
    _skill("javascript", "language", ("javascript", "js")),
    _skill(
        "typescript",
        "language",
        ("typescript",),
        guarded=("ts",),
        anchor=r"typescript|angular|\breact\b|\bnode",
    ),
    _skill("java", "language", ("java",)),
    # `golang` is unambiguous; bare `go` is the verb until an anchor proves otherwise.
    _skill(
        "go",
        "language",
        ("golang",),
        guarded=("go",),
        anchor=r"golang|goroutine|gofmt|\bgin\b|\bgorm\b|go module",
    ),
    _skill("rust", "language", ("rust",)),
    _skill("cpp", "language", ("c++", "cpp")),
    _skill("ruby", "language", ("ruby",)),
    _skill("scala", "language", ("scala",)),
    _skill("kotlin", "language", ("kotlin",)),
    # iOS `Swift`, not "a swift response" nor a person named Swift.
    _skill(
        "swift",
        "language",
        (),
        guarded=("swift",),
        anchor=r"\bios\b|xcode|swiftui|cocoa|objective\s*c|\bapple\b",
    ),
    # The R language — the single hardest token in the file (one letter).
    _skill(
        "r",
        "language",
        (),
        guarded=("r",),
        anchor=r"rstudio|r studio|tidyverse|ggplot|dplyr|\bshiny\b|\bcran\b|"
        r"statistical|statistics|data analysis|\brmarkdown\b",
    ),
    _skill("sql", "language", ("sql",)),
    _skill("php", "language", ("php",)),
    _skill("perl", "language", ("perl",)),
    _skill("bash", "language", ("bash",)),
    _skill("powershell", "language", ("powershell",)),
    _skill("matlab", "language", ("matlab",)),
    _skill("elixir", "language", ("elixir",)),
    _skill("clojure", "language", ("clojure",)),
    _skill("haskell", "language", ("haskell",)),
    _skill("solidity", "language", ("solidity",)),
    _skill("objective_c", "language", ("objective-c", "objective c")),
    # --- databases -----------------------------------------------------------------------
    _skill("nosql", "database", ("nosql",)),
    _skill("postgresql", "database", ("postgresql", "postgres")),
    _skill("mysql", "database", ("mysql",)),
    _skill("mongodb", "database", ("mongodb", "mongo")),
    _skill("redis", "database", ("redis",)),
    _skill("elasticsearch", "database", ("elasticsearch",)),
    _skill("snowflake", "database", ("snowflake",)),
    _skill("bigquery", "database", ("bigquery",)),
    _skill("redshift", "database", ("redshift",)),
    _skill("databricks", "database", ("databricks",)),
    _skill("sqlite", "database", ("sqlite",)),
    _skill("cassandra", "database", ("cassandra",)),
    _skill("dynamodb", "database", ("dynamodb",)),
    _skill("clickhouse", "database", ("clickhouse",)),
    _skill("sqlserver", "database", ("sql server", "mssql")),
    # --- data engineering ----------------------------------------------------------------
    _skill("dbt", "transform", ("dbt",)),
    _skill("airflow", "orchestration", ("airflow",)),
    _skill("prefect", "orchestration", ("prefect",)),
    _skill("dagster", "orchestration", ("dagster",)),
    _skill("fivetran", "ingestion", ("fivetran",)),
    _skill("airbyte", "ingestion", ("airbyte",)),
    _skill("kafka", "streaming", ("kafka",)),
    _skill("flink", "streaming", ("flink",)),
    _skill("hadoop", "compute", ("hadoop",)),
    # Apache `Spark`, not "spark joy" / "spark curiosity".
    _skill(
        "spark",
        "compute",
        (),
        guarded=("spark",),
        anchor=r"apache|pyspark|scala|hadoop|databricks|big data|streaming|\bemr\b",
    ),
    # --- libraries / ML ------------------------------------------------------------------
    _skill("pandas", "library", ("pandas",)),
    _skill("numpy", "library", ("numpy",)),
    _skill("scikit-learn", "library", ("scikit-learn", "sklearn")),
    _skill("tensorflow", "ml_framework", ("tensorflow",)),
    _skill("pytorch", "ml_framework", ("pytorch",)),
    _skill("keras", "ml_framework", ("keras",)),
    _skill("spacy", "library", ("spacy",)),
    _skill("opencv", "library", ("opencv",)),
    _skill("xgboost", "library", ("xgboost",)),
    _skill("huggingface", "library", ("huggingface", "hugging face")),
    _skill("matplotlib", "library", ("matplotlib",)),
    _skill("mlflow", "library", ("mlflow",)),
    # --- frontend ------------------------------------------------------------------------
    _skill("react", "frontend", ("react", "reactjs", "react.js")),
    _skill("vue", "frontend", ("vue", "vuejs", "vue.js")),
    _skill("angular", "frontend", ("angular",)),
    _skill("nextjs", "frontend", ("nextjs", "next.js")),
    _skill("svelte", "frontend", ("svelte",)),
    _skill("tailwind", "frontend", ("tailwind", "tailwindcss")),
    _skill("redux", "frontend", ("redux",)),
    _skill("jquery", "frontend", ("jquery",)),
    _skill("webpack", "frontend", ("webpack",)),
    _skill("html", "frontend", ("html", "html5")),
    _skill("css", "frontend", ("css", "css3")),
    # --- backend -------------------------------------------------------------------------
    _skill(
        "nodejs",
        "backend",
        ("nodejs", "node.js"),
        guarded=("node",),
        anchor=r"javascript|express|\bnpm\b|nestjs|\bjs\b|backend",
    ),
    _skill("django", "backend", ("django",)),
    # `Flask` the framework, not a laboratory flask.
    _skill(
        "flask",
        "backend",
        (),
        guarded=("flask",),
        anchor=r"python|django|\bapi\b|backend|\bweb\b|microservice",
    ),
    _skill("fastapi", "backend", ("fastapi",)),
    _skill(
        "rails",
        "backend",
        ("ruby on rails",),
        guarded=("rails", "ror"),
        anchor=r"ruby|active\s*record|\brails\s+app\b",
    ),
    # `Spring Boot` is unambiguous; bare `spring` is the season until Java context appears.
    _skill(
        "spring",
        "backend",
        ("spring boot",),
        guarded=("spring",),
        anchor=r"\bjava\b|\bboot\b|hibernate|\bmvc\b|\bjpa\b|maven",
    ),
    # `Express` the framework, not "express delivery" / "express yourself".
    _skill(
        "express",
        "backend",
        ("express.js", "expressjs"),
        guarded=("express",),
        anchor=r"node|nodejs|javascript|\bapi\b|backend",
    ),
    _skill("laravel", "backend", ("laravel",)),
    _skill("symfony", "backend", ("symfony",)),
    _skill("dotnet", "backend", ("dotnet", ".net", ".net core", "asp.net")),
    _skill("graphql", "backend", ("graphql",)),
    _skill("grpc", "backend", ("grpc",)),
    # `REST` API, not "the rest of" / "rest and recovery".
    _skill(
        "rest",
        "backend",
        ("rest api", "restful"),
        guarded=("rest",),
        anchor=r"\bapi\b|\bhttp\b|endpoint|\bjson\b|web service",
    ),
    # --- devops / cloud / CI -------------------------------------------------------------
    _skill("docker", "devops", ("docker",)),
    _skill("kubernetes", "devops", ("kubernetes", "k8s")),
    _skill("terraform", "devops", ("terraform",)),
    _skill("ansible", "devops", ("ansible",)),
    _skill("helm", "devops", ("helm",)),
    _skill("pulumi", "devops", ("pulumi",)),
    _skill("argocd", "devops", ("argocd", "argo cd")),
    _skill("openshift", "devops", ("openshift",)),
    _skill("nginx", "devops", ("nginx",)),
    _skill("rabbitmq", "devops", ("rabbitmq",)),
    _skill("jenkins", "ci_cd", ("jenkins",)),
    _skill("github_actions", "ci_cd", ("github actions",)),
    _skill("gitlab_ci", "ci_cd", ("gitlab ci",)),
    _skill("circleci", "ci_cd", ("circleci",)),
    _skill("aws", "cloud", ("aws", "amazon web services")),
    _skill("azure", "cloud", ("azure",)),
    _skill("gcp", "cloud", ("gcp", "google cloud")),
    # --- tools / observability / BI / design ---------------------------------------------
    _skill("git", "tool", ("git",)),
    _skill("linux", "tool", ("linux",)),
    _skill("jira", "tool", ("jira",)),
    _skill("grafana", "observability", ("grafana",)),
    _skill("datadog", "observability", ("datadog",)),
    _skill("prometheus", "observability", ("prometheus",)),
    _skill("tableau", "bi_tool", ("tableau",)),
    _skill("looker", "bi_tool", ("looker", "lookml")),
    _skill("power_bi", "bi_tool", ("power bi", "powerbi")),
    _skill("metabase", "bi_tool", ("metabase",)),
    _skill("qlik", "bi_tool", ("qlik", "qlikview", "qlik sense")),
    # `Excel` the tool, not "excel in a fast-paced environment" — the worst everyday-word trap.
    _skill(
        "excel",
        "office",
        (),
        guarded=("excel",),
        anchor=r"microsoft|spreadsheet|pivot|vlookup|\bvba\b|macros|ms office|"
        r"ms excel|advanced excel|\bxls",
    ),
    # `SAP` the ERP, not "sap" the ordinary word (also common in DE/NL descriptions).
    _skill(
        "sap",
        "erp",
        (),
        guarded=("sap",),
        anchor=r"\berp\b|s\s*4\s*hana|s4hana|\babap\b|sap fi|sap mm|sap sd|\bhana\b|sap erp",
    ),
    _skill("salesforce", "crm", ("salesforce",)),
    _skill("hubspot", "crm", ("hubspot",)),
    _skill("servicenow", "tool", ("servicenow",)),
    _skill("figma", "design_tool", ("figma",)),
)

#: How much of a description to scan. Descriptions are HTML soup and occasionally enormous;
#: this only bounds a pathological input. Matches `education._MAX_SCAN`.
_MAX_SCAN = 20_000

#: A generous ceiling on skills per posting. Real postings rarely name more than a dozen; the
#: cap only bounds a keyword-stuffed ad. Applied after sorting, so it is deterministic.
MAX_SKILLS = 20


def extract_skills(title: str | None, description: str | None) -> list[str]:
    """The canonical skills a posting names, deduped and sorted, or ``[]`` when it names none.

    ``[]`` is a first-class value — the storage column and every future filter must treat an
    empty list as "no constraint", the same null-passes rule as `education_min` and
    `work_mode`. The title is scanned as well as the description, because many postings are a
    bare "Python Developer" with no body at all.

    Sorted alphabetically so the result is deterministic (the cap must not depend on match
    order), and because a facet has no inherent ranking — every named skill is equally a fact
    about the posting.
    """
    text = _normalise(f"{title or ''} {(description or '')[:_MAX_SCAN]}")
    if not text:
        return []
    found = {s.canonical for s in _GAZETTEER if s.matches(text)}
    return sorted(found)[:MAX_SKILLS]


def canonical_skills() -> list[str]:
    """The controlled vocabulary — every canonical skill, sorted.

    This is the set a search facet's chips are drawn from and the set a web mirror is
    drift-tested against (a later phase). Exposed as a function, not a bare constant, so
    consumers cannot mutate the gazetteer.
    """
    return sorted(s.canonical for s in _GAZETTEER)


#: canonical skill -> category, for a facet that wants to group chips. Free-form labels; the
#: dormant dbt seed is not kept in step (Snowflake side decommissioned).
SKILL_CATEGORY: dict[str, str] = {s.canonical: s.category for s in _GAZETTEER}
