#!/usr/bin/env python3
"""What one subscriber costs the matcher per day, and what that becomes at N subscribers.

**This is the instrument the whole scaling ladder is sized against** (`notes/scaling/PLAN.md`).
The matcher is one Claude call per subscriber over a ~120-posting shortlist, so cost is
`O(subscribers × shortlist)` and the per-subscriber payload is the unit that decides whether
10 000 subscribers is reachable on subscription compute. Everything else on the ladder — delta
matching, cohort matching, cached judgements, shrinking the shortlist — is an attempt to move
this number, and none of it can be judged without measuring it first.

Run it against the file the routine actually reads:

    python scripts/scaling_budget.py exchange/shortlists.json
    python scripts/scaling_budget.py exchange/shortlists.json --at 10,100,1000,10000
    python scripts/scaling_budget.py exchange/shortlists.json --count-tokens   # exact, needs creds

Four things are deliberate, and each is a way this measurement could quietly be wrong:

- **Bytes are measured, tokens are estimated — and the two are never conflated.** Bytes come
  from re-serialising the real payload; that is a fact. Tokens are a *band*, because Claude
  tokenisation is model-specific and this payload is the awkward case for any rule of thumb:
  JSON keys and md5 hex ids and Czech/Swedish/Norwegian titles all tokenise worse than English
  prose, and a chars/4 rule would under-count all three. `--count-tokens` replaces the band
  with `messages.count_tokens`. **Never substitute `tiktoken`** — it is OpenAI's tokenizer and
  under-counts Claude by 15–20% on plain text and by more on exactly this content.

- **An unrepresentative sample is flagged rather than averaged.** The dev database holds 40
  postings whose descriptions average 57 characters; production holds ~99k averaging 4 791, so
  essentially every real candidate hits `DESC_CHARS` and a dev-shaped sample understates the
  payload by roughly half. A confident number off that sample is the "green tests, wrong
  answer" failure this repo keeps writing down, so `representative` is part of the output and
  the report says so in the header.

- **`CANDIDATE_FIELDS` is checked against `matcher._candidate_export` by the test suite**, not
  copied and hoped over. A field added to the export and not here would make every projection
  under-report from that day on, with nothing failing — the same shape as
  `source_watchdog.py` reading `search_jobs.source_classes` rather than a copy.

- **It reports aggregates only.** `shortlists.json` carries no email address by design, but it
  does carry profile ids and posting text. Nothing here prints either, so a budget report is
  safe to paste into a note or an issue.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The *maximal* field set `matcher._candidate_export` writes — what a posting that stated
#: everything ships. Since 2026-08-12 the exporter omits a field the posting never stated
#: rather than writing null, so a real candidate carries a subset of these; the drift guard in
#: `service/tests/test_scaling_budget.py` therefore feeds it a fully-populated row. Pinned
#: against the real exporter so a rename or an added field fails loudly here instead of
#: silently shrinking every projection.
CANDIDATE_FIELDS = (
    "posting_id", "title", "company", "location", "city", "remote", "reach",
    "work_mode", "education_min", "seniority", "work_type", "part_time", "salary",
    "description",
)

#: Mirrors `matcher.DESC_CHARS`. Read from the matcher when importable so the two cannot
#: drift; the literal is the fallback for running this on a box without the package.
try:  # pragma: no cover - trivial import guard
    from service.matcher import DESC_CHARS, SHORTLIST_SIZE
except Exception:  # pragma: no cover
    DESC_CHARS, SHORTLIST_SIZE = 320, 120

#: Bytes-per-token band for *this* payload shape. Deliberately wide and deliberately worse
#: than the ~4.0 that plain English gets: JSON keys repeat every candidate, `posting_id` is
#: 32 hex characters (which tokenise close to 1 token per 2–3 chars), and three of the largest
#: sources are Czech, Swedish and Norwegian. Replaced by a measurement under `--count-tokens`.
CHARS_PER_TOKEN_LOW = 2.8    # pessimistic — more tokens per byte
CHARS_PER_TOKEN_HIGH = 3.8   # optimistic — fewer tokens per byte

#: Below this share of descriptions at the cap, the sample cannot stand in for production.
#: Production sits at ~95% carrying a description averaging 4 791 chars, so the real figure is
#: near 1.0; the dev seed is 0.0.
REPRESENTATIVE_DESC_SHARE = 0.5

DEFAULT_AT = (10, 100, 1_000, 10_000)

#: What actually bounds a shard of `shortlists.json`. `deploy/matcher-routine.md` has the
#: routine read the file and iterate every subscriber in one pass, so a shard has to fit in
#: one context window — the limit is tokens, not megabytes, and it therefore *falls* as the
#: per-subscriber payload grows. Sharding by file size would pick the wrong boundary.
CONTEXT_TOKENS = 1_000_000
#: Instructions, the model's own output, and headroom. Stated rather than hidden because it is
#: the one number here nobody has measured; the shard band moves linearly with it.
CONTEXT_USABLE_SHARE = 0.7


class NotMeasurable(Exception):
    """The file holds nothing to measure — refuse rather than divide by zero."""


@dataclass
class Measurement:
    n_profiles: int
    n_candidates: int
    total_bytes: int
    bytes_per_profile: float
    bytes_per_candidate: float
    candidates_per_profile: float
    desc_at_cap_share: float
    representative: bool
    warnings: list[str] = field(default_factory=list)
    #: Set only by --count-tokens; None means the band below is an estimate.
    measured_tokens_per_profile: float | None = None


@dataclass
class Projection:
    n: int
    tokens_per_day_low: float
    tokens_per_day_high: float
    bytes_per_day: float


def measure(payload: dict) -> Measurement:
    """Measure the real per-subscriber payload out of a parsed `shortlists.json`."""
    profiles = payload.get("profiles") or []
    if not profiles:
        raise NotMeasurable("no profiles in this export — nothing to measure")

    per_profile_bytes: list[int] = []
    n_candidates = 0
    n_desc_at_cap = 0
    n_desc_present = 0

    for profile in profiles:
        blob = json.dumps(profile, ensure_ascii=False).encode("utf-8")
        per_profile_bytes.append(len(blob))
        for candidate in profile.get("candidates") or []:
            n_candidates += 1
            desc = candidate.get("description")
            if desc:
                n_desc_present += 1
                # `_candidate_export` truncates at DESC_CHARS, so "at the cap" means the
                # posting had at least that much text to give.
                if len(desc) >= DESC_CHARS:
                    n_desc_at_cap += 1

    if not n_candidates:
        raise NotMeasurable("profiles carry no candidates — nothing to measure")

    total_bytes = sum(per_profile_bytes)
    desc_share = n_desc_at_cap / n_candidates
    warnings: list[str] = []

    if desc_share < REPRESENTATIVE_DESC_SHARE:
        warnings.append(
            f"only {desc_share:.0%} of candidates carry a description at the {DESC_CHARS}-char "
            f"cap ({n_desc_present}/{n_candidates} carry any description at all). Production "
            f"sits near 100%, so this sample UNDERSTATES the real payload — most likely a dev "
            f"or seeded database. Do not quote these numbers as production."
        )

    candidates_per_profile = n_candidates / len(profiles)
    if candidates_per_profile < SHORTLIST_SIZE * 0.5:
        warnings.append(
            f"{candidates_per_profile:.0f} candidates per profile against a SHORTLIST_SIZE of "
            f"{SHORTLIST_SIZE} — a thin inventory or a heavily filtered export, so the "
            f"per-subscriber cost here is below what a full shortlist would cost."
        )

    return Measurement(
        n_profiles=len(profiles),
        n_candidates=n_candidates,
        total_bytes=total_bytes,
        bytes_per_profile=total_bytes / len(profiles),
        bytes_per_candidate=total_bytes / n_candidates,
        candidates_per_profile=candidates_per_profile,
        desc_at_cap_share=desc_share,
        representative=not warnings,
        warnings=warnings,
    )


@dataclass
class ShardPlan:
    n: int
    per_shard_min: int      # when each subscriber costs the most
    per_shard_max: int      # when each costs the least
    shards_min: int
    shards_max: int


def shard_plan(m: Measurement, n: int) -> ShardPlan:
    """How many subscribers fit one routine invocation, and how many invocations that needs.

    **`ceil`, never floor.** Rounding the shard count down drops the remainder, and a
    subscriber who falls off the end of the plan is not an error anywhere — they simply never
    get matched. That is `sendable_profiles`' tail problem in a new place, and it is the kind
    of arithmetic that looks like a rounding detail right up until somebody stops receiving
    email.
    """
    import math

    usable = CONTEXT_TOKENS * CONTEXT_USABLE_SHARE
    if m.measured_tokens_per_profile is not None:
        lo = hi = m.measured_tokens_per_profile
    else:
        lo = m.bytes_per_profile / CHARS_PER_TOKEN_HIGH
        hi = m.bytes_per_profile / CHARS_PER_TOKEN_LOW
    if hi >= usable:
        raise NotMeasurable(
            f"one subscriber is ~{hi:,.0f} tokens against {usable:,.0f} usable — a single "
            f"subscriber does not fit one context window, so sharding is not the fix. Shrink "
            f"the shortlist or the description budget first."
        )
    per_max = int(usable // lo)
    per_min = int(usable // hi)
    return ShardPlan(n=n, per_shard_min=per_min, per_shard_max=per_max,
                     shards_min=math.ceil(n / per_max), shards_max=math.ceil(n / per_min))


def project(m: Measurement, n: int) -> Projection:
    """What N subscribers cost the matcher per day, at one call per subscriber per day."""
    if m.measured_tokens_per_profile is not None:
        low = high = m.measured_tokens_per_profile * n
    else:
        low = (m.bytes_per_profile / CHARS_PER_TOKEN_HIGH) * n
        high = (m.bytes_per_profile / CHARS_PER_TOKEN_LOW) * n
    return Projection(n=n, tokens_per_day_low=low, tokens_per_day_high=high,
                      bytes_per_day=m.bytes_per_profile * n)


def count_tokens_exactly(payload: dict, model: str) -> float:
    """Replace the band with a measurement. Requires Anthropic credentials.

    Deliberately opt-in: this repo runs its LLM work on subscription compute and keeps no
    `ANTHROPIC_API_KEY` in any workflow, so counting tokens is a thing a human does once on
    purpose — never something the pipeline reaches for on its own.
    """
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        raise SystemExit("--count-tokens needs the `anthropic` package installed")

    client = anthropic.Anthropic()
    per_profile: list[int] = []
    for profile in payload["profiles"]:
        blob = json.dumps(profile, ensure_ascii=False)
        resp = client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": blob}])
        per_profile.append(resp.input_tokens)
    return sum(per_profile) / len(per_profile)


def _fmt(n: float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def render(m: Measurement, at: list[int] | tuple[int, ...] = DEFAULT_AT) -> str:
    """The report. Aggregates only — no profile ids, no posting text."""
    exact = m.measured_tokens_per_profile is not None
    lines = ["", "JobDigest matcher budget", "=" * 58]

    if not m.representative:
        lines.append("")
        lines.append("  !! SAMPLE IS NOT PRODUCTION-SHAPED - see the warnings below.")

    lines += [
        "",
        f"  profiles measured        {m.n_profiles}",
        f"  candidates               {m.n_candidates}  ({m.candidates_per_profile:.0f} per profile)",
        f"  descriptions at cap      {m.desc_at_cap_share:.0%}",
        "",
        f"  bytes / subscriber       {_fmt(m.bytes_per_profile)}   (measured)",
        f"  bytes / candidate        {_fmt(m.bytes_per_candidate)}   (measured)",
    ]

    if exact:
        lines.append(
            f"  tokens / subscriber      {_fmt(m.measured_tokens_per_profile)}   (counted)")
    else:
        low = m.bytes_per_profile / CHARS_PER_TOKEN_HIGH
        high = m.bytes_per_profile / CHARS_PER_TOKEN_LOW
        lines += [
            f"  tokens / subscriber      {_fmt(low)} - {_fmt(high)}   (ESTIMATE, "
            f"{CHARS_PER_TOKEN_LOW}-{CHARS_PER_TOKEN_HIGH} chars/token)",
            "                           run with --count-tokens for the measured figure",
        ]

    lines += ["", "  matcher input per day", "  " + "-" * 46]
    for n in at:
        p = project(m, n)
        span = _fmt(p.tokens_per_day_low) if exact else \
            f"{_fmt(p.tokens_per_day_low)} - {_fmt(p.tokens_per_day_high)}"
        lines.append(f"    {n:>7,} subscribers   {span:>20} tokens")

    # The routine reads a file and iterates every subscriber in one pass, so a shard is
    # bounded by one context window rather than by bytes. Each shard is one model pass, so
    # this row is also the number of routine invocations a day costs.
    try:
        lines += ["", f"  shards of shortlists.json  (one routine pass each, "
                      f"{int(CONTEXT_USABLE_SHARE * 100)}% of {_fmt(CONTEXT_TOKENS)} ctx)",
                  "  " + "-" * 46]
        for n in at:
            sp = shard_plan(m, n)
            per = (f"{sp.per_shard_min}" if sp.per_shard_min == sp.per_shard_max
                   else f"{sp.per_shard_min}-{sp.per_shard_max}")
            cnt = (f"{sp.shards_min}" if sp.shards_min == sp.shards_max
                   else f"{sp.shards_min}-{sp.shards_max}")
            lines.append(f"    {n:>7,} subscribers   {per:>12} per shard   {cnt:>12} shards")
    except NotMeasurable as exc:
        lines += ["", f"  shards: {exc}"]

    if m.warnings:
        lines += ["", "  warnings", "  " + "-" * 46]
        for w in m.warnings:
            lines.append(f"    - {w}")

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", type=Path, help="a shortlists.json written by export_shortlists")
    ap.add_argument("--at", default=",".join(str(n) for n in DEFAULT_AT),
                    help="comma-separated subscriber counts to project (default: 10,100,1000,10000)")
    ap.add_argument("--count-tokens", action="store_true",
                    help="count tokens exactly via the Anthropic API (needs credentials)")
    ap.add_argument("--model", default="claude-opus-5",
                    help="model to count tokens against; counts are model-specific")
    args = ap.parse_args(argv)

    if not args.path.exists():
        print(f"no such file: {args.path}", file=sys.stderr)
        return 2

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    try:
        m = measure(payload)
    except NotMeasurable as exc:
        print(f"cannot measure: {exc}", file=sys.stderr)
        return 2

    if args.count_tokens:
        m.measured_tokens_per_profile = count_tokens_exactly(payload, args.model)

    at = [int(x) for x in args.at.split(",") if x.strip()]
    print(render(m, at))
    # A sample that cannot represent production is not a failure, but it must not read as a
    # clean result either — the exit code is what a script or a future loop iteration checks.
    return 0 if m.representative else 1


if __name__ == "__main__":
    raise SystemExit(main())
