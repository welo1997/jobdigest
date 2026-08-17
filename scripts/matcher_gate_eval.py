"""Does the matcher's model actually obey the hard gates? A fixed answer key, no database.

`matcher_model_ab.py` compares two models on live shortlists and needs production. This is the
other half: a small, deliberately adversarial fixture with a **known correct answer**, so a model
can be graded rather than compared. It exists because the interesting failure is not "fewer
picks" — it is a specific wrong pick that no other check in this repo can see:

- **Seniority has no SQL gate on the digest path at all** (CLAUDE.md: `_hard_gate` must never grow
  that predicate). The model is the entire enforcement, so a lead role reaching a junior-only
  subscriber is a silent product failure, not a logged error.
- **`seniority=unstated` is 70% of the live corpus and is NOT a mismatch.** The expensive way to
  fail a hard seniority rule is to over-apply it and drop two thirds of every pool. Both
  directions are therefore in the key.
- **Location binds only for postings that are not fully remote**, and "hybrid" is not remote.
  Being emailed an on-site job in Brno when you live in Prague is the failure the rule names.
- **Part-time-only is a ceiling, not a drop**: a full-time posting may score at most 5, so it can
  still appear on /matches without headlining an email.

Every candidate below is tagged `keep`, `drop` or `cap5`, and the grade is how many of those the
model got right. Costs a few cents per model per pass; the fixture is ~2k input tokens.

    ANTHROPIC_API_KEY=... python scripts/matcher_gate_eval.py \
        --models claude-haiku-4-5,claude-sonnet-5 --repeat 3

`--repeat` matters: `match_profile` sets no temperature, so one pass measures one sample. A gate
the model obeys two times in three is not a gate.

Reads no database and writes nothing. The fixture is synthetic — no subscriber data is involved.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from eval_common import abort_message, is_terminal_error, no_data_line

from service import matcher

logger = logging.getLogger("matcher_gate_eval")


def token_budget(model: str) -> int:
    """`max_tokens` for this model's call. Haiku 4.5 answers directly; every model above it
    thinks by default, and thinking is billed out of the *same* ceiling as the JSON — so the
    production 1500 can be spent before the answer starts, truncating it. Widening it for those
    models keeps this a comparison of judgement rather than of who fits in the box."""
    return 1500 if model.startswith("claude-haiku") else 8000


def _cand(pid: str, title: str, company: str, **kw) -> dict:
    """A shortlist row as `matcher._candidates_block` reads one. Absent = the posting said
    nothing, which is the same convention the live rows carry."""
    row = {"posting_id": pid, "title": title, "company": company,
           "location": kw.get("location"), "city": kw.get("city"),
           "country_code": kw.get("country_code", "CZ"),
           "remote_signal": kw.get("remote", False), "work_mode": kw.get("work_mode"),
           "region": kw.get("region", "cz"), "seniority": kw.get("seniority"),
           "work_type": kw.get("work_type", "full_time"),
           "is_part_time": kw.get("part_time", False), "salary_raw": kw.get("salary"),
           "description": kw.get("desc")}
    return row


# Expected verdicts: "keep" (must be picked, score >= 4), "drop" (must not be picked at all),
# "cap5" (may be picked, but scoring above 5 breaks the stated rule).
CASES: list[dict] = [
    {
        "name": "junior-only Python dev, Prague only",
        "profile": {
            "id": "fixture-a", "label": "Junior Python developer",
            "role_categories": ["software_engineering"], "stack": ["Python", "Django"],
            "seniorities": ["junior"], "countries": ["CZ"], "cities": ["cz:prague"],
            "remote_scope": "eu", "work_types": ["full_time"], "sectors": [],
        },
        "candidates": [
            (_cand("a1", "Junior Python Developer", "Alpha", location="Praha", city="prague",
                   seniority="junior", desc="Django, PostgreSQL, first job welcome."), "keep"),
            (_cand("a2", "Head of Engineering", "Beta", location="Praha", city="prague",
                   seniority="lead", desc="Lead a team of 12 Python engineers."), "drop"),
            (_cand("a3", "Senior Python Engineer", "Gamma", location="Praha", city="prague",
                   seniority="senior", desc="8+ years of Python, own the platform."), "drop"),
            (_cand("a4", "Python Developer", "Delta", location="Brno", city="brno",
                   desc="On-site in our Brno office, Python and Flask."), "drop"),
            (_cand("a5", "Python Developer (Fully Remote)", "Epsilon", location="Remote, EU",
                   country_code="DE", remote=True, work_mode="remote",
                   desc="Fully remote within the EU, Python and Django."), "keep"),
            # The expensive false negative: no level in the title, which is 70% of the corpus.
            (_cand("a6", "Backend Developer", "Zeta", location="Praha", city="prague",
                   desc="Python, REST APIs, small product team."), "keep"),
            (_cand("a7", "Internship: Python", "Eta", location="Praha", city="prague",
                   seniority="intern", desc="Three-month summer placement for students."),
             "drop"),
            # Czech title, same job as a1 — the language rule.
            (_cand("a8", "Vyvojar Python (junior)", "Theta", location="Praha", city="prague",
                   seniority="junior", desc="Hledame juniorniho Python vyvojare."), "keep"),
            (_cand("a9", "Junior Accountant", "Iota", location="Praha", city="prague",
                   seniority="junior", desc="Bookkeeping, invoices, month-end close."), "drop"),
            (_cand("a10", "Python Developer (Hybrid)", "Kappa", location="Ostrava",
                   city="ostrava", work_mode="hybrid",
                   desc="Hybrid, three days a week in our Ostrava office."), "drop"),
        ],
    },
    {
        "name": "part-time-only mid designer, Prague",
        "profile": {
            "id": "fixture-b", "label": "Part-time designer",
            "role_categories": ["design"], "stack": ["Figma"], "seniorities": ["mid"],
            "countries": ["CZ"], "cities": ["cz:prague"], "remote_scope": "country",
            "work_types": ["part_time"], "sectors": [], "part_time_only": True,
        },
        "candidates": [
            (_cand("b1", "Product Designer (part-time)", "Alpha", location="Praha",
                   city="prague", seniority="mid", part_time=True,
                   desc="20 hours a week, Figma, design system work."), "keep"),
            # Perfect on every axis except the one the subscriber actually stated.
            (_cand("b2", "Product Designer", "Beta", location="Praha", city="prague",
                   seniority="mid", desc="Full-time, 40 hours, Figma and user research."),
             "cap5"),
            (_cand("b3", "Head of Design", "Gamma", location="Praha", city="prague",
                   seniority="lead", part_time=True,
                   desc="Build and lead the design function."), "drop"),
        ],
    },
    {
        "name": "senior-only data engineer, remote EU",
        "profile": {
            "id": "fixture-c", "label": "Senior data engineer",
            "role_categories": ["data_engineering"], "stack": ["Python", "dbt", "Snowflake"],
            "seniorities": ["senior"], "countries": ["CZ"], "cities": [],
            "remote_scope": "eu", "work_types": ["full_time"], "sectors": [],
        },
        "candidates": [
            (_cand("c1", "Senior Data Engineer", "Alpha", location="Praha", city="prague",
                   seniority="senior", desc="dbt, Snowflake, Airflow. Deep IC role."), "keep"),
            # senior and lead are different kinds of job, not adjacent rungs.
            (_cand("c2", "Head of Data", "Beta", location="Praha", city="prague",
                   seniority="lead", desc="Own the data org, manage five engineers."), "drop"),
            (_cand("c3", "Graduate Data Analyst", "Gamma", location="Praha", city="prague",
                   seniority="entry_level", desc="Graduate scheme, SQL basics."), "drop"),
            (_cand("c4", "Data Engineer", "Delta", location="Praha", city="prague",
                   desc="dbt and Snowflake, mature data platform, IC role."), "keep"),
        ],
    },
]


def grade(picks: list[dict], expected: dict[str, str]) -> tuple[Counter, list[str]]:
    """Score one model pass against the answer key. Returns (counts, human-readable failures)."""
    got = {p["posting_id"]: p["score"] for p in picks}
    counts: Counter = Counter()
    failures: list[str] = []
    for pid, verdict in expected.items():
        if verdict == "keep":
            if pid in got:
                counts["keep_ok"] += 1
            else:
                counts["keep_missed"] += 1
                failures.append(f"MISSED {pid} (should have been picked)")
        elif verdict == "drop":
            if pid in got:
                counts["drop_leaked"] += 1
                failures.append(f"LEAKED {pid} at score {got[pid]} (should never be picked)")
            else:
                counts["drop_ok"] += 1
        else:                                             # cap5
            if pid in got and got[pid] > 5:
                counts["cap_breached"] += 1
                failures.append(f"OVER-SCORED {pid} at {got[pid]} (cap is 5)")
            else:
                counts["cap_ok"] += 1
    # Anything picked that the key never mentions would mean the fixture drifted from the cases.
    for pid in got.keys() - expected.keys():
        failures.append(f"UNKNOWN pick {pid} — fixture and key disagree")
    return counts, failures


def run(models: list[str], repeat: int) -> int:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    totals = {m: Counter() for m in models}
    usage = {m: {"input": 0, "output": 0, "cache_read": 0, "calls": 0} for m in models}
    #: Passes that actually returned something to grade. Without it the summary divides by a
    #: floor of 1 and reports "0% of must-keeps kept, 0 missed" — a grade computed from no data,
    #: which reads like a verdict. A harness that cannot say "I measured nothing" is worse than
    #: no harness.
    ok = {m: 0 for m in models}
    n_keep = sum(1 for c in CASES for _, v in c["candidates"] if v == "keep")
    n_drop = sum(1 for c in CASES for _, v in c["candidates"] if v == "drop")
    n_cap = sum(1 for c in CASES for _, v in c["candidates"] if v == "cap5")
    print(f"Answer key: {n_keep} must-keep, {n_drop} must-drop, {n_cap} score-capped "
          f"across {len(CASES)} profile(s); {repeat} pass(es) per model\n")

    for case in CASES:
        print(f"--- {case['name']}")
        shortlist = [c for c, _ in case["candidates"]]
        expected = {c["posting_id"]: v for c, v in case["candidates"]}
        for model in models:
            for r in range(repeat):
                try:
                    picks = matcher.match_profile(client, case["profile"], shortlist,
                                                  usage_acc=usage[model], model=model,
                                                  max_tokens=token_budget(model))
                except Exception as exc:                  # noqa: BLE001 — grade the rest
                    logger.error("%s pass %d failed: %s", model, r + 1, exc)
                    # A bad key, a model id this account cannot reach, or an exhausted balance
                    # fails identically on every remaining pass. Retrying it 26 more times buys
                    # nothing but a wall of identical errors above a summary computed from no
                    # data, so stop at the first one that cannot be transient.
                    if is_terminal_error(exc):
                        raise SystemExit(abort_message(exc))
                    continue
                ok[model] += 1
                counts, failures = grade(picks, expected)
                totals[model].update(counts)
                wrong = (counts["keep_missed"] + counts["drop_leaked"]
                         + counts["cap_breached"])
                status = "clean" if not wrong else f"{wrong} wrong"
                print(f"  {model} pass {r + 1}: {len(picks)} picks, {status}")
                for line in failures:
                    print(f"      {line}")
        print()

    print("=" * 72)
    expected_passes = repeat * len(CASES)
    graded_any = False
    for model in models:
        if not ok[model]:
            print(no_data_line(model, expected_passes))
            continue
        graded_any = True
        t = totals[model]
        keep_rate = t["keep_ok"] / max(1, t["keep_ok"] + t["keep_missed"])
        drop_rate = t["drop_ok"] / max(1, t["drop_ok"] + t["drop_leaked"])
        cap_rate = t["cap_ok"] / max(1, t["cap_ok"] + t["cap_breached"])
        u = usage[model]
        partial = "" if ok[model] == expected_passes else f" [only {ok[model]} graded]"
        print(f"{model} over {ok[model]}/{expected_passes} pass(es){partial}: "
              f"kept {keep_rate:.0%} of must-keeps ({t['keep_missed']} missed), "
              f"dropped {drop_rate:.0%} of must-drops ({t['drop_leaked']} leaked), "
              f"respected the part-time cap {cap_rate:.0%} ({t['cap_breached']} breached); "
              f"{u['calls']} call(s), in={u['input']} out={u['output']}")
    if not graded_any:
        # Non-zero, because the caller of an eval that measured nothing must not read its exit
        # code as "the models are fine".
        print("\nNothing was graded — fix the failures above and re-run.")
        return 1
    print("\nA leak is the number that costs subscribers: nothing downstream of the model "
          "re-checks seniority, so a leaked lead role lands in an inbox.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="claude-haiku-4-5,claude-sonnet-5",
                    help="comma-separated model ids to grade")
    ap.add_argument("--repeat", type=int, default=3,
                    help="passes per model; one pass measures one sample of a sampling model")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(levelname)s %(name)s: %(message)s")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        ap.error("--models needs at least one model id")
    sys.exit(run(models, max(1, args.repeat)))


if __name__ == "__main__":
    main()
