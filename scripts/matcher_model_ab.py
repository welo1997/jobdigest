"""Is the metered Haiku matcher as good as the claude.ai routine was? Measure it, don't argue it.

Session 22 (2026-08-17) moved matching from a claude.ai cloud routine — subscription compute,
whatever model the account was on — to a metered `ANTHROPIC_API_KEY` on the VPS running
`claude-haiku-4-5`. The session note recorded "this is not a quality downgrade" as an *argument*
(Haiku was already the documented scoring model), not as a measurement, and production cannot
settle it either way: `digest_runs.picks_n` reads the same whether the model picked well or badly,
and the cutover left a single overlapping day across three subscribers. A pick count is not a
quality signal.

This harness makes the comparison the only way it can honestly be made: **one shortlist, several
models, the same prompt.** It builds each subscriber's shortlist exactly once, so every model
reads byte-identical input, then calls `matcher.match_profile` per model — the real prompt object,
not a copy of it — and reports where the models disagree.

It writes nothing: no `matches` row, no `digest_runs` row, no email, no `matcher.run()`. It reads
the database and calls the API. Safe to point at production.

    DATABASE_URL=... ANTHROPIC_API_KEY=... python scripts/matcher_model_ab.py \
        --models claude-haiku-4-5,claude-sonnet-5 --repeat 2

Three things matter here, and "how many picks" is none of them:

- **Hard-gate violations.** The digest path deliberately does not gate seniority in SQL at all
  (CLAUDE.md: `_hard_gate` must never grow that predicate, or two thirds of every candidate pool
  vanishes), so on that axis the model is the *only* enforcement that exists. A model that
  quietly sends a junior-only subscriber lead roles is worse in the way that costs subscribers,
  whatever its pick count says. Location and part-time are audited the same way, against the same
  rules the prompt states.
- **Misses.** Postings the stronger model scored 8+ that the cheaper one did not pick at all.
  That is the recall question, and in production it is invisible for ever — nobody can click a
  job they were never sent.
- **Self-agreement.** `--repeat 2` runs the same model twice. `match_profile` sets no
  temperature, so every call samples; without this number, "the two models agreed on 70%" cannot
  be distinguished from a model agreeing with itself 70% of the time.

The audit deliberately reports `unverifiable` rather than folding an unknown into either column.
A posting whose city never resolved (`city=?`) is one the prompt asks the model to judge from raw
location text — we cannot check that from columns, and scoring it as a pass would flatter every
model equally while scoring it as a violation would punish the one that read the text correctly.
Same rule as everywhere else in this repo: a lookup table in front of the matcher must be able to
say "I don't know".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from eval_common import abort_message, is_terminal_error, no_data_line

from service import geo, matcher, store

logger = logging.getLogger("matcher_model_ab")

#: Only for the cost line at the end of a run, never a gate — same convention as
#: `matcher._PRICE_IN`. List prices in $/token; Sonnet 5 carries an introductory rate through
#: 2026-08-31 that this deliberately ignores, so the figure is an upper bound rather than a
#: number that changes under us on a date nobody remembers.
PRICES = {
    "claude-haiku-4-5": (1.0e-6, 5.0e-6),
    "claude-sonnet-5": (3.0e-6, 15.0e-6),
    "claude-sonnet-4-6": (3.0e-6, 15.0e-6),
    "claude-opus-5": (5.0e-6, 25.0e-6),
    "claude-opus-4-8": (5.0e-6, 25.0e-6),
}

#: A pick at or above this score is one the digest would plausibly *headline*, so a
#: disagreement here is the one that reaches an inbox. `digest.EMAIL_MIN_SCORE` is the real
#: bar; this is deliberately its own constant because the question being asked is "did the
#: cheaper model miss something good", not "what would today's email have contained".
STRONG = 8


def token_budget(model: str) -> int:
    """`max_tokens` for this model's call. Haiku 4.5 answers directly; every model above it
    thinks by default, and thinking is billed out of the *same* ceiling as the JSON — so the
    production 1500 can be spent before the answer starts. Widening it for those models keeps
    this a comparison of judgement rather than of who fits in the box."""
    return 1500 if model.startswith("claude-haiku") else 8000


# --------------------------------------------------------------------------- rule audit

def _location_rules(profile: dict) -> tuple[set[str], dict[str, set[str]]]:
    """(allowed countries, {COUNTRY -> allowed city slugs}) as the prompt states them.

    An empty country set means the profile predates city-level preferences and falls back to
    `regions` — `_profile_block` shows it a coarse bucket instead, so there is no column-level
    rule to audit against and the caller skips the check rather than inventing one.
    """
    countries = set(geo.clean_countries(profile.get("countries")))
    cities: dict[str, set[str]] = {}
    for value in geo.clean_cities(profile.get("cities"), profile.get("countries")):
        country, slug = geo.split_city(value)
        if country:
            cities.setdefault(country, set()).add(slug)
    return countries, cities


def _reach_countries(posting_ids: list[str]) -> dict[str, list[str]]:
    """`postings.reach_countries` for the picked rows — the reason a country test can't be naive.

    A posting naming two or more countries answers a country filter for each of them
    (`geo.reach_countries`, ORed into the country test in `store._search_where`), and the
    shortlist SELECT does not carry the column. Auditing on `country_code` alone would therefore
    flag a Printful-shaped row listing twelve countries as an out-of-country pick — a false
    accusation aimed at exactly the model that read the posting correctly.
    """
    if not posting_ids:
        return {}
    with store.cursor() as cur:
        cur.execute("select posting_id, reach_countries from postings "
                    "where posting_id = any(%s)", (posting_ids,))
        return {r["posting_id"]: list(r["reach_countries"] or []) for r in cur.fetchall()}


def audit(profile: dict, picks: list[dict], by_id: dict[str, dict],
          reach: dict[str, list[str]]) -> tuple[list[dict], Counter]:
    """Check each pick against the three hard rules the SYSTEM prompt states.

    Returns (findings, counter). A finding is `{kind, posting_id, title, detail}` where `kind`
    is one of `seniority`, `country`, `city`, `part_time`, or `unverifiable`.
    """
    findings: list[dict] = []
    counts: Counter = Counter()
    targets = {str(s) for s in (profile.get("seniorities") or [])}
    allowed_countries, allowed_cities = _location_rules(profile)
    part_time_only = bool(profile.get("part_time_only"))

    for pick in picks:
        cand = by_id.get(pick["posting_id"])
        if cand is None:                      # can't happen: index_map came from this shortlist
            continue
        label = f"{cand.get('title') or '?'} @ {cand.get('company') or '?'}"

        # Bound as defaults rather than closed over: this is a per-iteration helper, and a
        # closure over the loop variable is the kind of thing that keeps working until someone
        # collects the findings lazily.
        def flag(kind: str, detail: str, pick: dict = pick, label: str = label) -> None:
            counts[kind] += 1
            findings.append({"kind": kind, "posting_id": pick["posting_id"],
                             "title": label, "score": pick["score"], "detail": detail})

        # Seniority. The one axis with no SQL gate anywhere on the digest path, so this is the
        # whole enforcement. A candidate whose level the title never named is stored NULL and
        # shown as `unstated` — 70% of the corpus, and explicitly not a mismatch.
        level = cand.get("seniority")
        if targets and level and level not in targets:
            flag("seniority", f"posting is {level}, subscriber targets {sorted(targets)}")

        # Location. Only a fully remote posting is exempt; hybrid is not remote.
        if not cand.get("remote_signal") and allowed_countries:
            reaches = {c for c in ([cand.get("country_code")] + reach.get(pick["posting_id"], []))
                       if c}
            reaches = {c.upper() for c in reaches}
            hit = reaches & allowed_countries
            if not reaches:
                flag("unverifiable", "posting resolved to no country; judged from location text")
            elif not hit:
                flag("country", f"on-site in {sorted(reaches)}, "
                                f"subscriber can work in {sorted(allowed_countries)}")
            else:
                # City only bites where the subscriber narrowed one *and* the posting resolved
                # one. An unresolved city is the `city=?` case the prompt hands to the model.
                narrowed = set().union(*(allowed_cities.get(c, set()) for c in hit)) \
                    if any(c in allowed_cities for c in hit) else set()
                slug = cand.get("city")
                if narrowed and not slug:
                    flag("unverifiable",
                         f"city unresolved ({cand.get('location') or '?'}); "
                         f"subscriber narrowed to {sorted(narrowed)}")
                elif narrowed and slug not in narrowed:
                    flag("city", f"on-site in {slug}, subscriber chose {sorted(narrowed)}")

        # Part-time. Not a drop but a ceiling: the prompt caps a full-time posting at 5 so it
        # can still appear on /matches without headlining the email.
        if part_time_only and not cand.get("is_part_time") and pick["score"] > 5:
            flag("part_time", f"full-time scored {pick['score']}, cap is 5")

    return findings, counts


# --------------------------------------------------------------------------- comparison

def _ids(picks: list[dict], min_score: int = 0) -> set[str]:
    return {p["posting_id"] for p in picks if p["score"] >= min_score}


def _overlap(a: set[str], b: set[str]) -> float:
    """Jaccard. Two empty pick sets agree completely, which is the honest reading."""
    union = a | b
    return 1.0 if not union else len(a & b) / len(union)


def compare(runs: dict[tuple[str, int], list[dict]], by_id: dict[str, dict],
            baseline: str, models: list[str], repeat: int) -> list[str]:
    """Lines describing how each model's run 1 differs from the baseline's run 1."""
    out: list[str] = []
    base = runs.get((baseline, 0), [])
    base_ids = _ids(base)
    base_scores = {p["posting_id"]: p["score"] for p in base}

    for model in models:
        if repeat > 1:
            self_ids = [_ids(runs.get((model, r), [])) for r in range(repeat)]
            pairs = [_overlap(self_ids[i], self_ids[j])
                     for i in range(repeat) for j in range(i + 1, repeat)]
            out.append(f"  {model}: self-agreement over {repeat} runs "
                       f"{sum(pairs) / len(pairs):.0%}  (sampling noise floor)")
        if model == baseline:
            continue
        other = runs.get((model, 0), [])
        other_ids = _ids(other)
        other_scores = {p["posting_id"]: p["score"] for p in other}
        # "Missed" is asymmetric on purpose: what the baseline failed to surface that the
        # comparison model rated highly is the recall loss a subscriber would never see.
        missed = [pid for pid in other_ids - base_ids if other_scores[pid] >= STRONG]
        extra = [pid for pid in base_ids - other_ids if base_scores[pid] >= STRONG]
        shared = base_ids & other_ids
        drift = ([base_scores[p] - other_scores[p] for p in shared] or [0])
        out.append(f"  {baseline} vs {model}: overlap {_overlap(base_ids, other_ids):.0%}; "
                   f"{len(missed)} strong pick(s) only {model} found, "
                   f"{len(extra)} only {baseline} found; "
                   f"mean score delta on the {len(shared)} shared "
                   f"{sum(drift) / len(drift):+.1f}")
        for pid in missed[:5]:
            cand = by_id.get(pid, {})
            out.append(f"      MISSED by {baseline} (score {other_scores[pid]} from {model}): "
                       f"{cand.get('title') or '?'} @ {cand.get('company') or '?'}")
    return out


# --------------------------------------------------------------------------- driver

def profiles_for(email: str | None, limit: int | None) -> list[dict]:
    if email:
        with store.cursor() as cur:
            cur.execute("select * from profiles where lower(email) = %s "
                        "order by created_at desc limit 1", (email.lower(),))
            row = cur.fetchone()
        rows = [dict(row)] if row else []
    else:
        rows = store.sendable_profiles()
    return rows[:limit] if limit else rows


def run(models: list[str], email: str | None, limit_profiles: int | None,
        shortlist_size: int, repeat: int, out_path: str | None) -> int:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    usage = {m: {"input": 0, "output": 0, "cache_read": 0, "calls": 0} for m in models}
    totals: dict[str, Counter] = {m: Counter() for m in models}
    picks_seen: dict[str, list[int]] = {m: [] for m in models}
    #: Calls that returned something. A model with zero of these has not scored zero — it has
    #: not been measured, and the summary must say so rather than print a number.
    ok = {m: 0 for m in models}
    report: list[str] = []
    dump: list[dict] = []

    profiles = profiles_for(email, limit_profiles)
    if not profiles:
        logger.error("No profiles to compare (use --email, or check sendable_profiles)")
        return 1
    print(f"Comparing {models} over {len(profiles)} profile(s), {repeat} run(s) each\n")

    for profile in profiles:
        # ONE shortlist for every model and every repeat. `already_sent_ids` is applied here
        # exactly as `_match_and_store` applies it, so the comparison runs on the window the
        # live matcher would really have been handed rather than on raw retrieval.
        shortlist, meta = store.query_shortlist_meta(profile, limit=shortlist_size)
        already = store.already_sent_ids(profile["id"]) if profile.get("id") else set()
        shortlist = [c for c in shortlist if c["posting_id"] not in already]
        by_id = {c["posting_id"]: c for c in shortlist}
        head = (f"profile {str(profile.get('id'))[:8]}  "
                f"{len(shortlist)} candidates"
                f"{' [WIDENED]' if meta.get('widened') else ''}")
        report.append(head)
        print(head)
        if not shortlist:
            report.append("  no candidates — nothing to compare")
            continue

        runs: dict[tuple[str, int], list[dict]] = {}
        for model in models:
            for r in range(repeat):
                try:
                    picks = matcher.match_profile(client, profile, shortlist,
                                                  usage_acc=usage[model], model=model,
                                                  max_tokens=token_budget(model))
                except Exception as exc:              # noqa: BLE001 — one model failing must
                    logger.error("%s run %d failed: %s", model, r + 1, exc)  # not end the run
                    # ...unless it cannot be transient, in which case every later call is the
                    # same error and the comparison would report a model as picking nothing.
                    if is_terminal_error(exc):
                        raise SystemExit(abort_message(exc))
                    picks = []
                else:
                    ok[model] += 1
                runs[(model, r)] = picks

        reach = _reach_countries(
            sorted({pk["posting_id"] for picks in runs.values() for pk in picks}))
        for model in models:
            picks = runs[(model, 0)]
            findings, counts = audit(profile, picks, by_id, reach)
            totals[model].update(counts)
            picks_seen[model].append(len(picks))
            mean = sum(p["score"] for p in picks) / len(picks) if picks else 0.0
            hard = sum(v for k, v in counts.items() if k != "unverifiable")
            line = (f"  {model}: {len(picks)} picks, mean score {mean:.1f}, "
                    f"{hard} hard-rule violation(s), {counts['unverifiable']} unverifiable")
            report.append(line)
            print(line)
            for f in findings:
                if f["kind"] == "unverifiable":
                    continue
                detail = f"      {f['kind'].upper()}: {f['title']} — {f['detail']}"
                report.append(detail)
                print(detail)
            dump.append({"profile_id": str(profile.get("id")), "model": model,
                         "picks": picks, "findings": findings})

        for line in compare(runs, by_id, models[0], models, repeat):
            report.append(line)
            print(line)
        print()

    print("=" * 72)
    expected_calls = len(profiles) * repeat
    for model in models:
        if not ok[model]:
            print(no_data_line(model, expected_calls))
            continue
        u = usage[model]
        p_in, p_out = PRICES.get(model, (0.0, 0.0))
        cost = u["input"] * p_in + u["output"] * p_out
        n = picks_seen[model]
        hard = sum(v for k, v in totals[model].items() if k != "unverifiable")
        print(f"{model}: {sum(n)} picks over {len(n)} profile(s) "
              f"(mean {sum(n) / len(n):.1f}/subscriber); "
              f"{hard} hard-rule violation(s) {dict(totals[model])}; "
              f"{u['calls']} call(s), in={u['input']} cache_read={u['cache_read']} "
              f"out={u['output']} ~ ${cost:.4f}"
              + ("" if model in PRICES else "  [price unknown, cost shown as $0]"))
    if not any(ok.values()):
        # Non-zero, so a caller cannot read "nothing was measured" as "the models agree".
        print("\nNothing was compared — fix the failures above and re-run.")
        return 1
    print("\nA hard-rule violation is the number to read first: seniority has no SQL gate, so "
          "the model is the only thing standing between a junior-only subscriber and a lead role.")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"models": models, "results": dump}, f, ensure_ascii=False, indent=1)
        print(f"\nRaw picks + findings written to {out_path}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="claude-haiku-4-5,claude-sonnet-5",
                    help="comma-separated; the FIRST is the baseline everything is compared to")
    ap.add_argument("--email", help="compare on one subscriber only")
    ap.add_argument("--limit-profiles", type=int)
    ap.add_argument("--shortlist", type=int, default=matcher.SHORTLIST_SIZE)
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per model; >1 measures each model's agreement with itself, which "
                         "is the noise floor any cross-model overlap has to beat")
    ap.add_argument("--json", dest="out_path", metavar="FILE",
                    help="write raw picks and findings for later inspection")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(levelname)s %(name)s: %(message)s")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        ap.error("--models needs at least one model id")
    sys.exit(run(models, args.email, args.limit_profiles, args.shortlist,
                 max(1, args.repeat), args.out_path))


if __name__ == "__main__":
    main()
