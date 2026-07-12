"""Claude curation pass — pick the job offers genuinely worth sending today.

The personal scorer (``personal_scorer.py``) rates each posting in isolation.
This pass looks at the whole day's shortlist together: it drops near-duplicates
and marginal fits, ranks what's left best-first, and writes a one-line "why this
one today" note per pick. It runs after the liveness probe in ``notify.py``.

Degrades gracefully: on a missing API key, request failure, or unparseable
response it returns ``None`` and the caller falls back to sending the top matches
by score. It never raises, so it can never crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

CANDIDATE_PROFILE = (
    "Early-career data professional (~1-2 yrs), based in Czech Republic (EU). "
    "Core stack: dbt, Snowflake, Python, SQL. Working knowledge of Docker, Metabase. "
    "Strong interest in AI/ML tooling. MAIN focus: fully remote roles open to the "
    "EU / worldwide; CZ-based roles (remote/hybrid/on-site) are also welcome. "
    "Not eligible for roles requiring US/non-EU work authorization or non-EU on-site "
    "presence. Full-time or contract. Background in ecommerce (Footshop); bonus "
    "alignment with ecommerce or trading/fintech. Growing toward mid-level, open to "
    "stretch roles where the stack matches."
)

PROMPT_TEMPLATE = """You are the job-hunt assistant for this candidate:

{profile}

Below is today's shortlist of already-scored, confirmed-live postings (JSON).
Pick the ones genuinely worth the candidate's attention today. Rules:
- Choose at most {max_alerts} postings, ordered best-first.
- Drop near-duplicates (same role at the same company under different titles/sources) — keep the single best.
- Prefer fully-remote EU/worldwide roles; rank them above CZ-local ones when fit is comparable.
- Drop weak fits even if their score is borderline; quality over quantity. It is fine to return fewer than {max_alerts}, or none.
- For each pick, write ONE short sentence on why it's worth a look today (stack overlap, growth, location, company type) — concrete, not generic.

Shortlist:
{catalog}

Respond ONLY with a JSON array, best-first, of objects:
[{{"id": "<posting_id>", "note": "<one sentence>"}}]
No prose, no markdown fences."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def curate(matches: list[dict], max_alerts: int) -> Optional[list[dict]]:
    """Select + annotate the matches worth sending.

    Returns an ordered subset of the input dicts, each with an added
    ``curation_note`` key, or ``None`` if curation could not run (caller falls
    back). Returns ``[]`` only when there was nothing to curate.
    """
    if not matches:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("No ANTHROPIC_API_KEY set — skipping curation, caller will fall back.")
        return None

    catalog = [
        {
            "id": m["posting_id"],
            "title": m.get("title"),
            "company": m.get("company"),
            "score": m.get("personal_score"),
            "country": m.get("country_code"),
            "skills": (m.get("skills_csv") or "")[:200],
            "summary": (m.get("personal_summary") or "")[:300],
        }
        for m in matches
    ]
    prompt = PROMPT_TEMPLATE.format(
        profile=CANDIDATE_PROFILE,
        max_alerts=max_alerts,
        catalog=json.dumps(catalog, ensure_ascii=False, indent=1),
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        picks = json.loads(_strip_fences(resp.content[0].text))
        if not isinstance(picks, list):
            raise ValueError("expected a JSON array of picks")
    except Exception:
        logger.exception("Curation call failed — caller will fall back to score order.")
        return None

    by_id = {m["posting_id"]: m for m in matches}
    selected: list[dict] = []
    seen: set[str] = set()
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        pid = pick.get("id")
        if pid in by_id and pid not in seen:
            chosen = dict(by_id[pid])
            chosen["curation_note"] = str(pick.get("note") or "").strip()
            selected.append(chosen)
            seen.add(pid)
        if len(selected) >= max_alerts:
            break

    if not selected:
        logger.warning("Curation returned no usable picks — caller will fall back.")
        return None

    logger.info("Curation selected %d of %d live matches.", len(selected), len(matches))
    return selected
