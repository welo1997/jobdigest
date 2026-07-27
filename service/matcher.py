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

from search_jobs import seniority_stated  # noqa: E402
from service import geo, store  # noqa: E402

logger = logging.getLogger("service.matcher")

MODEL = os.environ.get("MATCHER_MODEL", "claude-haiku-4-5")
SHORTLIST_SIZE = 120       # candidates fed to the model per subscriber
MAX_PICKS = 20             # cap on how many the model returns (email shows a curated few)
DESC_CHARS = 320           # per-candidate description budget (keeps input tokens bounded)
# Store any pick scoring >= this. The email keeps a higher bar (digest.EMAIL_MIN_SCORE);
# the extra 4-5s are surfaced on the "all matches" web page, not emailed.
MATCH_FLOOR = int(os.environ.get("MATCH_FLOOR", "4"))

SYSTEM = """You are the matching engine for JobDigest, a daily job-alert product. You are \
given ONE subscriber's profile and a numbered list of current job postings that a cheap \
keyword filter already pulled for them. Your job: pick the postings that genuinely fit \
this specific person, and rank them best-first.

How to judge fit:
- Weigh the whole context, not just keyword overlap: role type, seniority, skills/stack, \
work setup (remote/hybrid/onsite), location/region, and sector interest.
- Seniority is a HARD filter, not a soft signal. The profile lists target seniority level(s) \
(junior/mid/senior). Exclude any posting whose level clearly differs from every target — a \
senior/lead/principal role for a junior-only subscriber, or a junior/graduate/intern role for \
a senior-only subscriber — even if the role, skills and location fit perfectly; give it a \
score below 4 so it is dropped. Candidates whose level the posting never stated are marked \
`seniority=unstated`: those are NOT mismatches, so judge them on overall fit like any other.
- Location is a HARD filter for anything that is not fully remote. The profile's "Locations" \
line lists the countries the subscriber can work in and, where they named specific cities, \
those cities. A posting that requires being somewhere else — another city, or a country they \
did not pick — is not a fit however well the role matches: score it below 4. Being emailed an \
on-site job in Brno when you live in Prague is the failure this rule exists to prevent. Only a \
FULLY remote posting is exempt (those are marked `remote=yes`): "hybrid" is not remote, it \
means being in that city most weeks. A candidate marked `city=?` is one whose location text we \
could not parse — the prefilter deliberately lets those through, so read its location yourself \
and judge it rather than assuming it fits.
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
        f"Locations (on-site work must be in one of these): {locations}",
        f"Work types: {_join('work_types')}",
        f"Sectors of interest: {_join('sectors')}",
    ]
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
    """The candidate's level as the model should read it — 'unstated' when the title never
    said one. `search_jobs.seniority()` defaults to 'mid', so a plain "2D Grafik" is stored
    as mid and used to reach the model as a confident claim; against a hard seniority filter
    that quietly reads as a mismatch for a junior-only subscriber (or, worse, as a licence to
    email them mid-level roles). Recomputed from the title rather than stored, because the
    column cannot express the difference."""
    if not seniority_stated(c.get("title")):
        return "unstated"
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


def _candidates_block(shortlist: list[dict]) -> tuple[str, dict[int, str]]:
    """Render candidates for the prompt + return {index -> posting_id}."""
    index_map: dict[int, str] = {}
    rows = []
    for i, c in enumerate(shortlist):
        index_map[i] = c["posting_id"]
        desc = (c.get("description") or "").replace("\n", " ").strip()[:DESC_CHARS]
        salary = c.get("salary_raw") or ""
        rows.append(
            f"[{i}] {c.get('title') or '?'} @ {c.get('company') or '?'}\n"
            f"    location={c.get('location') or '?'} city={_city_for_model(c)} "
            f"remote={'yes' if c.get('remote_signal') else 'no'} "
            f"region={c.get('region') or '?'} "
            f"seniority={_seniority_for_model(c)} work={c.get('work_type') or '?'}"
            + (" part_time=yes" if c.get("is_part_time") else "")
            + (f" salary={salary}" if salary else "")
            + (f"\n    {desc}" if desc else "")
        )
    return "\n".join(rows), index_map


def match_profile(client, profile: dict, shortlist: list[dict]) -> list[dict]:
    """Return [{posting_id, score, summary}] the model judged a genuine fit."""
    candidates, index_map = _candidates_block(shortlist)
    user = (
        f"SUBSCRIBER PROFILE:\n{_profile_block(profile)}\n\n"
        f"POSTINGS ({len(shortlist)}):\n{candidates}\n\n"
        f"Return the best fits (max {MAX_PICKS}) as JSON."
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    raw = resp.content[0].text.strip()
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
    "seniority, skills, work setup, location, sector — not just keywords). Treat seniority as "
    "a HARD filter: exclude any posting whose level clearly differs from the subscriber's "
    "target seniority level(s) — a senior/lead role for a junior-only subscriber, or a "
    "junior/graduate/intern role for a senior-only subscriber — even if everything else fits "
    "(omit it / score it below 4). A candidate with \"seniority\":\"unstated\" never named a "
    "level and is NOT a mismatch — judge it on overall fit. "
    "Treat location as a HARD filter for anything that is not fully remote: the profile's "
    "\"locations\" line names the countries and, where given, the exact cities the subscriber "
    "can work in. A posting requiring presence anywhere else — another city, or a country they "
    "did not pick — is not a fit however well the role matches (omit it / score it below 4); "
    "being emailed an on-site job in Brno when you live in Prague is the failure this prevents. "
    "Only \"remote\":true candidates are exempt — \"hybrid\" is not remote. A candidate with "
    "\"city\":\"?\" did not resolve to a known city: read its \"location\" text and judge it "
    "yourself rather than assuming the prefilter checked it. "
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
    # Only when true: an explicit "part_time_only": false in every profile is noise the model
    # has to read past, and false is already the default reading of its absence.
    if p.get("part_time_only"):
        out["part_time_only"] = True
    return out


def _candidate_export(c: dict) -> dict:
    desc = (c.get("description") or "").replace("\n", " ").strip()[:DESC_CHARS]
    return {
        "posting_id": c["posting_id"], "title": c.get("title"), "company": c.get("company"),
        "location": c.get("location"), "region": c.get("region"),
        "city": _city_for_model(c), "remote": bool(c.get("remote_signal")),
        "seniority": _seniority_for_model(c), "work_type": c.get("work_type"),
        "part_time": bool(c.get("is_part_time")),
        "salary": c.get("salary_raw"), "description": desc,
    }


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
        # Recorded before the `continue` below: a profile that exports nothing is exactly the
        # case worth alerting on, and skipping the write would make the worst outcome the one
        # that leaves no trace. See store.record_digest_run.
        store.record_digest_run(p.get("id"), shortlist_n=len(shortlist),
                                widened=bool(meta.get("widened")))
        if not shortlist:
            logger.warning("profile %s: no candidates to export (retrieval found %d, "
                           "%d already sent)", p.get("id"), meta["n"], len(already))
            continue
        # No email address: the routine matches on the profile, and `import_picks` keys on
        # profile_id, so the address was never read by anything downstream — it only widened
        # what a shortlist file discloses if the transport folder were ever exposed. Without
        # it the export is pseudonymous: an opaque id, stated preferences, and public jobs.
        payload["profiles"].append({
            "profile_id": str(p["id"]),
            "profile": _profile_export(p),
            "candidates": [_candidate_export(c) for c in shortlist],
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    n = len(payload["profiles"])
    logger.info("Exported shortlists for %d profile(s) -> %s", n, path)
    return n


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
    return total


def run(email: str | None = None, limit_profiles: int | None = None,
        shortlist_size: int = SHORTLIST_SIZE, dry_run: bool = False) -> None:
    if email:
        # Match a single subscriber by email — any status, so it works before confirm too.
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

    client = None
    if not dry_run:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    total_picks = 0
    for p in profiles:
        shortlist, meta = store.query_shortlist_meta(p, limit=shortlist_size)
        already = store.already_sent_ids(p["id"]) if p.get("id") else set()
        shortlist = [c for c in shortlist if c["posting_id"] not in already]
        if not dry_run:
            store.record_digest_run(p.get("id"), shortlist_n=len(shortlist),
                                    widened=bool(meta.get("widened")))
        if not shortlist:
            logger.warning("profile %s (%s): no fresh candidates (retrieval found %d)",
                           p.get("id"), p.get("email"), meta["n"])
            continue

        if dry_run:
            cand, _ = _candidates_block(shortlist)
            logger.info("profile %s (%s): %d candidates (dry run, no API call)",
                        p.get("id"), p.get("email"), len(shortlist))
            print(f"\n--- SYSTEM ---\n{SYSTEM}\n--- USER (first 1500 chars) ---")
            print((f"SUBSCRIBER PROFILE:\n{_profile_block(p)}\n\nPOSTINGS ({len(shortlist)}):\n{cand}")[:1500])
            continue

        try:
            picks = match_profile(client, p, shortlist)
        except Exception:
            logger.exception("profile %s: match call failed, skipping", p.get("id"))
            continue

        for pk in picks:
            store.upsert_match(p["id"], pk["posting_id"], pk["score"], pk["summary"])
        total_picks += len(picks)
        store.record_digest_run(p.get("id"), picks_n=len(picks))
        logger.info("profile %s (%s): %d candidates -> %d picks",
                    p.get("id"), p.get("email"), len(shortlist), len(picks))

    logger.info("Matcher done: %d picks across %d profiles", total_picks, len(profiles))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", metavar="FILE",
                    help="no-API: write shortlists JSON for the claude.ai routine")
    ap.add_argument("--import", dest="import_path", metavar="FILE",
                    help="no-API: read the routine's picks JSON into `matches`")
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
    elif args.import_path:
        import_picks(args.import_path)
    else:  # --match: metered API path (premium / on-demand)
        run(email=args.email, limit_profiles=args.limit_profiles,
            shortlist_size=args.shortlist, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
