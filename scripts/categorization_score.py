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

    python scripts/categorization_score.py --fetch      # refresh the Swedish key (network)
    python scripts/categorization_score.py --fetch-cz   # build/refresh the Czech key (network)
    python scripts/categorization_score.py              # score every cached key, per slice
    python scripts/categorization_score.py --titles F   # also report coverage on a corpus dump

**Two answer keys, scored separately, and never averaged into one number.** Until
2026-08-09-c there was only the Swedish one, which meant "how good is classification" really
meant "how good is it in one of the three languages that feed it" — Czech (MPSV, ISCO-08) and
Norwegian were entirely unmeasured, and could have been at 30% with nothing to say so. MPSV
publishes the same kind of ground truth SSYK does: an occupation code assigned by the
publisher, not derived from the title. An average across the two would hide the trade this
gate exists to catch — a change that lifts Swedish while sinking Czech — so **the rule
"accuracy may not fall" applies to each slice on its own.**

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
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service import taxonomy  # noqa: E402

CACHE = Path(os.environ.get("CATEGORIZATION_TRUTH", ROOT / "notes" / "categorization" /
                            "ssyk_truth.json"))
CZ_CACHE = Path(os.environ.get("CATEGORIZATION_TRUTH_CZ", ROOT / "notes" / "categorization" /
                               "isco_truth.json"))
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
    # `social_care` (2026-08-10) is graded on the CZ key (ISCO 2635/3412), NOT here: the SSYK
    # "social inriktning" field is dominated by personal-care assistants the taxonomy files under
    # healthcare, so grading it as social_care would measure an unmade healthcare/social boundary
    # decision, not the classifier. `cybersecurity` and `science_research` are likewise
    # English/CZ-measured — SSYK does not isolate them.
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


#: ISCO-08 prefix → category, for the Czech register (`profeseCzIsco`). Longest prefix wins,
#: so a 4-digit unit group overrides its 3-digit minor group and that overrides its sub-major.
#:
#: **Written here rather than imported from `mpsv.ISCO_CATEGORIES`, and that is not
#: duplication for its own sake.** That map is an *input* to the classifier — it becomes the
#: `source_category` hint. Grading the classifier against the same table that feeds it would
#: make the two agree by construction and the key could never contradict the adapter, which
#: is the one thing an answer key is for. It also covers only ISCO majors 1–3, the white-collar
#: slice the adapter ingests; the sector categories added on 2026-08-09 live in majors 4–9 and
#: would have no ground truth at all.
#:
#: The exclusions below follow the same rule as the Swedish key: a group with no counterpart
#: in `taxonomy.CATEGORIES` is left out, never mapped to the nearest thing. Architects (216),
#: physical scientists (211) and statisticians (212) are the notable ones — the same judgement
#: that keeps `Planeringsarkitekter` out of the Swedish key.
ISCO_MAP: dict[str, str] = {
    # 1 managers
    "121": "operations", "122": "sales",
    "1321": "manufacturing_production", "1323": "construction",
    "1324": "logistics_transport",
    "1342": "healthcare", "1343": "healthcare", "1345": "education",
    "1341": "education", "1346": "finance_accounting",
    "141": "hospitality", "142": "sales",
    # 2 professionals
    "211": "science_research", "213": "science_research",   # physical/earth + life scientists
    "214": "engineering", "215": "engineering",       # engineering, incl. electrotechnology
    "22": "healthcare", "23": "education",
    "241": "finance_accounting", "2423": "hr_recruiting", "2424": "hr_recruiting",
    "2431": "marketing", "2432": "marketing", "2433": "sales", "2434": "sales",
    "251": "software_engineering", "252": "devops_platform",
    "261": "legal", "2635": "social_care",            # social work and counselling professionals
    # 3 technicians and associate professionals
    "311": "engineering", "3122": "manufacturing_production", "3123": "construction",
    "313": "manufacturing_production",
    "32": "healthcare",
    "331": "finance_accounting", "332": "sales",
    "3412": "social_care",                             # social work associate professionals
    "3331": "logistics_transport", "3333": "hr_recruiting",
    "3432": "design", "3434": "hospitality",
    "351": "customer_support",                        # ICT operations and user support
    "3511": "devops_platform", "3513": "devops_platform",
    "3514": "software_engineering",
    # 4 clerical support workers
    "4222": "customer_support", "4225": "customer_support", "4226": "hospitality",
    "431": "finance_accounting",
    "4321": "logistics_transport", "4322": "manufacturing_production",
    "4323": "logistics_transport",
    # 5 service and sales workers
    "512": "hospitality", "513": "hospitality",
    "52": "sales",
    "531": "education", "532": "healthcare",
    # 7 craft and related trades workers
    "71": "construction",
    "721": "manufacturing_production", "722": "manufacturing_production",
    "723": "skilled_trades",                           # machinery mechanics and repairers
    "732": "manufacturing_production",
    "741": "skilled_trades", "742": "skilled_trades",  # electrical and electronic trades
    "751": "manufacturing_production", "752": "manufacturing_production",
    "753": "manufacturing_production",
    # 8 plant and machine operators, and assemblers
    "81": "manufacturing_production", "82": "manufacturing_production",
    "831": "logistics_transport", "832": "logistics_transport", "833": "logistics_transport",
    "834": "construction",                             # earthmoving and crane operators
    "8344": "logistics_transport",                     # lifting-truck (forklift) operators are
                                                       # warehouse work — the classifier reads
                                                       # "Skladník" as logistics and is right; the
                                                       # "834" construction prefix would grade that
                                                       # correct answer as wrong (82 rows, 2026-08-10)
    # 9 elementary occupations
    "931": "construction", "932": "manufacturing_production", "933": "logistics_transport",
    "94": "hospitality",
}

#: ISCO prefixes with no counterpart in `taxonomy.CATEGORIES`. Reported, never mapped to the
#: nearest thing — the module docstring's first deliberate property, one register down.
#: Longest prefix wins against `ISCO_MAP` too, which is how 7512 (bakers, inside a
#: manufacturing minor group) or 3121 (mining supervisors, inside an engineering one) opt out
#: without disturbing the group around them.
ISCO_OUT_OF_SCOPE: frozenset[str] = frozenset({
    "0",                          # armed forces
    "11", "131", "1322", "133", "1344", "1349", "143",   # managers with no category here
    "212", "216",                 # statisticians/mathematicians, architects (211/213 now science)
    "2421", "2422",               # management analysts, policy administration
    "262", "263", "264", "265",   # librarians, social/religious, journalists, artists
                                  #   (263 stays out; 2635 social work opts back in via ISCO_MAP)
    "3121", "314", "315",         # mining supervisors, life science techs, ship/air control
    "3332", "3334", "3339", "334", "335",   # events, real estate, secretaries, regulatory
    "341", "342", "343",          # legal/social associates, sports coaches, arts
    "352",                        # telecom and broadcasting technicians
    "41", "421", "4221", "4223", "4224", "4227", "4229", "44",
    "511", "514", "515", "516", "54",       # travel, beauty, housekeeping, protective
    "6",                          # agriculture, forestry, fishery
    "731", "754", "7512",         # handicraft, other craft, bakers (hospitality-adjacent)
    "835",                        # ships' deck crews
    "91", "92", "95", "96",       # cleaners, farm labourers, street vendors, refuse
})


def truth_for_isco(code: str) -> tuple[str | None, bool]:
    """(category, in_scope) for one CZ-ISCO code. Longest prefix wins.

    Returns `(None, False)` for a code the map deliberately excludes and `(None, True)` for
    one it simply does not cover — the caller counts those separately, because "no category
    exists for this work" and "the map has a hole" are different problems and only the second
    is a bug here.
    """
    code = (code or "").strip()
    for n in (4, 3, 2, 1):
        prefix = code[:n]
        if prefix in ISCO_OUT_OF_SCOPE:
            return None, False
        if prefix in ISCO_MAP:
            return ISCO_MAP[prefix], True
    return None, True


def truth_for(row: dict) -> str | None:
    """The publisher's answer for one ad, or None when it is out of scope or unmapped."""
    if row.get("isco"):
        return truth_for_isco(row["isco"])[0]
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


def fetch_cz(limit: int = 2000, bucket: int = 0, seed: int = 0) -> list[dict]:
    """Build the Czech answer key from MPSV's daily open-data dump.

    **Deliberately unfiltered by ISCO major.** `mpsv.ISCO_MAJOR_KEEP` ingests majors 1–3
    only — a dilution guard on what reaches subscribers, not a statement about what the
    classifier should be able to read. Sampling the same slice would leave every sector
    category added on 2026-08-09 with no Czech ground truth, which is the blind spot this
    whole key exists to remove.

    The adapter's own downloader and streaming parser are reused rather than reimplemented:
    the archive is 186 MB uncompressed and `_iter_objects` is what keeps peak memory at one
    record. Sampling is **seeded**, so two runs produce the same key and a change in the
    number can only come from the classifier or from the register.

    **`bucket` is the holdout, and it is a partition rather than a second sample.** Every
    vacancy is assigned to bucket 0 or 1 by a hash of the register's own immutable
    `portalId`, so the two keys are guaranteed disjoint and stay disjoint as the register
    changes. Patterns written by reading bucket 0's misses must be scored on bucket 1 or the
    number only measures memorisation — the lesson the Swedish `--before` slice records, made
    cheap enough here that there is no excuse for skipping it.
    """
    import hashlib
    import random

    from ingestion.sources import mpsv

    source = mpsv.MpsvSource()
    path = source._download()
    if path is None:
        print("  MPSV archive could not be downloaded", file=sys.stderr)
        return []

    # ~39 000 records in the register (documented in the adapter). Sampling probability is
    # derived from that rather than from a count, because counting means a second pass over
    # 186 MB to learn something the publisher already tells us.
    keep_p = min(1.0, limit / (39_000 / 2))
    rng = random.Random(seed)
    rows: list[dict] = []
    try:
        with open(path, "rb") as probe:
            gzipped = probe.read(2) == b"\x1f\x8b"
        opener = gzip.open if gzipped else open
        with opener(path, "rb") as handle:
            stream = iter(lambda: handle.read(1 << 20), b"")
            for item in mpsv._iter_objects(stream):
                title = mpsv._cs(item.get("pozadovanaProfese"))
                code = str((item.get("profeseCzIsco") or {}).get("id") or "").split("/")[-1]
                portal_id = str(item.get("portalId") or "")
                if not title or not code or not portal_id:
                    continue
                # The partition, before the sampling: which half a vacancy belongs to is a
                # property of the vacancy, not of this run.
                digest = hashlib.md5(portal_id.encode()).digest()[0]
                if digest % 2 != bucket % 2 or rng.random() >= keep_p:
                    continue
                # Title and code only. The register's `profeseCzIsco` carries an id and no
                # label, so a `profession` column here would be an always-empty field that
                # reads like missing data rather than data that was never there.
                rows.append({"title": title.strip(), "isco": code})
    except (OSError, EOFError) as exc:
        print(f"  MPSV archive unreadable ({exc}) — {len(rows)} parsed", file=sys.stderr)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
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
            if row.get("isco"):
                in_scope = truth_for_isco(row["isco"])[1]
            else:
                in_scope = not ((row.get("field") or "") in OUT_OF_SCOPE
                                or (row.get("group") or "").strip() in OUT_OF_SCOPE_GROUPS)
            if in_scope:
                unmapped += 1
            else:
                out_of_scope += 1
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


def report(label: str, rows: list[dict], titles: Path | None = None) -> None:
    """One slice, reported on its own. There is deliberately no combined figure."""
    s = score(rows)
    print(f"\ncategorisation score — {label}")
    print("=" * 62)
    print(f"  answer key            {len(rows):,} ads")
    print(f"  graded                {s['graded']:,}   (out of scope {s['out_of_scope']:,}, "
          f"unmapped {s['unmapped']:,})")
    if not s["graded"]:
        print("  ACCURACY              n/a — nothing in this key is gradable\n")
        return
    print(f"  ACCURACY              {s['accuracy']:.1%}   ({s['correct']:,}/{s['graded']:,})")
    print(f"    of the misses, still uncategorised: {s['missed_as_uncategorised']:,}")

    if titles and titles.exists():
        lines = [ln.rstrip("\n") for ln in
                 titles.read_text(encoding="utf-8").splitlines() if ln.strip()]
        uncat, total = coverage_of(lines)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fetch", action="store_true",
                    help="refresh the Swedish (SSYK) answer key from the API")
    ap.add_argument("--pages", type=int, default=20, help="100 ads per page (default 20)")
    ap.add_argument("--before", help="ISO timestamp: only ads published before it. With "
                                     "CATEGORIZATION_TRUTH pointing elsewhere, this is how a "
                                     "holdout key disjoint from the cached one is built")
    ap.add_argument("--fetch-cz", action="store_true",
                    help="build the Czech (ISCO) answer key from the MPSV open-data dump")
    ap.add_argument("--cz-limit", type=int, default=2000,
                    help="how many Czech vacancies to sample (default 2000)")
    ap.add_argument("--cz-bucket", type=int, default=0, choices=(0, 1),
                    help="which half of the register to sample: 0 is the working key, 1 is "
                         "the disjoint holdout. Score Czech pattern work on bucket 1")
    ap.add_argument("--titles", type=Path,
                    help="a file of production titles (one per line) to report coverage on")
    args = ap.parse_args(argv)

    if args.fetch:
        rows = fetch(args.pages, args.before)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"answer key (SE): {len(rows)} ads -> {CACHE}")
    if args.fetch_cz:
        rows = fetch_cz(args.cz_limit, args.cz_bucket)
        CZ_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CZ_CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"answer key (CZ): {len(rows)} vacancies -> {CZ_CACHE}")

    # Both slices, separately. Averaging them would hide the trade this gate exists to catch:
    # a change that lifts one language while sinking another.
    keys = [("Swedish register (SSYK)", CACHE), ("Czech register (ISCO-08)", CZ_CACHE)]
    found = [(label, path) for label, path in keys if path.exists()]
    if not found:
        print(f"no answer key at {CACHE} or {CZ_CACHE}; run --fetch / --fetch-cz first",
              file=sys.stderr)
        return 2
    for label, path in found:
        rows = json.loads(path.read_text(encoding="utf-8"))
        report(label, rows, args.titles)
        # The Czech key covers the whole labour market on purpose (see `fetch_cz`), but
        # `mpsv.ISCO_MAJOR_KEEP` ingests majors 1-3 only. Reporting one number would answer
        # neither question honestly: the whole-register figure says how well Czech titles are
        # read, and the ingested slice says what a subscriber is affected by *today*. A gap
        # between them is information, not noise.
        ingested = [r for r in rows if str(r.get("isco", ""))[:1] in "123"]
        if ingested and len(ingested) != len(rows):
            report(f"{label} — the slice MPSV actually ingests (majors 1-3)",
                   ingested, args.titles)
    if len(found) < len(keys):
        missing = [str(p) for label, p in keys if not p.exists()]
        print(f"  (not scored: {', '.join(missing)} — that language is unmeasured, which is "
              f"not the same as fine)\n", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
