#!/usr/bin/env python3
"""How well is `role_category` actually assigned — coverage *and* correctness.

**This is the gate for `notes/categorization/PLAN.md`, and it exists because the obvious
metric is the wrong one.** "Percent uncategorised" is trivially gamed: assign everything to
something and it reaches zero, having made the product strictly worse, with the headline
number improving the whole way. A loop optimising it would do exactly that and report success.

So two numbers are reported together and one has a veto:

  coverage   1 − (uncategorised ÷ total). Easy to move, easy to fake.
  accuracy   agreement with ground truth the classifier cannot see. Hard to fake.

**Coverage may only rise if accuracy does not fall.**

Ground truth comes from Arbetsförmedlingen's JobSearch API, whose ads carry an occupation
code *assigned by the publisher* rather than derived from the title — a real answer key, free,
and independent of anything in this repo. The classifier under test sees only the title, so
scoring it against the code is not circular.

Accuracy is reported **per category** as well as overall, because an 85% average hides a
category that is 20% right, and that category is somebody's entire feed.

    python scripts/categorization_score.py --fetch      # refresh the answer key (network)
    python scripts/categorization_score.py              # score against the cached key
    python scripts/categorization_score.py --titles F   # also report coverage on a corpus dump

Two properties are deliberate and worth not undoing:

- **A field with no matching category is EXCLUDED from the answer key, never mapped to
  something close.** Swedish social work, sanitation, agriculture, security and beauty have no
  category in `taxonomy.CATEGORIES`, and forcing them somewhere would score the classifier
  against a wrong answer and reward it for guessing. They are counted and reported as
  out-of-scope so the number stays visible.
- **The map is field-level where a field means one thing and group-level where it does not.**
  "Data/IT" spans software, support and platform work, so a field-level map would grade three
  distinct categories as one.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service import taxonomy  # noqa: E402

CACHE = Path(os.environ.get("CATEGORIZATION_TRUTH", ROOT / "notes" / "categorization" /
                            "ssyk_truth.json"))
BASE_URL = "https://jobsearch.api.jobtechdev.se/search"

#: SSYK occupation *field* → category, for the fields that mean exactly one thing.
FIELD_MAP: dict[str, str] = {
    "Yrken med teknisk inriktning": "engineering",   # in scope since the 2026-08-09 category
    "Hälso- och sjukvård": "healthcare",
    "Pedagogik": "education",
    "Hotell, restaurang, storhushåll": "hospitality",
    "Industriell tillverkning": "manufacturing_production",
    "Transport, distribution, lager": "logistics_transport",
    "Bygg och anläggning": "construction",
    "Installation, drift, underhåll": "skilled_trades",
    "Hantverk": "skilled_trades",
}

#: SSYK occupation *group* → category, inside fields that span several categories.
#: "Data/IT" is the clearest case: software, IT support and platform work share one field.
GROUP_MAP: dict[str, str] = {
    # Data/IT
    "Mjukvaru- och systemutvecklare m.fl.": "software_engineering",
    "Systemanalytiker och IT-arkitekter m.fl.": "software_engineering",
    "Systemtestare och testledare": "software_engineering",
    "Utvecklare inom spel och digitala media": "software_engineering",
    "Supporttekniker, IT": "customer_support",
    "Systemförvaltare m.fl.": "devops_platform",
    "Nätverks- och systemtekniker m.fl.": "devops_platform",
    "Systemadministratörer": "devops_platform",
    # Försäljning, inköp, marknadsföring
    "Företagssäljare": "sales",
    "Butikssäljare, fackhandel": "sales",
    "Butikssäljare, dagligvaror": "sales",
    "Telefonförsäljare m.fl.": "sales",
    "Torg- och marknadsförsäljare m.fl.": "sales",
    "Eventsäljare och butiksdemonstratörer m.fl.": "sales",
    "Säljande butikschefer och avdelningschefer i butik": "sales",
    "Marknadsanalytiker och marknadsförare m.fl.": "marketing",
    "Informatörer, kommunikatörer och PR-specialister": "marketing",
    # Administration, ekonomi, juridik
    "Kundtjänstpersonal": "customer_support",
    "Personal- och HR-specialister": "hr_recruiting",
    "Redovisningsekonomer": "finance_accounting",
    "Löne- och personaladministratörer": "finance_accounting",
    "Revisorer m.fl.": "finance_accounting",
    "Jurister m.fl.": "legal",
    "Grafiska formgivare m.fl.": "design",
}

#: Fields with no counterpart in `taxonomy.CATEGORIES`. Excluded from the answer key rather
#: than mapped to the nearest thing — see the module docstring. Reported, not hidden.
OUT_OF_SCOPE: frozenset[str] = frozenset({
    "Yrken med social inriktning", "Sanering och renhållning", "Naturbruk",
    "Säkerhet och bevakning", "Kropps- och skönhetsvård", "Naturvetenskap",
    "Militära yrken", "Chefer och verksamhetsledare", "Kultur, media, design",
    # "Yrken med teknisk inriktning" left this set on 2026-08-09 — it now maps to engineering.
})

#: The same exclusion one level down: *groups* inside an in-scope field that the field's
#: category is wrong for. "Yrken med teknisk inriktning" is not homogeneous — alongside the
#: mechanical, electrical and chemical engineers it carries property managers
#: (`Fastighetsförvaltare`) and urban planners (`Planeringsarkitekter`), which are not
#: engineering and have no category of their own. Grading the classifier as *wrong* for
#: declining to call a property manager an engineer scores it against a wrong answer key and
#: rewards guessing — the same failure the field-level exclusion exists to prevent. Mirrored in
#: `platsbanken.SSYK_GROUP_UNMAPPED`, so the shipped hint declines on exactly these groups too.
OUT_OF_SCOPE_GROUPS: frozenset[str] = frozenset({
    "Fastighetsförvaltare", "Planeringsarkitekter m.fl.", "Arkitekter m.fl.",
})


def truth_for(row: dict) -> str | None:
    """The publisher's answer for one ad, or None when it is out of scope or unmapped."""
    group = (row.get("group") or "").strip()
    if group in GROUP_MAP:
        return GROUP_MAP[group]
    if group in OUT_OF_SCOPE_GROUPS:
        return None
    field = (row.get("field") or "").strip()
    if field in OUT_OF_SCOPE:
        return None
    return FIELD_MAP.get(field)


def fetch(pages: int = 20, before: str | None = None) -> list[dict]:
    """Pull a fresh answer key. Deliberately unfiltered by occupation field.

    Sampling only the seven fields `platsbanken.OCCUPATION_FIELDS` ingests would leave the
    seven sector categories added on 2026-08-09 with no ground truth at all — the scorer
    would be blind to exactly the newest and least-proven half of the taxonomy.

    `before` (an ISO timestamp, `--before`) asks the API for ads published earlier than that
    moment. **That is what makes a holdout possible, and a holdout is not optional here.**
    Patterns get written by reading the titles this key declined, so scoring them against the
    same key measures memorisation, not classification: every word added scores by
    construction. A run over a disjoint slice is the only number that says whether the
    vocabulary generalises. The API caps `offset` at 2 000, so a second sample cannot be had
    by paging further — it has to come from a different window.
    """
    import requests
    from ingestion import politeness

    headers = {"User-Agent": politeness.USER_AGENT, "accept": "application/json"}
    rows: list[dict] = []
    for offset in range(0, pages * 100, 100):
        politeness.throttle(BASE_URL)
        params: dict[str, object] = {"limit": 100, "offset": offset}
        if before:
            params["published-before"] = before
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=45)
        if resp.status_code != 200:
            print(f"  api returned {resp.status_code}, stopping", file=sys.stderr)
            break
        hits = resp.json().get("hits", [])
        if not hits:
            break
        for ad in hits:
            rows.append({
                "title": ad.get("headline") or "",
                "field": ((ad.get("occupation_field") or {}).get("label") or ""),
                "group": ((ad.get("occupation_group") or {}).get("label") or ""),
            })
    return rows


def score(rows: list[dict]) -> dict:
    graded = 0
    correct = 0
    out_of_scope = 0
    unmapped = 0
    per_cat: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])  # [correct, total]
    confusion: collections.Counter = collections.Counter()
    predicted_uncat = 0

    for row in rows:
        expected = truth_for(row)
        if expected is None:
            if ((row.get("field") or "") in OUT_OF_SCOPE
                    or (row.get("group") or "").strip() in OUT_OF_SCOPE_GROUPS):
                out_of_scope += 1
            else:
                unmapped += 1
            continue
        got = taxonomy.classify(row.get("title"))
        graded += 1
        per_cat[expected][1] += 1
        if got == expected:
            correct += 1
            per_cat[expected][0] += 1
        else:
            confusion[(expected, got)] += 1
            if got == taxonomy.UNCATEGORISED:
                predicted_uncat += 1

    return {
        "graded": graded, "correct": correct,
        "accuracy": correct / graded if graded else 0.0,
        "missed_as_uncategorised": predicted_uncat,
        "out_of_scope": out_of_scope, "unmapped": unmapped,
        "per_cat": {k: tuple(v) for k, v in per_cat.items()},
        "confusion": confusion,
    }


def coverage_of(rows: list) -> tuple[int, int]:
    """(uncategorised, total) for a corpus dump — coverage needs no answer key.

    Accepts bare titles or ``(title, hint)`` pairs. **The hint matters and accuracy
    deliberately ignores it.** A `source_category` from a publisher's occupation code
    (`mpsv.ISCO_CATEGORIES`, `platsbanken.SSYK_CATEGORIES`) is how the register sources get
    classified at all, so a title-only coverage number under-reports them — measured on
    2026-08-09, it missed 1 534 recovered Czech vacancies entirely and reported the change as
    doing nothing.

    Accuracy stays title-only, and that is not an oversight. Grading a hint-classified row
    against the same occupation code that produced the hint is circular: it would score 100%
    by construction and the gate would be decorative for exactly the sources it matters most
    for. Coverage asks "did we assign anything"; accuracy asks "is the title classifier
    right". Only the first can honestly see a hint.
    """
    uncat = 0
    for row in rows:
        title, hint = row if isinstance(row, (tuple, list)) else (row, None)
        if taxonomy.classify(title, hint) == taxonomy.UNCATEGORISED:
            uncat += 1
    return uncat, len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fetch", action="store_true", help="refresh the answer key from the API")
    ap.add_argument("--pages", type=int, default=20, help="100 ads per page (default 20)")
    ap.add_argument("--before", help="ISO timestamp: only ads published before it. With "
                                     "CATEGORIZATION_TRUTH pointing elsewhere, this is how a "
                                     "holdout key disjoint from the cached one is built")
    ap.add_argument("--titles", type=Path,
                    help="a file of production titles (one per line) to report coverage on")
    args = ap.parse_args(argv)

    if args.fetch:
        rows = fetch(args.pages, args.before)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"answer key: {len(rows)} ads -> {CACHE}")
    if not CACHE.exists():
        print(f"no answer key at {CACHE}; run with --fetch first", file=sys.stderr)
        return 2

    rows = json.loads(CACHE.read_text(encoding="utf-8"))
    s = score(rows)

    print("\ncategorisation score")
    print("=" * 62)
    print(f"  answer key            {len(rows):,} ads from the Swedish register")
    print(f"  graded                {s['graded']:,}   (out of scope {s['out_of_scope']:,}, "
          f"unmapped {s['unmapped']:,})")
    print(f"  ACCURACY              {s['accuracy']:.1%}   ({s['correct']:,}/{s['graded']:,})")
    print(f"    of the misses, still uncategorised: {s['missed_as_uncategorised']:,}")

    if args.titles and args.titles.exists():
        titles = [ln.rstrip("\n") for ln in
                  args.titles.read_text(encoding="utf-8").splitlines() if ln.strip()]
        uncat, total = coverage_of(titles)
        print(f"  COVERAGE              {1 - uncat / total:.1%}   "
              f"({total - uncat:,}/{total:,} classified)")

    print("\n  accuracy per category  (an average hides the one that is 20% right)")
    print("  " + "-" * 58)
    for cat, (ok, tot) in sorted(s["per_cat"].items(), key=lambda kv: -kv[1][1]):
        bar = "" if tot >= 20 else "   (thin sample)"
        print(f"    {cat:<28} {ok:4}/{tot:<5} {ok / tot:6.1%}{bar}")

    if s["confusion"]:
        print("\n  most common mistakes")
        print("  " + "-" * 58)
        for (exp, got), n in s["confusion"].most_common(10):
            print(f"    {exp:<26} -> {got:<26} {n:4}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
