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

from service import taxonomy

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
    "r": r"(?<![a-z])r(?![a-z])", "java": r"\bjava\b", "scala": r"\bscala\b",
    "typescript": r"\btypescript\b", "javascript": r"\bjavascript\b", "react": r"\breact\b",
    "excel": r"\bexcel\b", "git": r"\bgit\b", "fivetran": r"\bfivetran\b",
    "metabase": r"\bmetabase\b", "pytorch": r"\bpytorch\b", "tensorflow": r"\btensorflow\b",
    "scikit-learn": r"scikit[- ]?learn|sklearn",
}

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


def extract_signals(text: str) -> dict:
    """Turn raw CV text into storable profile signals. Never returns raw CV content."""
    text = text or ""
    skills = _skills(text)
    years = _years(text)
    roles = _roles(text)
    sectors = _sectors(text)
    seniorities = _seniority(text, years)

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
    summary = "Detected: " + " · ".join(bits) if bits else "We couldn't detect much — add details yourself."

    return {
        "skills": skills,
        "role_categories": roles,
        "seniorities": seniorities,
        "sectors": sectors,
        "years_experience": years,
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

    data["has_cv"] = True
    data["cv_summary"] = signals.get("summary")
    if data.get("years_experience") is None and signals.get("years_experience") is not None:
        data["years_experience"] = signals["years_experience"]
    return data
