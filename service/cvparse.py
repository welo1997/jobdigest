"""CV parsing — extract profile signals from an uploaded PDF/DOCX, then throw the file away.

Privacy stance (decided 2026-07-18): **parse & discard**. We read the file in memory,
pull out derived signals (skills, role categories, seniority, sectors, rough years of
experience), and never persist the raw document. Only the derived signals are stored,
so there is no sensitive CV content at rest.

Extraction is heuristic (free, instant) so the sign-up form can autofill immediately.
A richer Claude pass can run later in the nightly batch — the return shape stays stable.

    from service import cvparse
    text    = cvparse.extract_text(file_bytes, filename)
    signals = cvparse.extract_signals(text)     # -> dict, safe to store
"""

from __future__ import annotations

import io
import re
import zipfile

from typing import Optional

from service import education, taxonomy

MAX_BYTES = 8 * 1024 * 1024          # reject anything over 8 MB (compressed / on-the-wire)
# A DOCX is a ZIP; the 8 MB cap is on the *compressed* size, so a decompression bomb can
# still expand to gigabytes. Cap the total uncompressed size and the per-entry ratio before
# handing anything to python-docx/lxml.
MAX_UNCOMPRESSED = 80 * 1024 * 1024  # a real CV's parts total well under this
MAX_ZIP_RATIO = 200                  # sum(uncompressed)/compressed above this = bomb
ALLOWED = (".pdf", ".docx", ".txt")


class CVParseError(Exception):
    pass


# --------------------------------------------------------------- text extract --

def extract_text(data: bytes, filename: str) -> str:
    name = (filename or "").lower().strip()
    if not name.endswith(ALLOWED):
        raise CVParseError("Please upload a PDF or DOCX file.")
    if len(data) > MAX_BYTES:
        raise CVParseError("That file is too large (max 8 MB).")

    if name.endswith(".pdf"):
        return _from_pdf(data)
    if name.endswith(".docx"):
        return _from_docx(data)
    return data.decode("utf-8", errors="ignore")


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:                       # pragma: no cover
        raise CVParseError("PDF support needs `pypdf` (pip install pypdf).") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        raise CVParseError("Couldn't read that PDF — it may be scanned images.") from e


def _guard_zip_bomb(data: bytes) -> None:
    """Reject decompression bombs before python-docx/lxml touch the file.

    Sums the declared uncompressed sizes in the ZIP central directory and checks both an
    absolute cap and the overall compression ratio. Cheap (reads the directory, not the
    entries) and runs before any XML is parsed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
    except zipfile.BadZipFile as e:
        raise CVParseError("Couldn't read that DOCX file.") from e
    if total > MAX_UNCOMPRESSED:
        raise CVParseError("That file is too large once decompressed.")
    if len(data) and total / len(data) > MAX_ZIP_RATIO:
        raise CVParseError("That file looks malformed and can't be processed.")


def _from_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as e:                        # pragma: no cover
        raise CVParseError("DOCX support needs `python-docx` (pip install python-docx).") from e
    _guard_zip_bomb(data)
    try:
        # python-docx parses via lxml with resolve_entities=False (no XXE / entity expansion).
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    except CVParseError:
        raise
    except Exception as e:
        raise CVParseError("Couldn't read that DOCX file.") from e


# --------------------------------------------------------------- signal extract -

# Canonical skill -> regex of accepted variants (word-boundaried, case-insensitive).
_SKILLS: dict[str, str] = {
    "dbt": r"\bdbt\b", "snowflake": r"\bsnowflake\b", "python": r"\bpython\b",
    "sql": r"\bsql\b", "airflow": r"\bairflow\b", "spark": r"\bspark\b",
    "pandas": r"\bpandas\b", "looker": r"\blooker\b", "power bi": r"power\s?bi",
    "tableau": r"\btableau\b", "dagster": r"\bdagster\b", "kafka": r"\bkafka\b",
    "aws": r"\baws\b", "gcp": r"\bgcp\b|google cloud", "azure": r"\bazure\b",
    "docker": r"\bdocker\b", "kubernetes": r"\bkubernetes\b|\bk8s\b",
    "terraform": r"\bterraform\b", "postgres": r"postgres(?:ql)?",
    "bigquery": r"\bbigquery\b", "redshift": r"\bredshift\b", "databricks": r"\bdatabricks\b",
    # A bare "r" is the hardest skill in this list to detect: one letter, and the obvious
    # `(?<![a-z])r(?![a-z])` fires on the r in "s.r.o." — which is on essentially every
    # Czech CV, since it's the local Ltd. suffix in every employer's name. It also fires on
    # "R&D". Excluding a neighbouring dot or ampersand kills both without losing "R, Python",
    # "(R)" or "R/SQL". Real case seen in production: a Czech designer's CV came back as
    # "Detected: r" and nothing else, so the matcher was told they were an R programmer.
    "r": r"(?<![\w.])r(?![\w.&])", "java": r"\bjava\b", "scala": r"\bscala\b",
    "typescript": r"\btypescript\b", "javascript": r"\bjavascript\b", "react": r"\breact\b",
    "excel": r"\bexcel\b", "git": r"\bgit\b", "fivetran": r"\bfivetran\b",
    "metabase": r"\bmetabase\b", "pytorch": r"\bpytorch\b", "tensorflow": r"\btensorflow\b",
    "scikit-learn": r"scikit[- ]?learn|sklearn",
}

#: Every tool this parser can name. Exported so `store.unmet_demand_terms` can tell a skill
#: a subscriber typed ("Figma") from a role the taxonomy has no representation for ("Sales").
SKILL_NAMES: tuple[str, ...] = tuple(_SKILLS)

# role_category -> keyword list. Defined in service/taxonomy.py (CV_RULES), which also
# explains why reading a CV uses a deliberately narrower rule set than classifying a title.
_ROLE_RULES = taxonomy.CV_RULES

_SECTORS = ("fintech", "ecommerce", "e-commerce", "trading", "banking", "insurance",
            "healthcare", "gaming", "logistics", "retail", "saas", "crypto", "marketing")

_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|architect)\b", re.I)
_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|graduate|intern|trainee|entry[- ]level)\b", re.I)
_YEARS_RE = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)\b", re.I)


def _skills(text: str) -> list[str]:
    found = [canon for canon, pat in _SKILLS.items() if re.search(pat, text, re.I)]
    # stable, most-common-first-ish ordering by appearance
    return found


def _years(text: str) -> int | None:
    yrs = [int(m) for m in _YEARS_RE.findall(text)]
    return max(yrs) if yrs else None


def _seniority(text: str, years: int | None) -> list[str]:
    if years is not None:
        if years < 2:
            base = "junior"
        elif years < 6:
            base = "mid"
        else:
            base = "senior"
    elif _SENIOR_RE.search(text):
        base = "senior"
    elif _JUNIOR_RE.search(text):
        base = "junior"
    else:
        base = "mid"
    # include the adjacent level so we don't over-narrow the search
    ladder = ["junior", "mid", "senior"]
    i = ladder.index(base)
    return sorted({ladder[i], ladder[max(0, i - 1)]}, key=ladder.index)


def _roles(text: str) -> list[str]:
    t = text.lower()
    return [cat for cat, kws in _ROLE_RULES if any(k in t for k in kws)]


def _sectors(text: str) -> list[str]:
    t = text.lower()
    out = []
    for s in _SECTORS:
        if s in t:
            out.append("ecommerce" if s == "e-commerce" else s)
    return sorted(set(out))


# --- education ----------------------------------------------------------------
# Deliberately a *narrower* vocabulary than service/education.py's. That module reads job ads,
# which argue about requirements in prose; a CV states a qualification, usually as a title or a
# section heading. Czech and Slovak CVs mostly carry the abbreviated title rather than the word
# — "Ing." is a master's, "Bc." a bachelor's — and without those a Czech CV reads as no
# education at all.
_CV_EDUCATION: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("doctorate", re.compile(
        r"\bph\.?\s?d\.?\b|\bdoctorate\b|\bdoctoral\b|\bdoktor\w*|\bcsc\.\b|\brndr\.\b", re.I)),
    ("master", re.compile(
        r"\bmaster'?s?\b|\bm\.?sc\.?\b|\bm\.?a\.?\b|\bmba\b|\bmagistr\w*|\bmgr\.?\b|"
        r"\bing\.\b|\bmga\.\b|\bdipl\.?[- ]ing\b", re.I)),
    ("bachelor", re.compile(
        r"\bbachelor'?s?\b|\bb\.?sc\.?\b|\bb\.?a\.?\b|\bbakal[áa][řr]\w*|\bbc\.\b", re.I)),
    ("vocational", re.compile(
        r"\bapprenticeship\b|\bvocational\b|\bberufsausbildung\b|\bvyu[čc]en\w*|"
        r"\bv[ýy]u[čc]n[íi]\s+list\b|\bst[řr]edn[íi]\s+odborn\w*", re.I)),
    ("secondary", re.compile(
        r"\bhigh\s+school\b|\bsecondary\s+school\b|\bgymn[áa]zium\b|\bgymn[áa]zia\b|"
        r"\bst[řr]edn[íi]\s+[šs]kola\b|\bmaturit(?!y)\w*|\babitur\b", re.I)),
)

# "MSc in Economics", "Bachelor of Computer Science", "studium informatiky". Stops at the first
# comma, bracket, digit or line break — a CV line is "MSc in Economics, Charles University 2019"
# far more often than it is a sentence, and the university is not the field.
_CV_FIELD = re.compile(
    r"\b(?:degree|bachelor'?s?|master'?s?|ph\.?\s?d\.?|m\.?sc\.?|b\.?sc\.?|mba|studium|"
    r"studi[uo]m|obor)\b[^\S\n]*(?:degree[^\S\n]*)?\b(?:in|of|v|z|oboru)\b[^\S\n]*"
    r"([A-Za-zÀ-ž][A-Za-zÀ-ž&/ -]{2,60})", re.I)


def _education(text: str) -> tuple[Optional[str], Optional[str]]:
    """(highest level found, field of study) — either may be None.

    Highest, not lowest: this reads what somebody *has*, whereas
    `education.classify_requirement` reads what a job *demands*, and the safe direction is
    opposite in the two cases. Over-reading a CV would narrow the subscriber's own digest;
    over-reading an ad would delete a job from it.
    """
    level = next((lv for lv, pat in _CV_EDUCATION if pat.search(text or "")), None)
    m = _CV_FIELD.search(text or "")
    field = " ".join(m.group(1).split()).strip(" -/&") if m else None
    return level, (field or None)


def extract_signals(text: str) -> dict:
    """Turn raw CV text into storable profile signals. Never returns raw CV content."""
    text = text or ""
    skills = _skills(text)
    years = _years(text)
    roles = _roles(text)
    sectors = _sectors(text)
    seniorities = _seniority(text, years)
    edu_level, edu_field = _education(text)

    bits = []
    if roles:
        bits.append(roles[0].replace("_", " "))
    if skills:
        head = ", ".join(skills[:3]) + (f" (+{len(skills) - 3})" if len(skills) > 3 else "")
        bits.append(head)
    if years is not None:
        bits.append(f"~{years} yr{'s' if years != 1 else ''}")
    if sectors:
        bits.append(sectors[0])
    if edu_level:
        bits.append(education.LEVEL_LABELS[edu_level].lower()
                    + (f" in {edu_field}" if edu_field else ""))
    summary = "Detected: " + " · ".join(bits) if bits else "We couldn't detect much — add details yourself."

    return {
        "skills": skills,
        "role_categories": roles,
        "seniorities": seniorities,
        "sectors": sectors,
        "years_experience": years,
        # The highest qualification detected. The browser uses it to *pre-tick* the education
        # checkboxes so the person can see and correct it — it is deliberately not applied as
        # a filter server-side. See `merge_into_profile`.
        "education": edu_level,
        "education_field": edu_field,
        "summary": summary,
    }


def merge_into_profile(data: dict, signals: dict) -> dict:
    """Fold CV-derived signals into a subscription `data` dict for `create_email_subscription`.

    User-entered values win: CV signals only *add* to what the form already has, so a
    manual choice is never overwritten. Sets the storable CV columns (has_cv, cv_summary,
    years_experience). Returns the same dict (mutated) for convenience.
    """
    def _union(key: str, extra: list[str]) -> None:
        seen = list(data.get(key) or [])
        for v in extra:
            if v not in seen:
                seen.append(v)
        if seen:
            data[key] = seen

    _union("stack", signals.get("skills", []))
    _union("role_categories", signals.get("role_categories", []))
    _union("seniorities", signals.get("seniorities", []))
    _union("sectors", signals.get("sectors", []))

    # Field of study is additive and harmless: it reaches the AI matcher as context and is
    # never filtered on, so a wrong guess costs a slightly worse-phrased prompt.
    if not data.get("education_field") and signals.get("education_field"):
        data["education_field"] = signals["education_field"]
    # `education_levels` is deliberately NOT set from the CV, even though the level was
    # detected. Accepting levels "up to what you have" *narrows* the digest, so a CV that never
    # spells out a master's would silently delete every master-requiring role from it — a
    # filter the subscriber never chose, with nothing failing anywhere, which is the exact
    # failure mode CLAUDE.md's taxonomy rules exist to prevent. The detected level goes back to
    # the browser in `signals["education"]` and pre-ticks the boxes, where a human can see it.

    data["has_cv"] = True
    data["cv_summary"] = signals.get("summary")
    if data.get("years_experience") is None and signals.get("years_experience") is not None:
        data["years_experience"] = signals["years_experience"]
    return data
