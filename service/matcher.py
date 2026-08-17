"""AI job matcher — retrieve → rerank, one batched Claude call per subscriber.

This is the core of the product: for each subscriber we pull a cheap keyword/role
shortlist (`store.query_shortlist`), then ask Claude to read the whole shortlist *in
context* and pick the jobs that genuinely fit the person — the same "keywords + overall
context, AI picks" approach that worked for the founder's own search, not a numeric
heuristic. Picks are written to `matches`; the digest reads them from there.

One Claude call handles a subscriber's entire shortlist (not one call per job), so cost
scales with subscribers, not with inventory. Candidates are passed to the model by short
integer index — the 32-char posting_ids never round-trip through the model — and any
index the model invents is dropped. JSON parse/validation errors are logged and skipped,
never crash the run (per CLAUDE.md).

    DATABASE_URL=... ANTHROPIC_API_KEY=... python -m service.matcher [--email x@y] \
        [--limit-profiles N] [--shortlist 120] [--max-picks 15] [--dry-run]

Model: env MATCHER_MODEL (default claude-haiku-4-5) — the project's documented choice for
personal scoring; bump to a larger model by setting the env var.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import education, geo, store  # noqa: E402

logger = logging.getLogger("service.matcher")

MODEL = os.environ.get("MATCHER_MODEL", "claude-haiku-4-5")
SHORTLIST_SIZE = 120       # candidates fed to the model per subscriber
MAX_PICKS = 20             # cap on how many the model returns (email shows a curated few)
DESC_CHARS = 320           # per-candidate description budget (keeps input tokens bounded)
# Store any pick scoring >= this. The email keeps a higher bar (digest.EMAIL_MIN_SCORE);
# the extra 4-5s are surfaced on the "all matches" web page, not emailed.
MATCH_FLOOR = int(os.environ.get("MATCH_FLOOR", "4"))
# Output ceiling for one subscriber's pick list. **A ceiling, not a reservation** — output is
# billed per token actually generated, so raising this costs nothing until the model uses it, and
# nothing about a wider ceiling makes it write more. The realistic response is ~600 tokens
# (~$0.003 on Haiku 4.5 at $5/MTok), and it bills the same under 1500 or 4000.
#
# What the old 1500 bought was a tail risk, flagged on 2026-08-17 and hit nothing: `summary` is
# truncated to 280 chars *after* parsing, so nothing stops the model emitting 20 picks × 280-char
# reasons ≈ 1800 tokens. Past the ceiling the JSON stops mid-object, `json.loads` raises, and
# `match_profile` logs one warning and returns [] — that subscriber loses the day's fresh picks and
# the outcome is indistinguishable from a quiet inventory day. Likelier on Czech text (worse
# tokenisation) and on Haiku (more verbose than a bigger model), which is exactly the live
# configuration. A free change that removes a silent-failure mode is not a trade-off.
MAX_TOKENS = int(os.environ.get("MATCHER_MAX_OUTPUT_TOKENS", "4000"))
# Metered-path abort budget. 0 = no ceiling (the default). When >0, a run stops matching
# further subscribers once accumulated tokens (input + output) cross it, so a bug that
# inflates a shortlist or loops can never run up unbounded API spend before anyone notices.
# It is a *safety cap*, not a per-day cost target — set it well above a healthy run's usage.
MAX_RUN_TOKENS = int(os.environ.get("MATCHER_MAX_TOKENS", "0"))
# Haiku 4.5 list price ($/token), for the end-of-run cost line only — never a gate.
_PRICE_IN, _PRICE_OUT, _PRICE_CACHE_READ = 1.0e-6, 5.0e-6, 0.10e-6


def _run_cost_usd(u: dict) -> float:
    return u["input"] * _PRICE_IN + u["output"] * _PRICE_OUT + u["cache_read"] * _PRICE_CACHE_READ

SYSTEM = """You are the matching engine for JobDigest, a daily job-alert product. You are \
given ONE subscriber's profile and a numbered list of current job postings that a cheap \
keyword filter already pulled for them. Your job: pick the postings that genuinely fit \
this specific person, and rank them best-first.

How to judge fit:
- Weigh the whole context, not just keyword overlap: role type, seniority, skills/stack, \
work setup (remote/hybrid/onsite), location/region, and sector interest.
- Seniority is a HARD filter, not a soft signal. The profile lists target seniority level(s) \
drawn from six: intern (a placement — internship/Praktikum/Werkstudent), entry_level (a first \
permanent job — graduate scheme/absolvent/trainee), junior, mid, senior (senior individual \
contributor — senior/staff/principal/architect) and lead (people leadership — lead/head/ \
director/VP/C-level). Exclude any posting whose level clearly differs from every target — a \
lead or senior role for a junior-only subscriber, an internship for anyone who did not ask \
for one, a graduate role for a senior-only subscriber — even if the role, skills and location \
fit perfectly; give it a score below 4 so it is dropped. Note that senior and lead are \
different kinds of job rather than adjacent rungs: someone targeting senior wants deeper \
individual work, and a "Head of" role is not a promotion they asked for. Candidates whose \
level the posting never stated are marked `seniority=unstated` — that is 70% of postings and \
is NOT a mismatch, so judge them on overall fit like any other.
- Location is a HARD filter for anything that is not fully remote. The profile's "Locations" \
line lists the countries the subscriber can work in and, where they named specific cities, \
those cities. A posting that requires being somewhere else — another city, or a country they \
did not pick — is not a fit however well the role matches: score it below 4. Being emailed an \
on-site job in Brno when you live in Prague is the failure this rule exists to prevent. A \
FULLY remote posting (marked `remote=yes`) is exempt from the *city* test, but not from the \
`reach` rule below: "hybrid" is not remote, it means being in that city most weeks. A candidate \
marked `city=?` is one whose location text we \
could not parse — the prefilter deliberately lets those through, so read its location yourself \
and judge it rather than assuming it fits.
- "Remote" is TWO questions and `reach` is the second one. Whether there is an office is one \
thing; which countries you may LIVE IN while holding the job is another, and in this inventory \
"remote" overwhelmingly means work-from-home inside one single country. So every fully-remote \
candidate carries a reach token: \
`reach=anywhere` — they may live anywhere, so location is satisfied outright. \
`reach=in-your-country` — the role's remote area includes a country the subscriber picked, so \
it is genuinely open to them. \
`reach=other-country-only` — remote, but only from a country they did NOT pick: this is not a \
fit however perfectly the role, stack and seniority match, so score it below 4. \
`reach=?` — we could not establish the breadth, so read the posting's own words and judge it; \
do not assume it is open to them, and equally do not refuse it merely for being silent. \
Emailing someone in Prague a "100% remote" job that turns out to mean work-from-home inside \
Poland is the exact twin of the Brno failure above, and it is the one this rule prevents.
- If the Locations line ends "plus fully remote roles worldwide", that means the subscriber is \
willing to work remotely for an employer in ANY country. It does NOT mean they can relocate, \
and it does not grant them the right to work anywhere — they still live in the countries named \
on that line, so the `reach` rule applies to them unchanged.
- Work schedule: if the profile says "part-time only", a posting that is full-time (or does \
not offer a part-time option) is not what this person asked for. Score it at most 5 — it can \
still appear on their matches page as a weaker option, but it must not headline their email. \
Candidates offering part-time are marked `part_time=yes`; prefer them.
- Postings may be in Czech, Slovak, or English — judge them equally; a "Vývojář" is a \
developer, "Obchodní zástupce" is a sales rep, "Účetní" is an accountant.
- Rank best-first and be honest with the scores: a 9-10 is an excellent fit, a 6-7 solid, \
a 4-5 a plausible-but-weaker option. A later step decides which scores make the email, so \
include everything that is at least a 4 and skip only the clear non-fits.
- Do NOT invent postings. Only reference indices present in the list.

Score each pick 0-10 for fit (10 = ideal). Include every posting scoring 4 or higher.
Return STRICT JSON, no prose, no markdown fences:
{"picks": [{"i": <index>, "score": <int 0-10>, "reason": "<one short sentence on why it fits>"}]}
Order picks by score descending. Return {"picks": []} if nothing fits."""


def _profile_block(p: dict) -> str:
    def _join(key: str) -> str:
        v = p.get(key) or []
        return ", ".join(str(x) for x in v) if v else "—"

    # Where they can actually work. `regions` is only the coarse derived bucket, so it is the
    # fallback for a profile that predates city-level preferences — for everyone else this
    # names the countries and, per country, the cities that on-site work has to be in.
    locations = geo.describe(p.get("countries"), p.get("cities"), p.get("remote_scope"))
    if locations == "—":
        locations = _join("regions")

    lines = [
        f"Label: {p.get('label') or '—'}",
        f"Target roles: {_join('role_categories')}",
        f"Skills / stack: {_join('stack')}",
        f"Seniority: {_join('seniorities')}",
        f"Locations — where the subscriber LIVES (on-site work must be in one of these, and "
        f"remote work must be holdable from one of them): {locations}",
        f"Work types: {_join('work_types')}",
        f"Sectors of interest: {_join('sectors')}",
    ]
    # Only when narrowed. All three modes is the default and the overwhelming majority, and a
    # line saying "any work setup" in every profile is noise the model has to read past.
    modes = geo.clean_work_modes(p.get("work_modes"))
    if len(modes) < len(geo.WORK_MODES):
        lines.append(
            f"Work setup: {geo.describe_work_modes(modes)}. Most postings never state theirs, "
            "so judge an unstated one from its description rather than assuming it qualifies")
    # Same "only when narrowed" rule. This line matters more than most: the SQL gate can only
    # act on the ~3% of postings whose description states a requirement, and on **none** of the
    # Czech/Slovak inventory, which carries no description text at all. For everything else the
    # model is the only thing that can enforce this, and it can only do so where the posting
    # itself says something — so the instruction is to read, not to assume.
    levels = education.clean_levels(p.get("education_levels"))
    field = education.clean_field(p.get("education_field"))
    if len(levels) < len(education.LEVELS):
        lines.append(
            f"Education: {education.describe_levels(levels, field)}. A posting that demands a "
            "higher qualification than these is not a fit (omit it / score it below 4), but "
            "most postings never state one — do not infer a requirement that is not written "
            "down, and do not exclude a posting merely for being silent")
    elif field:
        # No filter, but the field of study is still worth knowing for judging relevance.
        lines.append(f"Studied: {field}")
    # Stated on the signup form but, until now, never shown to the model or used in the SQL
    # prefilter — subscribers who ticked "part-time only" were being emailed full-time roles.
    if p.get("part_time_only"):
        lines.append("Work schedule: PART-TIME ONLY (a full-time role is not what they asked for)")
    if p.get("years_experience") is not None:
        lines.append(f"Years experience: {p['years_experience']}")
    if p.get("cv_summary"):
        lines.append(f"CV summary: {p['cv_summary']}")
    return "\n".join(lines)


def _seniority_for_model(c: dict) -> str:
    """The candidate's level as the model should read it — 'unstated' when the title said none.

    This used to recompute the answer from the title on every export, because the column could
    not express the difference: `seniority()` returned a confident 'mid' for a plain "2D
    Grafik", which against a hard seniority filter reads as a mismatch for a junior-only
    subscriber (or, worse, as a licence to email them mid-level roles). The column now stores
    NULL for exactly that case, so the stored value is the answer and there is no second
    derivation left to drift from the first."""
    return c.get("seniority") or "unstated"


def _city_for_model(c: dict) -> str:
    """The candidate's resolved city, or '?' when the free-text location didn't resolve.

    '?' is load-bearing rather than cosmetic: the SQL prefilter deliberately keeps postings
    with an unresolved city (see `geo.location_predicate`), so the model has to know which
    ones it is being asked to judge from the raw location text instead of trusting the gate.
    """
    slug = c.get("city")
    if not slug:
        return "?"
    country = (c.get("country_code") or "").upper()
    return geo.CITIES.get(country, {}).get(slug) or slug.replace("-", " ")


def _subscriber_countries(profile: dict) -> set[str]:
    """The ISO-2 countries the subscriber lives in, as `_reach_for_model` needs them.

    Empty for a legacy profile carrying only coarse `regions` — and empty is load-bearing, not
    a degenerate case: it is what makes every reach verdict `?` instead of "somewhere else".
    `geo.location_predicate` returns before its reach gate for exactly those profiles, so the
    prompt and the SQL agree about which subscribers this axis can be judged for at all."""
    return {c.upper() for c in geo.clean_countries(profile.get("countries"))}


def _reach_for_model(c: dict, countries: set[str]) -> str:
    """Where the subscriber may live while holding this remote job — one token, or ''.

    **The field the digest was missing.** `remote_signal` says there is no office;
    `remote_reach`/`reach_countries` say which countries you may live in, and until
    2026-08-17 neither reached the model. It read `remote=yes`, applied the location
    exemption exactly as instructed, and emailed the owner five "100% remote" data-engineering
    roles bound to Poland, India and the UK — all scored 7-8, none holdable from Prague.

    Four answers, and `?` is one of them (the `city=?` precedent one function up):

      * ``reach=anywhere``            — live anywhere; exempt from location outright.
      * ``reach=in-your-country``     — its remote area covers a country they picked.
      * ``reach=other-country-only``  — positively bound elsewhere. `geo.reach_predicate` now
        refuses these in SQL, so a shortlist should hold none; it is still rendered because
        the gate is not the only caller (`scripts/matcher_gate_eval.py` builds shortlists by
        hand) and because a rule the model can apply is worth more than one it cannot see.
      * ``reach=?``                   — unprovable, which is 104 of the owner's 1 101 remote
        rows: never classified, a macro-region word we could not enumerate, or a single
        unnamed country. The prompt says read the posting and judge, never assume either way.

    Returns ``''`` for a posting that is not fully remote: its city and country are already the
    hard test, and a reach token there would invite the model to re-litigate an on-site job on
    the wrong axis.
    """
    if not c.get("remote_signal"):
        return ""
    if not countries:
        return "reach=?"
    reach = c.get("remote_reach")
    # Arm-for-arm with `geo.reach_predicate`'s `case`, in the same order — the two are one rule
    # in two languages and a test asserts they agree row by row. In particular an unclassified
    # `remote_reach` wins over a populated `reach_countries`: that array is "countries this
    # posting named", filled whether or not the job is remote, so on a row whose breadth was
    # never classified it is not an eligibility enumeration and must not be read as one.
    if reach is None:
        return "reach=?"
    if reach == "anywhere":
        return "reach=anywhere"
    named = {str(x).upper() for x in (c.get("reach_countries") or []) if x}
    cc = (c.get("country_code") or "").upper()
    if named:
        return "reach=in-your-country" if (named & countries) or (cc and cc in countries) \
            else "reach=other-country-only"
    if reach == "country" and cc:
        return "reach=in-your-country" if cc in countries else "reach=other-country-only"
    return "reach=?"


def _candidates_block(shortlist: list[dict],
                      countries: set[str] | None = None) -> tuple[str, dict[int, str]]:
    """Render candidates for the prompt + return {index -> posting_id}.

    `countries` is the subscriber's own — the reach verdict is relative to where they live, so
    it cannot be computed from the posting alone. Passing none means "unknown subscriber", and
    every remote row then reads `reach=?`, which is the safe direction: the model judges from
    the posting's text instead of being handed a verdict derived from an empty set.
    """
    countries = countries or set()
    index_map: dict[int, str] = {}
    rows = []
    for i, c in enumerate(shortlist):
        index_map[i] = c["posting_id"]
        desc = (c.get("description") or "").replace("\n", " ").strip()[:DESC_CHARS]
        salary = c.get("salary_raw") or ""
        reach = _reach_for_model(c, countries)
        rows.append(
            f"[{i}] {c.get('title') or '?'} @ {c.get('company') or '?'}\n"
            f"    location={c.get('location') or '?'} city={_city_for_model(c)} "
            f"remote={'yes' if c.get('remote_signal') else 'no'} "
            + (f"{reach} " if reach else "")
            + f"setup={c.get('work_mode') or 'unstated'} "
            f"region={c.get('region') or '?'} "
            f"seniority={_seniority_for_model(c)} work={c.get('work_type') or '?'}"
            + (" part_time=yes" if c.get("is_part_time") else "")
            + (f" salary={salary}" if salary else "")
            + (f"\n    {desc}" if desc else "")
        )
    return "\n".join(rows), index_map


def _accumulate_usage(acc: dict | None, resp) -> None:
    """Fold one response's token usage into a run-level accumulator (in place, never raises).

    Purely for the metered path's cost log and abort budget — the SYSTEM block is prompt-cached
    (`cache_control` below), so `cache_read` is where most input tokens go after the first
    subscriber in a run, and the log separates the two so a cache regression is visible."""
    if acc is None:
        return
    u = getattr(resp, "usage", None)
    if u is None:
        return
    acc["input"] += getattr(u, "input_tokens", 0) or 0
    acc["output"] += getattr(u, "output_tokens", 0) or 0
    acc["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    acc["calls"] += 1


def match_profile(client, profile: dict, shortlist: list[dict],
                  usage_acc: dict | None = None, model: str | None = None,
                  max_tokens: int = MAX_TOKENS) -> list[dict]:
    """Return [{posting_id, score, summary}] the model judged a genuine fit.

    `usage_acc`, when given, is updated in place with this call's token usage — the metered
    path passes one so it can log the run's cost and honour an abort budget.

    `model` overrides `MODEL` for this one call. Nothing on the delivery path passes it: it
    exists so `scripts/matcher_model_ab.py` can put the *same* prompt in front of two models
    over one shortlist. Comparing model classes by monkeypatching the module global would work
    until someone imported it by value, and the whole point of that harness is that it exercises
    the real prompt rather than a copy of it. `max_tokens` defaults to `MAX_TOKENS` and is raised
    by the harnesses for a model that thinks before answering, because thinking is billed out of
    the same ceiling as the JSON — a thinking model can spend the whole budget before the answer
    starts, which grades the box rather than the judgement.
    """
    candidates, index_map = _candidates_block(shortlist, _subscriber_countries(profile))
    user = (
        f"SUBSCRIBER PROFILE:\n{_profile_block(profile)}\n\n"
        f"POSTINGS ({len(shortlist)}):\n{candidates}\n\n"
        f"Return the best fits (max {MAX_PICKS}) as JSON."
    )
    resp = client.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    _accumulate_usage(usage_acc, resp)
    # The first *text* block, not `content[0]`. `MATCHER_MODEL` is a documented knob and the
    # obvious thing to point it at is a stronger model — but every model above Haiku 4.5 thinks
    # by default, and on those the response opens with a `thinking` block whose text is empty.
    # Indexing position 0 therefore parsed "" as JSON, logged one warning, and returned zero
    # picks for every subscriber: an empty digest that looks exactly like a quiet inventory day,
    # which is the failure shape this repo keeps rediscovering. Skipping to the text block makes
    # the env var mean what it says.
    # Named before parsing, because the ceiling is the one cause the parse error cannot point at.
    # A response cut off at `max_tokens` is invalid JSON, so it surfaced below as "bad JSON from
    # model" — which sends the next reader to the parser or the prompt, not to the budget. This is
    # the tripwire `max_tokens` exists to trip: raising the ceiling makes truncation rarer, only a
    # log line makes it *legible*. Two knobs are usually the cause and they are a pair —
    # `MATCHER_MODEL` pointed at a thinking model bills its thinking out of this same ceiling, so
    # moving one without `MATCHER_MAX_OUTPUT_TOKENS` is how the budget gets eaten before the JSON
    # starts. It stays `error`, not `warning`: this subscriber just lost the day's fresh picks.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        logger.error("profile %s: model %s hit max_tokens=%d — the reply is truncated and will not "
                     "parse, so this subscriber gets no fresh picks. Raise "
                     "MATCHER_MAX_OUTPUT_TOKENS (a ceiling, not a reservation — unused headroom "
                     "is free), and check whether MATCHER_MODEL is a thinking model billing its "
                     "thinking out of the same budget.", profile.get("id"), model or MODEL,
                     max_tokens)
    raw = next((b.text for b in resp.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", None)), "").strip()
    if not raw:
        logger.warning("profile %s: model %s returned no text block (%s)", profile.get("id"),
                       model or MODEL, [getattr(b, "type", "?") for b in resp.content])
        return []
    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.split("\n") if not l.strip().startswith("```")).strip()

    try:
        picks = json.loads(raw).get("picks", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("profile %s: bad JSON from model (%s): %s", profile.get("id"), exc, raw[:200])
        return []

    out, seen = [], set()
    for pk in picks:
        try:
            i = int(pk["i"])
            score = max(0, min(10, int(pk["score"])))
        except (KeyError, ValueError, TypeError):
            continue
        pid = index_map.get(i)
        if not pid or pid in seen or score < MATCH_FLOOR:
            continue
        seen.add(pid)
        out.append({"posting_id": pid, "score": score,
                    "summary": str(pk.get("reason") or "")[:280]})
    return out[:MAX_PICKS]


# --- no-API path: export shortlists -> claude.ai routine -> import picks --------
# Free tier does the AI reasoning inside a claude.ai cloud routine (subscription
# compute, no metered API). That sandbox has no DB access and no API key, so we hand it
# a JSON shortlist file and read back a JSON picks file. Transport (Drive / git / CI
# artifact) is external — these two functions only touch local files, so any medium works.

ROUTINE_INSTRUCTIONS = (
    "You are JobDigest's daily matcher. For EACH subscriber below, read their profile and "
    "candidate postings and pick the jobs that genuinely fit them (whole context: role, "
    "seniority, skills, work setup, location, sector — not just keywords). "
    "A candidate carries ONLY the fields its posting actually stated, so **a missing field "
    "means we do not know, never that the answer is no**. Judge a missing field from the "
    "description where there is one, and never exclude a posting for being silent. "
    "Treat seniority as "
    "a HARD filter. The six levels are: intern (a placement — internship/Praktikum/"
    "Werkstudent), entry_level (a first permanent job — graduate scheme/absolvent/trainee), "
    "junior, mid, senior (senior individual contributor) and lead (people leadership — "
    "lead/head/director/VP/C-level). Exclude any posting whose level clearly differs from the "
    "subscriber's target level(s) — a lead or senior role for a junior-only subscriber, an "
    "internship for anyone who did not ask for one, a graduate role for a senior-only "
    "subscriber — even if everything else fits (omit it / score it below 4). Senior and lead "
    "are different kinds of job, not adjacent rungs: a \"Head of\" role is not what someone "
    "targeting senior asked for. A candidate with no \"seniority\" never named a level and "
    "is NOT a mismatch — that is 70% of them; judge it on overall fit. "
    "Treat location as a HARD filter for anything that is not fully remote: the profile's "
    "\"locations\" line names the countries and, where given, the exact cities the subscriber "
    "can work in. A posting requiring presence anywhere else — another city, or a country they "
    "did not pick — is not a fit however well the role matches (omit it / score it below 4); "
    "being emailed an on-site job in Brno when you live in Prague is the failure this prevents. "
    "Only candidates carrying \"remote\":true are exempt — \"hybrid\" is not remote, and a "
    "candidate with no \"remote\" field is not exempt. A candidate with no \"city\" did not "
    "resolve to a known city: read its \"location\" text and judge it yourself rather than "
    "assuming the prefilter checked it. "
    "\"remote\" is TWO questions and \"reach\" is the second: whether there is an office is one "
    "thing, which countries you may LIVE IN while holding the job is another, and in this "
    "inventory \"remote\" overwhelmingly means work-from-home inside one single country. "
    "\"reach\":\"anywhere\" means they may live anywhere, so location is satisfied outright. "
    "\"reach\":\"in-your-country\" means the role's remote area covers a country the subscriber "
    "picked, so it is genuinely open to them. \"reach\":\"other-country-only\" means remote but "
    "only from a country they did NOT pick — not a fit however perfectly the role, stack and "
    "seniority match (omit it / score it below 4). A candidate with NO \"reach\" key is one whose "
    "breadth we could not establish: read the posting's own words and judge it, neither assuming "
    "it is open to them nor refusing it merely for being silent. Emailing someone in Prague a "
    "\"100% remote\" job that means work-from-home inside Poland is the twin of the Brno failure "
    "above. And if the profile's \"locations\" line offers \"fully remote roles worldwide\", that "
    "is a willingness to work for an employer in any country — NOT permission to relocate or to "
    "live anywhere; they still live in the countries named, so this rule applies unchanged. "
    "If (and only if) the profile has a \"work_setup\" line, the subscriber has ruled some "
    "arrangements out: a candidate whose \"work_mode\" is not one they accept is not a fit "
    "(omit it / score it below 4). A candidate with no \"work_mode\" never stated an "
    "arrangement — most do not — so read its description and judge it, rather than letting it "
    "through because the field was missing. "
    "If (and only if) the profile has an \"education\" line, the subscriber has ruled out roles "
    "demanding a qualification above the levels listed: a candidate whose \"education_min\" is "
    "higher than any level they accept is not a fit (omit it / score it below 4). "
    "A candidate with no \"education_min\" is the common case (about 91%) and means the "
    "requirement was never READ — not that there is none — so read the description where there "
    "is one and judge it, and **never exclude a posting just for being silent**: a requirement "
    "nobody wrote down is not a requirement. A degree named as \"preferred\", \"nice to have\" "
    "or \"or equivalent experience\" does not disqualify anyone. "
    "\"education_field\" is what the subscriber studied — context for judging how well a role "
    "fits them, never a reason to exclude one. "
    "If the profile has \"part_time_only\":true, a full-time posting is not what they asked "
    "for: score it at most 5 (it still shows on their matches page, it just must not headline "
    "the email) and prefer candidates with \"part_time\":true. Postings may be "
    "Czech/Slovak/English; judge them equally. Score each pick 0-10 on overall fit "
    "(9-10 excellent, 6-7 solid, 4-5 plausible-but-weaker); include every posting scoring 4+ "
    "(skip only clear non-fits); order best-first; max 20 per subscriber; never invent a posting_id. "
    "Write ONE JSON file `picks.json`: "
    '{"picks":[{"profile_id":"<id>","jobs":[{"posting_id":"<id>","score":<int>,"reason":"<one sentence>"}]}]}'
)


def _profile_export(p: dict) -> dict:
    keys = ["label", "role_categories", "stack", "seniorities", "regions",
            "work_types", "sectors", "years_experience", "cv_summary"]
    out = {k: p.get(k) for k in keys if p.get(k) not in (None, [], "")}
    # Location, twice over: the structured fields so the rule is machine-checkable, and one
    # plain sentence because that is what the model actually reasons over. Both are derived
    # from the same source, so they cannot disagree.
    locations = geo.describe(p.get("countries"), p.get("cities"), p.get("remote_scope"))
    if locations != "—":
        out["locations"] = locations
        out["countries"] = geo.clean_countries(p.get("countries"))
        cities = geo.clean_cities(p.get("cities"), p.get("countries"))
        if cities:
            out["cities"] = cities
        out["remote_scope"] = geo.clean_remote_scope(p.get("remote_scope"))
    # Same rule as the prompt block: emitted only when the subscriber has narrowed it, so its
    # presence in the file means "this one has an opinion". See `work_modes` in geo.py.
    modes = geo.clean_work_modes(p.get("work_modes"))
    if len(modes) < len(geo.WORK_MODES):
        out["work_modes"] = modes
        out["work_setup"] = geo.describe_work_modes(modes)
    # Same rule again: present only when the subscriber has an opinion. `education_field` is
    # emitted whenever it is set, filter or no filter — it is context for judging relevance,
    # not a constraint, and it is the one part of this axis that never reaches SQL.
    levels = education.clean_levels(p.get("education_levels"))
    if len(levels) < len(education.LEVELS):
        out["education_levels"] = levels
        out["education"] = education.describe_levels(levels)
    field = education.clean_field(p.get("education_field"))
    if field:
        out["education_field"] = field
    # Only when true: an explicit "part_time_only": false in every profile is noise the model
    # has to read past, and false is already the default reading of its absence.
    if p.get("part_time_only"):
        out["part_time_only"] = True
    return out


def _candidate_export(c: dict, countries: set[str] | None = None) -> dict:
    """One posting as the routine reads it. **A field the posting never stated is absent, not
    null** — one rule, replacing five different spellings of "we don't know".

    This is the payload the whole scaling ladder is sized against, and most of it used to be
    padding. Measured on production 2026-08-09: 691 bytes per candidate, 117 candidates per
    subscriber, ~25k tokens a day each. Five of the fourteen keys carried their default on the
    *majority* of rows — `education_min` is null on 90.6% of active postings, `salary` on ~65%
    (coverage is 30-40%), `work_mode` on most of them, and `remote`/`part_time` are false on
    most — so `"education_min":null` was being written 117 times per subscriber per day to say
    nothing. Omission is what `_profile_export` already does one function above, and it is
    worth ~100-125 bytes a candidate.

    **The saving is not the reason the convention is better, though.** Null previously meant
    "unread" for `education_min`, "never stated" for `work_mode`, and "not claimed" for
    `remote` — three readings of one token, each taught separately in the prompt. Absence means
    exactly one thing everywhere, which is a rule the model can apply to a field nobody
    anticipated. `ROUTINE_INSTRUCTIONS` and `deploy/matcher-routine.md` teach that rule and
    must move with this function: a model told `null` means unread, reading a file that omits
    the key instead, would silently start treating silence as a fact.

    `region` is dropped entirely. It is the coarse derived bucket — `profiles.regions`' own
    docstring calls it "a backstop for old clients, never the filter" — and the candidate
    already carries `location` and `city`, which are what the location rule is judged on.
    """
    # Insertion order is the reading order in the file; keep it stable so a diff of two days'
    # exports shows content changes rather than key shuffling.
    out: dict = {"posting_id": c["posting_id"]}
    for key, value in (("title", c.get("title")),
                       ("company", c.get("company")),
                       ("location", c.get("location"))):
        if value:
            out[key] = value
    # '?' is what `_city_for_model` returns for a location text that resolved to no known city.
    # Absent now carries that meaning, so the sentinel would be a second way to say it.
    city = _city_for_model(c)
    if city != "?":
        out["city"] = city
    # Only ever emitted true. The location rule exempts `remote: true` and nothing else, so a
    # missing key lands in the same branch `false` did.
    if c.get("remote_signal"):
        out["remote"] = True
    # The second of the two questions "remote" asks — which countries the subscriber may LIVE in
    # while holding it. Emitted right after `remote` because it qualifies it: `remote: true` on
    # its own is what let five Poland-bound roles into a Prague inbox on 2026-08-17.
    #
    # Absent when the verdict is `?`, under this function's one rule — a reach we could not
    # establish is unknown, and unknown is said by saying nothing. `ROUTINE_INSTRUCTIONS` and
    # `SYSTEM` both have to teach that, because "no reach key" and "reach=other-country-only"
    # must never collapse into one reading. See `_reach_for_model` for the four verdicts.
    reach = _reach_for_model(c, countries or set())
    if reach and reach != "reach=?":
        out["reach"] = reach.split("=", 1)[1]
    # "remote"|"hybrid"|"onsite". `remote` above is the boolean the location rule keys on;
    # this is the finer answer, and its absence is the common case, not a special one.
    if c.get("work_mode"):
        out["work_mode"] = c["work_mode"]
    # Lowest qualification the ad demands. Absent on 90.6% of rows, and absent means the
    # requirement was never *read* — not that there is none. The prompt has to say so, because
    # this is the one field where the safe reading is not the obvious one.
    if c.get("education_min"):
        out["education_min"] = c["education_min"]
    # One of the six levels, or absent when the title named none — which is 70% of rows, so
    # absence here is the ordinary case and the prompt says so explicitly.
    if c.get("seniority"):
        out["seniority"] = c["seniority"]
    if c.get("work_type"):
        out["work_type"] = c["work_type"]
    if c.get("is_part_time"):
        out["part_time"] = True
    if c.get("salary_raw"):
        out["salary"] = c["salary_raw"]
    desc = (c.get("description") or "").replace("\n", " ").strip()[:DESC_CHARS]
    if desc:
        out["description"] = desc
    return out


def _profiles_for(email: str | None, limit_profiles: int | None) -> list[dict]:
    if email:
        with store.cursor() as cur:
            cur.execute("select * from profiles where lower(email) = %s order by created_at desc limit 1",
                        (email.lower(),))
            row = cur.fetchone()
            profiles = [dict(row)] if row else []
    else:
        profiles = store.sendable_profiles()
    return profiles[:limit_profiles] if limit_profiles else profiles


def _record_shadow(profile: dict, live_shortlist: list[dict]) -> None:
    """Write the vector ranking beside the live one. Never raises, never changes the digest.

    **The try/except is the design, not defensiveness.** This is an experiment sitting inside
    the one job that decides whether real people get email tomorrow, and `jobdigest-match.sh`
    runs under `set -euo pipefail`. An embedding column that is null, a pgvector GUC that moves
    in a future upgrade, or a model swap mid-backfill must cost a measurement — never a
    subscriber's digest. `shortlist_shadow` is read by nothing on the delivery path, so failing
    quietly here loses a row of research data and nothing else.

    It runs *after* `already_sent_ids` has been applied to the live shortlist, so `in_live` is
    computed against what the matcher was really handed rather than what retrieval first
    returned. Comparing against the pre-filter list would score the vector path as disagreeing
    on jobs the subscriber had already been emailed.
    """
    try:
        ranked = store.query_shortlist_vector(profile, limit=SHORTLIST_SIZE)
        if not ranked:
            return
        n = store.record_shortlist_shadow(
            profile["id"], ranked, {c["posting_id"] for c in live_shortlist})
        agree = sum(1 for r in ranked if r["posting_id"] in
                    {c["posting_id"] for c in live_shortlist})
        logger.info("profile %s: shadow shortlist %d rows, %d also in the live shortlist",
                    profile.get("id"), n, agree)
    except Exception:                                    # noqa: BLE001 — see the docstring
        logger.exception("profile %s: shadow shortlist failed (digest unaffected)",
                         profile.get("id"))


def _entry_bytes(entry: dict | None) -> int:
    """What this subscriber costs the matcher, in bytes of `shortlists.json`.

    Serialised the same way the file is written (`ensure_ascii=False`), because that is the
    payload the routine reads — counting characters instead would under-report every Czech,
    Swedish and Norwegian posting, and three of the largest sources are not English.

    `indent=1` in the final `json.dump` adds a little whitespace this does not count. That is
    deliberate: the figure is meant to track the *content* a subscriber costs the model, and
    pretty-printing is an artefact of the transport that could change without the cost
    changing. Bytes-on-disk is not the same question, and `scripts/scaling_budget.py` measures
    the same way so the two agree.
    """
    if not entry:
        return 0
    return len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))


def export_shortlists(path: str, email: str | None = None, limit_profiles: int | None = None,
                      shortlist_size: int = SHORTLIST_SIZE) -> int:
    """Write a JSON shortlist file for the claude.ai routine to match. Returns #profiles."""
    from datetime import datetime, timezone
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "instructions": ROUTINE_INSTRUCTIONS, "profiles": []}
    for p in _profiles_for(email, limit_profiles):
        shortlist, meta = store.query_shortlist_meta(p, limit=shortlist_size)
        already = store.already_sent_ids(p["id"]) if p.get("id") else set()
        shortlist = [c for c in shortlist if c["posting_id"] not in already]
        # Built here, above the write, for two reasons: `shortlist_bytes` must measure the
        # entry that actually ships rather than an estimate of it, and doing it in one
        # `record_digest_run` call keeps this loop at one round trip per profile — which is
        # the thing the scaling work is trying to reduce, not add to.
        #
        # No email address: the routine matches on the profile, and `import_picks` keys on
        # profile_id, so the address was never read by anything downstream — it only widened
        # what a shortlist file discloses if the transport folder were ever exposed. Without
        # it the export is pseudonymous: an opaque id, stated preferences, and public jobs.
        entry = {
            "profile_id": str(p["id"]),
            "profile": _profile_export(p),
            "candidates": [_candidate_export(c, _subscriber_countries(p)) for c in shortlist],
        } if shortlist else None
        # Recorded before the `continue` below: a profile that exports nothing is exactly the
        # case worth alerting on, and skipping the write would make the worst outcome the one
        # that leaves no trace. See store.record_digest_run.
        store.record_digest_run(p.get("id"), shortlist_n=len(shortlist),
                                widened=bool(meta.get("widened")),
                                shortlist_bytes=_entry_bytes(entry))
        # Before the `continue`, for the same reason `record_digest_run` is: a profile the
        # keyword path found nothing for is the single most informative row this table can
        # hold — it is exactly the retrieval failure the vector ranking is supposed to fix,
        # and skipping it would leave the comparison blind to the cases that motivated it.
        _record_shadow(p, shortlist)
        if not shortlist:
            logger.warning("profile %s: no candidates to export (retrieval found %d, "
                           "%d already sent)", p.get("id"), meta["n"], len(already))
            continue
        payload["profiles"].append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    n = len(payload["profiles"])
    logger.info("Exported shortlists for %d profile(s) -> %s", n, path)
    return n


def _mentioned_profile_ids(path: str) -> set[str] | None:
    """Every profile_id the picks file names, valid or not. `None` if it cannot be read.

    Deliberately *not* "every profile whose picks were accepted". An entry that arrived
    malformed, scored below the floor, or named a profile that no longer exists is a
    different failure and already has its own log line in `import_picks`. What this measures
    is whether the matcher considered the subscriber **at all** — which is the only thing a
    truncated or partial file can tell us apart from a subscriber the model rejected.

    Unreadable returns `None` rather than an empty set so the caller can report the whole
    export as uncovered instead of silently reporting no gap.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    entries = data.get("picks", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return None
    return {str(e.get("profile_id")) for e in entries
            if isinstance(e, dict) and e.get("profile_id")}


def coverage_gap(path: str) -> list[str]:
    """Profiles today's export asked about that `path` does not mention at all.

    **Nothing else in the pipeline can see this failure.** `import_picks` iterates the
    entries the file happens to contain, so a file covering 5 of 30 profiles imports
    cleanly and exits 0; the 25 others get no `picks_n`, no `sendable_n`, no email, and no
    error anywhere. `jobdigest-match.sh` checks only that picks.json *exists* and is fresh,
    both of which a truncated file satisfies. The watchdog would eventually notice, but not
    for three days, and `starved_profiles` excludes profiles younger than that window
    entirely — so a new subscriber's first three days of silence are invisible by design.

    That is the exact shape of every incident this repo keeps rediscovering: the outcome is
    indistinguishable from a quiet inventory day. This makes it loud instead.

    Returns the ids sorted, so the alert body is stable run to run.
    """
    exported = store.exported_profile_ids()
    mentioned = _mentioned_profile_ids(path)
    if mentioned is None:
        return sorted(exported)
    return sorted(exported - mentioned)


def report_coverage(path: str) -> int:
    """Log the coverage gap and return how many profiles are missing. Never raises.

    Split from the exit code on purpose: `import_picks` calls this to *log*, because a gap
    must not abort the import — `jobdigest-match.sh` runs under `set -e`, so a non-zero exit
    here would skip the send and cost the covered subscribers their digest over the
    uncovered ones. The `--check-coverage` step carries the exit code instead, and runs
    after the send and the archive so the alert costs nothing.
    """
    try:
        missing = coverage_gap(path)
    except Exception:                                  # pragma: no cover - diagnostics only
        logger.exception("picks coverage: check failed, continuing")
        return 0
    for pid in missing:
        # ASCII on purpose: this line becomes the body of an alert email, assembled from the
        # journal by jobdigest-alert.py, and a mangled non-ASCII character in a mail that only
        # ever arrives when something is already wrong is a needless second thing to debug.
        logger.error("picks coverage: profile %s was exported today but %s does not mention "
                     "it - that subscriber gets no digest", pid, path)
    if missing:
        logger.error("picks coverage: %d exported profile(s) missing from %s. A partial "
                     "picks file is indistinguishable from a quiet day; check the matcher.",
                     len(missing), path)
    return len(missing)


def import_picks(path: str) -> int:
    """Read the routine's picks JSON, validate against the DB, write to `matches`.

    Accepts {"picks":[{profile_id, jobs:[{posting_id, score, reason}]}]} (or a bare list).

    This file crosses a trust boundary: it is written by a claude.ai routine and travels via
    cloud storage, so nothing in it is taken on faith. BOTH sides of every match are checked
    against the DB — `posting_id` must be an active posting (so no invented job or URL can
    reach an inbox) and `profile_id` must be a real subscriber. Validating the profile
    matters twice over: it stops picks being attributed to a subscriber they were never
    generated for, and an unknown id would otherwise raise a foreign-key error that aborts
    the whole import — costing every subscriber that day's digest over one bad record.
    Scores are clamped, sub-floor picks dropped, and a failing entry is logged and skipped.
    Returns #picks."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("picks", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        logger.warning("picks file %s: expected a list of entries, got %s",
                       path, type(entries).__name__)
        return 0

    # Resolve every profile the file claims in one lookup, so an invented, stale or
    # deleted id is dropped before it can reach an insert.
    claimed = [e.get("profile_id") for e in entries if isinstance(e, dict)]
    known = store.existing_profile_ids(claimed)
    for unknown in sorted({str(c) for c in claimed if c} - known):
        logger.warning("picks file: unknown profile_id %s — entry skipped", unknown)

    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("profile_id") or "")
        jobs = entry.get("jobs") or entry.get("picks") or []
        if pid not in known or not isinstance(jobs, list) or not jobs:
            continue
        want = {str(j.get("posting_id")): j for j in jobs
                if isinstance(j, dict) and j.get("posting_id")}
        if not want:
            continue
        try:
            with store.cursor() as cur:  # validate: only active postings that exist
                cur.execute(
                    "select posting_id from postings where is_active and posting_id = any(%s)",
                    (list(want),))
                valid = {r["posting_id"] for r in cur.fetchall()}
            kept = 0
            for posting_id in valid:
                j = want[posting_id]
                try:
                    score = max(0, min(10, int(j.get("score", 0))))
                except (ValueError, TypeError):
                    continue
                if score < MATCH_FLOOR:
                    continue
                store.upsert_match(pid, posting_id, score, str(j.get("reason") or "")[:280])
                kept += 1
            total += kept
            store.record_digest_run(pid, picks_n=kept)
        except Exception:
            logger.exception("picks file: profile %s failed to import, skipping", pid)
            continue
    logger.info("Imported %d picks from %s", total, path)
    report_coverage(path)
    return total


def _match_and_store(client, profile: dict, shortlist_size: int = SHORTLIST_SIZE,
                     usage_acc: dict | None = None) -> int:
    """Match one subscriber against their shortlist and write the picks to `matches`.

    The single per-subscriber unit, shared by the daily batch (`run`) and the on-demand
    endpoint (`match_one`). Records `shortlist_n`/`widened` then `picks_n` on `digest_runs`
    *before* any early return, so a subscriber the retrieval found nothing for still leaves a
    trace (the watchdog's `RETRIEVAL` case). Returns the number of picks stored — 0 if the
    shortlist was empty or the match call failed. A failed call is logged, never raised, so one
    bad subscriber can never abort the rest of a batch."""
    pid = profile.get("id")
    shortlist, meta = store.query_shortlist_meta(profile, limit=shortlist_size)
    already = store.already_sent_ids(pid) if pid else set()
    shortlist = [c for c in shortlist if c["posting_id"] not in already]
    store.record_digest_run(pid, shortlist_n=len(shortlist), widened=bool(meta.get("widened")))
    if not shortlist:
        logger.warning("profile %s (%s): no fresh candidates (retrieval found %d)",
                       pid, profile.get("email"), meta["n"])
        return 0
    try:
        picks = match_profile(client, profile, shortlist, usage_acc=usage_acc)
    except Exception:
        logger.exception("profile %s: match call failed, skipping", pid)
        return 0
    for pk in picks:
        store.upsert_match(pid, pk["posting_id"], pk["score"], pk["summary"])
    store.record_digest_run(pid, picks_n=len(picks))
    logger.info("profile %s (%s): %d candidates -> %d picks",
                pid, profile.get("email"), len(shortlist), len(picks))
    return len(picks)


def match_one(profile: dict, shortlist_size: int = SHORTLIST_SIZE) -> int:
    """Match a single, already-resolved subscriber synchronously — the on-demand path.

    Creates its own metered client, matches just this profile, writes the picks, and returns
    the count. Raises only if `ANTHROPIC_API_KEY` is unset (a misconfigured box, which the
    caller turns into a 5xx); a transient API failure is swallowed by `_match_and_store` and
    returns 0, so the endpoint then simply sends from whatever is already in `matches`."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _match_and_store(client, profile, shortlist_size)


def run(email: str | None = None, limit_profiles: int | None = None,
        shortlist_size: int = SHORTLIST_SIZE, dry_run: bool = False,
        profiles: list[dict] | None = None) -> None:
    """Match subscribers and write picks to `matches` (the metered daily batch).

    `profiles` lets the caller pass an explicit set — the pipeline passes only the subscribers
    it will actually send to today (`_is_due`), so a weekly subscriber is not matched, and
    billed, on the five days no digest goes out. With no list given it falls back to a single
    `email`, or to every `sendable_profile` (the old behaviour)."""
    if profiles is None:
        if email:
            # Match one subscriber by email — any status, so it works before confirm too.
            with store.cursor() as cur:
                cur.execute("select * from profiles where lower(email) = %s order by created_at desc limit 1",
                            (email.lower(),))
                row = cur.fetchone()
                profiles = [dict(row)] if row else []
        else:
            profiles = store.sendable_profiles()
    if limit_profiles:
        profiles = profiles[:limit_profiles]

    logger.info("Matching %d profile(s) with model %s%s",
                len(profiles), MODEL, " [DRY RUN]" if dry_run else "")

    if dry_run:
        for p in profiles:
            shortlist, meta = store.query_shortlist_meta(p, limit=shortlist_size)
            already = store.already_sent_ids(p["id"]) if p.get("id") else set()
            shortlist = [c for c in shortlist if c["posting_id"] not in already]
            cand, _ = _candidates_block(shortlist, _subscriber_countries(p))
            logger.info("profile %s (%s): %d candidates (dry run, no API call)",
                        p.get("id"), p.get("email"), len(shortlist))
            print(f"\n--- SYSTEM ---\n{SYSTEM}\n--- USER (first 1500 chars) ---")
            print((f"SUBSCRIBER PROFILE:\n{_profile_block(p)}\n\nPOSTINGS ({len(shortlist)}):\n{cand}")[:1500])
        return

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    usage = {"input": 0, "output": 0, "cache_read": 0, "calls": 0}

    total_picks = matched = 0
    for p in profiles:
        # Abort budget, checked before each call: a bug that inflated a shortlist or looped
        # stops here rather than after billing every subscriber. 0 disables it (the default).
        if MAX_RUN_TOKENS and (usage["input"] + usage["output"]) >= MAX_RUN_TOKENS:
            logger.error("Matcher ABORT BUDGET hit (%d tokens >= MATCHER_MAX_TOKENS=%d) after "
                         "%d/%d subscribers — stopping to avoid unbounded spend",
                         usage["input"] + usage["output"], MAX_RUN_TOKENS, matched, len(profiles))
            break
        total_picks += _match_and_store(client, p, shortlist_size, usage_acc=usage)
        matched += 1

    logger.info("Matcher done: %d picks across %d/%d profiles; tokens in=%d cache_read=%d "
                "out=%d over %d call(s) ~ $%.4f", total_picks, matched, len(profiles),
                usage["input"], usage["cache_read"], usage["output"], usage["calls"],
                _run_cost_usd(usage))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", metavar="FILE",
                    help="no-API: write shortlists JSON for the claude.ai routine")
    ap.add_argument("--import", dest="import_path", metavar="FILE",
                    help="no-API: read the routine's picks JSON into `matches`")
    ap.add_argument("--check-coverage", dest="coverage_path", metavar="FILE",
                    help="exit non-zero if the picks file misses a profile the export asked "
                         "about (run AFTER the send, so the alert costs no digests)")
    ap.add_argument("--email", help="operate on a single subscriber by email")
    ap.add_argument("--limit-profiles", type=int)
    ap.add_argument("--shortlist", type=int, default=SHORTLIST_SIZE)
    ap.add_argument("--dry-run", action="store_true", help="--match: assemble prompts, no API calls")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)

    if args.export:
        export_shortlists(args.export, email=args.email,
                          limit_profiles=args.limit_profiles, shortlist_size=args.shortlist)
    elif args.coverage_path:
        # Non-zero is the alert: the systemd unit's OnFailure hands the journal to
        # jobdigest-alert.py, the same path the watchdogs use. No second notification channel.
        n = report_coverage(args.coverage_path)
        if n:
            sys.exit(1)
        logger.info("picks coverage: every exported profile is present in %s",
                    args.coverage_path)
    elif args.import_path:
        import_picks(args.import_path)
    else:  # --match: metered API path (premium / on-demand)
        run(email=args.email, limit_profiles=args.limit_profiles,
            shortlist_size=args.shortlist, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
