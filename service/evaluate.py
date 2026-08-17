"""Run the synthetic personas (53 since migration 012) through the real matching path and
grade the result.

    python -m service.evaluate --verify-teardown     # prove the exit before taking the risk
    python -m service.evaluate --seed
    python -m service.evaluate --export /exchange/eval-shortlists.json
    python -m service.evaluate --audit               # deterministic findings, no AI involved
    python -m service.evaluate --import /exchange/eval-picks.json
    python -m service.evaluate --send                # MAIL_BACKEND=file
    python -m service.evaluate --grade
    python -m service.evaluate --teardown

Why this exists: every matching defect so far was found by reading one real subscriber's data
end to end, and every one was invisible from the outside — no exception, no failed timer,
every check green, a real person receiving nothing. This asks the same question fifty-odd times
about people who do not exist.

**Scoping is the safety property.** This module runs against the production database on
purpose — testing a local checkout would test `master` rather than the code actually
deployed, and drift between the two is a documented recurring failure here. What makes that
safe is that every statement is scoped to `personas.DOMAIN`:

  * writes only ever create or delete addresses ending `@jobdigest-eval.invalid`
  * the export loop reads persona rows directly instead of `store.sendable_profiles()`, so it
    cannot overwrite a real subscriber's `digest_runs` row or put them in a shortlist file
  * the send loop mirrors `pipeline.run`'s body over persona rows only, so no real subscriber
    is mailed off-schedule
  * **every persona is created already paused**, so the production 05:00 export cannot see it

That last one was learned the hard way (2026-07-27). The three above only scope what *this
module* does; they say nothing about what the box does on its own schedule. The cohort was
seeded `status='active'`, the session ended before teardown, and the next morning's real
export ran `sendable_profiles()` and shipped 53 profiles to Drive instead of 3 — the routine's
whole daily budget aimed at 50 people who do not exist. Nothing was mailed (`digest_sends`
stayed empty, so no `.invalid` address ever reached SMTP) and no real subscriber's row was
overwritten, but the safety argument had quietly depended on the session finishing in one
sitting, which is not a property anyone can promise.

`paused_until` is the right lever because `sendable_profiles()` filters on it and
`pipeline._is_due` does not — so production cannot see a persona, while this harness still
drives the full send path over `persona_rows()`. A persona is therefore quarantined for its
whole life; there is deliberately no `--resume`.

Nothing here reimplements matching. Retrieval is `store.query_shortlist_meta`, the export
projection is `matcher._profile_export`/`_candidate_export`, the trust boundary is
`matcher.import_picks`, the diagnosis is `watchdog.diagnose`. A harness that reimplemented any
of those would grade a system that does not ship.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from service import digest as digestmod
from service import geo, mailer, matcher, pipeline, store, watchdog
from service.tests import personas as P

logger = logging.getLogger("service.evaluate")

LIKE = f"%@{P.DOMAIN}"
CHILD_TABLES = ("matches", "digest_runs", "digest_sends", "events")

# Far enough out that no persona is ever due again. `sendable_profiles()` reads this column;
# `pipeline._is_due` does not, so quarantine costs the harness nothing.
PAUSED_UNTIL = "2099-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- scoping ---

def persona_rows() -> list[dict]:
    """Persona profiles, straight from the DB — deliberately NOT `sendable_profiles()`.

    Using the real selector would sweep in live subscribers, and everything downstream
    (`record_digest_run`, the shortlist file, the send loop) would then act on them.
    """
    with store.cursor() as cur:
        cur.execute("select * from profiles where email like %s order by email", (LIKE,))
        return [dict(r) for r in cur.fetchall()]


def real_profile_count() -> int:
    """Live subscribers, i.e. everything this harness must leave untouched."""
    with store.cursor() as cur:
        cur.execute("select count(*) as n from profiles where email not like %s", (LIKE,))
        return int(cur.fetchone()["n"])


def _child_counts(profile_ids: list[str]) -> dict[str, int]:
    if not profile_ids:
        return {t: 0 for t in CHILD_TABLES}
    out = {}
    with store.cursor() as cur:
        for table in CHILD_TABLES:
            cur.execute(f"select count(*) as n from {table} where profile_id = any(%s::uuid[])",
                        (profile_ids,))
            out[table] = int(cur.fetchone()["n"])
    return out


# ------------------------------------------------------------------------ seed / drop ---

def validate_payloads() -> list[str]:
    """Push every payload through the real request model before trusting it.

    Both the `.filter(Boolean)` drop and the slugify-into-`role_categories` bug lived in the
    request layer, so seeding straight into the store would step over exactly the code that
    broke. The address is substituted because `EmailStr` is not what is under test here and
    `.invalid` is reserved.
    """
    from service.webapp import SubscribeIn

    problems = []
    for persona in P.PERSONAS:
        try:
            SubscribeIn(**{**persona["payload"], "email": "persona@example.com"})
        except Exception as exc:                                  # noqa: BLE001 - reported
            problems.append(f"{persona['key']}: {type(exc).__name__}: {exc}")
    return problems


def quarantine() -> int:
    """Make every persona invisible to the production export. Idempotent.

    Called by `seed()` immediately after creation and safe to run at any time. This is the
    guard that does not depend on the session reaching `--teardown`: if the process dies here,
    the personas are already unreachable by `sendable_profiles()`, so the worst case is 50
    orphan rows rather than 50 fake subscribers in tomorrow's shortlist file.
    """
    with store.cursor(commit=True) as cur:
        cur.execute("update profiles set paused_until = %s where email like %s",
                    (PAUSED_UNTIL, LIKE))
        return cur.rowcount


def exportable_profile_count() -> int:
    """What the real 05:00 export would ship right now — the number that should stay at 3.

    Deliberately the live selector rather than a persona-scoped query: the thing worth
    asserting is not "personas are paused" but "production sees none of them".
    """
    return len(store.sendable_profiles())


def seed(keys: set[str] | None = None) -> dict:
    made, refused = [], []
    for persona in P.PERSONAS:
        if keys and persona["key"] not in keys:
            continue
        row = store.create_email_subscription(
            persona["email"], dict(persona["payload"]), confirmed=True)
        (made if row else refused).append(persona["key"])
    # Before returning, not as a separate step a caller might forget or never reach.
    paused = quarantine()
    return {"created": made, "refused": refused, "quarantined": paused,
            "production_would_export": exportable_profile_count()}


def negative_cases() -> list[dict]:
    """The signups that must be rejected. "Was refused" is the assertion here."""
    from service.webapp import SubscribeIn

    results = []
    for case in P.NEGATIVE_CASES:
        if case["expect"] == "422":
            try:
                SubscribeIn(**{**case["payload"], "email": "persona@example.com"})
                verdict = "ACCEPTED — should have been rejected"
                ok = False
            except Exception as exc:                              # noqa: BLE001 - reported
                verdict = f"rejected ({type(exc).__name__})"
                ok = True
        else:                                                     # expect: none
            row = store.create_email_subscription(
                case["email"], dict(case["payload"]), confirmed=True)
            ok = row is None
            verdict = "refused (None)" if ok else "CREATED A SECOND LIVE PROFILE"
        results.append({"key": case["key"], "ok": ok, "verdict": verdict,
                        "note": case["note"]})
    quarantine()      # n01 is *expected* to create nothing, but do not bet the export on it
    return results


def teardown() -> dict:
    """Delete the cohort and prove the cascade actually fired.

    Run this BEFORE seeding the full set, against two throwaway personas — an exit path that
    has never been executed is not an exit path. It is also the 30-day erasure promise in
    miniature: one delete, and every table keyed on the profile empties itself.
    """
    ids = [str(r["id"]) for r in persona_rows()]
    before = _child_counts(ids)
    with store.cursor(commit=True) as cur:
        cur.execute("delete from profiles where email like %s", (LIKE,))
        deleted = cur.rowcount
    after = _child_counts(ids)
    return {"profiles_deleted": deleted, "child_rows_before": before,
            "child_rows_after": after, "real_profiles_untouched": real_profile_count()}


# ------------------------------------------------------------------ export + audit ---

# Deliberately NOT `geo._QUALIFIED_HOME_OFFICE`, and deliberately cruder: this is the second
# opinion. The 2026-07-28 miss was not a wrong rule but a trusted input — `_allowed_location`
# recomputed the location logic from scratch and then exempted anything flagged remote, which
# is the one field the bug was in. A check that imports the fix cannot detect the next
# scraper that lies in a way the fix does not cover, so this reads the posting's own words.
_HYBRID_WORDS = ("obcasn", "občasn", "prilezitostn", "příležitostn", "príležitostn",
                 "prevazn", "převážn", "prevažn", "castecn", "částečn", "čiastočn",
                 "hybrid", "hybridn")


def _remote_claim_contradicted(cand: dict) -> bool:
    """Does this posting call itself remote while its text describes a hybrid arrangement?"""
    if not cand.get("remote_signal"):
        return False
    text = f"{cand.get('location') or ''} {(cand.get('description') or '')[:2000]}".lower()
    return any(w in text for w in _HYBRID_WORDS)


def _allowed_location(cand: dict, profile: dict) -> bool:
    """Mirror of `geo.location_predicate`'s intent, applied to a retrieved row.

    Recomputed rather than re-run so a bug in the SQL cannot also hide itself in the check.
    Four rules, all of which have been broken: fully-remote is exempt from the *city* test but
    not from `reach` (2026-08-17 — five Poland-bound "100% remote" roles in one email), an
    unknown country, city or reach is KEPT for the model to judge, and hybrid is not remote.
    """
    countries = [c.upper() for c in (profile.get("countries") or [])]
    if cand.get("remote_signal"):
        # Mirror of `geo.reach_predicate`: refuse only a reach the posting positively states,
        # and only when it excludes every country the subscriber lives in. A null reach, a
        # region we could not enumerate, or an unnamed single country all stay admissible —
        # so this flags the leak the SQL is supposed to prevent, not the unknowns it keeps.
        if not countries:
            return True
        reach = cand.get("remote_reach")
        named = {str(x).upper() for x in (cand.get("reach_countries") or []) if x}
        cc = (cand.get("country_code") or "").upper()
        if reach == "anywhere" or reach is None:
            return True
        if named:
            return bool(named.intersection(countries)) or (cc in countries)
        if reach == "country" and cc:
            return cc in countries
        return True
    if not countries:
        return True
    cc = cand.get("country_code")
    if not cc:
        return True                                   # unknown country — kept by design
    if cc.upper() not in countries:
        return False
    picked = {c for c in (profile.get("cities") or []) if c.startswith(f"{cc.lower()}:")}
    if not picked:
        return True                                   # no city named for this country = any
    city = cand.get("city")
    if not city:
        return True                                   # unresolved city — kept by design
    return f"{cc.lower()}:{city}" in picked


def _allowed_work_mode(cand: dict, profile: dict) -> bool:
    """Second opinion on the work-setup gate, recomputed rather than re-run.

    The rule it is checking is deliberately permissive — a null `work_mode` passes, because
    most postings never state one — so the only thing worth catching here is a row whose mode
    *is* known and *is* one the subscriber ruled out. That is the failure a wrong `any(%s)`
    parameter or a misplaced `is null` would produce, and it is invisible downstream: the
    posting simply appears, looking like every other candidate.
    """
    modes = geo.clean_work_modes(profile.get("work_modes"))
    if len(modes) == len(geo.WORK_MODES):
        return True
    mode = cand.get("work_mode")
    return mode is None or mode in modes


def _audit_one(profile: dict, rows: list[dict], meta: dict) -> dict:
    selected = list(profile.get("role_categories") or [])
    cats = Counter(r.get("role_category") or "uncategorised" for r in rows)
    violations = [r["posting_id"] for r in rows if not _allowed_location(r, profile)]
    # Counted separately from `violations` because these pass the location rule — they are
    # exempt from it — and that is exactly what makes them dangerous. A posting here reached
    # the subscriber on a remote claim its own text contradicts.
    fake_remote = [r["posting_id"] for r in rows if _remote_claim_contradicted(r)]
    wrong_setup = [r["posting_id"] for r in rows if not _allowed_work_mode(r, profile)]

    # What the part-time sort actually promises, precisely. NOT "every part-time row precedes
    # every full-time one" — the round-robin interleaves buckets, and `is_part_time desc`
    # orders *within* a bucket, so a strict global sort is not the contract and asserting it
    # produces a false positive on any multi-bucket profile. The real guarantee is the one
    # from the bug report: part-time rows must reach the freshest-120 window at all, rather
    # than being crowded out by full-time before a single one appears.
    part_time = None
    if profile.get("part_time_only") and rows:
        flags = [bool(r.get("is_part_time")) for r in rows]
        part_time = {"total": sum(flags), "in_top_20": sum(flags[:20]),
                     "share": round(sum(flags) / len(flags), 3)}

    from search_jobs import seniority_stated
    stated = sum(1 for r in rows if seniority_stated(r.get("title") or ""))

    return {
        "n": meta.get("n", len(rows)),
        "n_narrow": meta.get("n_narrow"),
        "widened": bool(meta.get("widened")),
        "categories": dict(cats.most_common()),
        "selected_with_zero_rows": [c for c in selected if cats.get(c, 0) == 0],
        "top_category_share": round(max(cats.values()) / len(rows), 3) if rows else None,
        "location_violations": violations,
        "remote_claim_contradicted": fake_remote,
        "work_mode_violations": wrong_setup,
        "unstated_share": round(1 - stated / len(rows), 3) if rows else None,
        "part_time": part_time,
        "buckets": max(1, len(selected) + 1),   # selected categories, plus `__other__`
    }


def export(path: str, audit_path: str | None = None) -> dict:
    """Write the routine's input for persona profiles only, and audit the same retrieval."""
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": matcher.ROUTINE_INSTRUCTIONS,
        "profiles": [],
    }
    audit: dict[str, Any] = {}
    by_email = {p["email"]: p for p in P.PERSONAS}

    for row in persona_rows():
        shortlist, meta = store.query_shortlist_meta(row, limit=matcher.SHORTLIST_SIZE)
        already = store.already_sent_ids(row["id"])
        shortlist = [c for c in shortlist if c["posting_id"] not in already]
        # Recorded before the `continue`, same as the real export: the profile that exports
        # nothing is precisely the one worth alerting on.
        store.record_digest_run(row["id"], shortlist_n=len(shortlist),
                                widened=bool(meta.get("widened")))
        persona = by_email.get(row["email"], {})
        audit[persona.get("key", row["email"])] = {
            "profile_id": str(row["id"]),
            "note": persona.get("note"),
            "expect": persona.get("expect"),
            **_audit_one(row, shortlist, meta),
        }
        if not shortlist:
            continue
        payload["profiles"].append({
            "profile_id": str(row["id"]),
            "profile": matcher._profile_export(row),
            "candidates": [matcher._candidate_export(c, matcher._subscriber_countries(row))
                           for c in shortlist],
        })

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    if audit_path:
        with open(audit_path, "w", encoding="utf-8") as fh:
            json.dump(audit, fh, ensure_ascii=False, indent=1)

    return {"profiles_exported": len(payload["profiles"]), "audited": len(audit),
            **inspect_export(path)}


_EMAIL_RE = __import__("re").compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def inspect_export(path: str) -> dict:
    """What addresses actually ended up in the file that travels to Google Drive.

    `test_matcher_exchange.py` asserts `"@" not in raw` — correct, but it runs against a
    synthetic fixture. Real job descriptions carry recruiter contact addresses, so the
    blanket assertion cannot hold on real inventory and the interesting question is a
    narrower one: **no subscriber address may appear.** That is the guarantee the privacy
    policy actually rests on. Third-party addresses harvested from posting text are a
    separate matter, reported rather than asserted, because the file is owner-only but does
    leave the box.
    """
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    found = _EMAIL_RE.findall(raw)
    domains: Counter = Counter(a.split("@")[1].lower() for a in found)
    subscriber_hits = [a for a in found if a.lower().endswith(P.DOMAIN)]
    with store.cursor() as cur:
        cur.execute("select email from profiles where email is not null")
        live = {r["email"].lower() for r in cur.fetchall()}
    subscriber_hits += [a for a in found if a.lower() in live]
    assert not subscriber_hits, f"export leaked subscriber addresses: {len(subscriber_hits)}"
    # Every bare "@" that is NOT an address. `test_matcher_exchange.py` asserts none at all;
    # on real inventory that assertion holds for the wrong reason, so record what is there.
    contexts = [raw[max(0, i - 30):i + 30].replace("\n", " ")
                for i, ch in enumerate(raw) if ch == "@"][:6]
    return {"third_party_addresses_in_descriptions": len(found),
            "distinct_domains": len(domains),
            "top_domains": domains.most_common(5),
            "bare_at_signs": raw.count("@"),
            "at_contexts": contexts}


def merge_picks(paths: list[str], out: str) -> dict:
    """Concatenate the matcher's per-batch outputs into one `picks.json`.

    The routine is run in batches because 50 subscribers x 120 candidates does not fit one
    context. Merging is deliberately dumb — no dedupe, no reordering, no score adjustment —
    because anything cleverer would be matching logic living outside the matcher, and
    `import_picks` is the thing that decides what is admissible anyway.
    """
    picks, seen = [], set()
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("picks", data if isinstance(data, list) else []):
            pid = str(entry.get("profile_id") or "")
            if pid in seen:
                logger.warning("profile %s appears in more than one batch — keeping first", pid)
                continue
            seen.add(pid)
            picks.append(entry)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"picks": picks}, fh, ensure_ascii=False, indent=1)
    return {"batches": len(paths), "profiles": len(picks),
            "jobs": sum(len(e.get("jobs") or []) for e in picks),
            "profiles_with_no_picks": sum(1 for e in picks if not e.get("jobs"))}


def audit_report(audit_path: str) -> list[str]:
    """Turn the audit sidecar into findings — what a correct system should have done."""
    with open(audit_path, encoding="utf-8") as fh:
        audit = json.load(fh)
    findings = []
    for key, a in audit.items():
        exp = a.get("expect") or {}
        if a["location_violations"]:
            findings.append(
                f"{key}: LOCATION — {len(a['location_violations'])} on-site candidates "
                f"outside the chosen cities/countries")
        if a.get("remote_claim_contradicted"):
            findings.append(
                f"{key}: FALSE REMOTE — {len(a['remote_claim_contradicted'])} candidates are "
                f"flagged fully remote but describe a hybrid arrangement, so they bypassed "
                f"the location gate entirely")
        if a.get("work_mode_violations"):
            findings.append(
                f"{key}: WORK SETUP — {len(a['work_mode_violations'])} candidates carry a "
                f"work_mode this subscriber ruled out (unknown modes are kept on purpose, so "
                f"every row here is one we classified and then ignored)")
        if a["selected_with_zero_rows"]:
            findings.append(
                f"{key}: EMPTY CATEGORY — selected {a['selected_with_zero_rows']} but got "
                f"0 rows from it (a filter matching nothing raises no error anywhere)")
        if a["widened"] and not exp.get("widened_ok"):
            findings.append(
                f"{key}: UNEXPECTED WIDENING — narrow retrieval returned "
                f"{a['n_narrow']}, under the floor")
        if not a["widened"] and a["n"] < exp.get("min_shortlist", 20):
            findings.append(
                f"{key}: THIN — {a['n']} candidates, floor did not fire")
        pt = a.get("part_time")
        if pt and pt["total"] and not pt["in_top_20"]:
            findings.append(
                f"{key}: PART-TIME — {pt['total']} part-time rows retrieved but none in the "
                f"first 20, so the digest headlines full-time work they did not ask for")
        if pt is not None and not pt["total"]:
            findings.append(f"{key}: PART-TIME — no part-time rows reached the shortlist")
        # Imbalance only means something where a partition was supposed to spread the slots.
        # With no categories selected there is a single `__other__` bucket, so the natural
        # volume skew is structural rather than a partition failure — reported separately.
        share = a.get("top_category_share")
        if share and share > 0.6 and a.get("buckets", 1) > 2:
            findings.append(
                f"{key}: IMBALANCE — one category holds {share:.0%} despite "
                f"{a['buckets']} buckets")
    return findings


# ------------------------------------------------------------------- send + grading ---

def send(dry_run: bool = False) -> dict:
    """Mirror of `pipeline.run`'s send loop, scoped to personas.

    Same calls in the same order — `_is_due`, `build_digest`, `record_digest_run` before the
    skip, render, send, `record_sends`, `mark_digest_sent`. Only the profile selector differs,
    because `sendable_profiles()` would sweep in live subscribers and mail them off-schedule.
    """
    now = datetime.now(timezone.utc)
    sent = nojobs = notdue = 0
    detail = []
    for p in persona_rows():
        if not pipeline._is_due(p, now):
            notdue += 1
            continue
        jobs = digestmod.build_digest(p, limit=digestmod.DEFAULT_LIMIT)
        store.record_digest_run(p["id"], sendable_n=len(jobs))
        if not jobs:
            nojobs += 1
            detail.append({"email": p["email"], "jobs": 0})
            continue
        total = store.match_count(p["id"])
        subject = digestmod.subject_line(p, jobs)
        html = digestmod.render_html(p, jobs, base_url=pipeline.BASE_URL, total_matches=total)
        text = digestmod.render_text(p, jobs, base_url=pipeline.BASE_URL, total_matches=total)
        if dry_run:
            sent += 1
            detail.append({"email": p["email"], "jobs": len(jobs), "subject": subject})
            continue
        mailer.send(p["email"], subject, html, text,
                    list_unsubscribe=digestmod._manage_url(p, "unsub"))
        store.record_sends(p["id"], [(j["posting_id"], j["score"]) for j in jobs])
        store.mark_digest_sent(p["id"])
        store.record_digest_run(p["id"], sent=True)
        sent += 1
        detail.append({"email": p["email"], "jobs": len(jobs), "subject": subject,
                       "scores": [j["score"] for j in jobs]})
    return {"sent": sent, "skipped_no_matches": nojobs, "skipped_not_due": notdue,
            "detail": detail}


def grade() -> list[dict]:
    """Per-persona outcome, classified by the watchdog's own ladder."""
    by_email = {p["email"]: p for p in P.PERSONAS}
    out = []
    for p in persona_rows():
        with store.cursor() as cur:
            cur.execute(
                "select day as last_run_day, shortlist_n, widened, picks_n, sendable_n "
                "from digest_runs where profile_id = %s order by day desc limit 1",
                (p["id"],))
            row = cur.fetchone()
        run = dict(row) if row else {"last_run_day": None}
        picks = store.matched_jobs(p["id"], limit=100)
        scores = [j["score"] for j in picks if j.get("score") is not None]
        persona = by_email.get(p["email"], {})
        out.append({
            "key": persona.get("key", p["email"]),
            "note": persona.get("note"),
            "expect": persona.get("expect"),
            "shortlist_n": run.get("shortlist_n"),
            "widened": run.get("widened"),
            "picks_n": len(picks),
            "sendable_n": sum(1 for s in scores if s >= digestmod.EMAIL_MIN_SCORE),
            "score_max": max(scores) if scores else None,
            "score_mean": round(sum(scores) / len(scores), 2) if scores else None,
            "diagnosis": watchdog.diagnose(run),
            "top": [{"title": j.get("title"), "score": j.get("score"),
                     "city": j.get("city"), "remote": bool(j.get("remote_signal")),
                     "category": j.get("role_category")}
                    for j in picks[:5]],
        })
    return out


# ------------------------------------------------------------------------------ cli ---

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", action="store_true", help="push payloads through SubscribeIn")
    ap.add_argument("--verify-teardown", action="store_true",
                    help="seed 2 personas, delete them, prove the cascade — run this FIRST")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--pause", action="store_true",
                    help="quarantine the cohort out of the production export (idempotent)")
    ap.add_argument("--negatives", action="store_true", help="run the must-be-rejected cases")
    ap.add_argument("--export", metavar="FILE")
    ap.add_argument("--audit-file", metavar="FILE", default=None)
    ap.add_argument("--audit", action="store_true", help="report findings from --audit-file")
    ap.add_argument("--merge", nargs="+", metavar="FILE",
                    help="per-batch matcher outputs to concatenate into --merge-out")
    ap.add_argument("--merge-out", metavar="FILE", default="picks.json")
    ap.add_argument("--import", dest="import_path", metavar="FILE")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def show(obj):
        print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))

    if args.status:
        rows = persona_rows()
        show({"personas_in_db": len(rows), "real_profiles": real_profile_count(),
              "production_would_export": exportable_profile_count(),
              "unquarantined": [r["email"] for r in rows if r.get("paused_until") is None],
              "child_rows": _child_counts([str(r["id"]) for r in rows])})
    if args.pause:
        n = quarantine()
        show({"quarantined": n, "production_would_export": exportable_profile_count(),
              "real_profiles": real_profile_count()})
    if args.validate:
        problems = validate_payloads()
        show({"payloads": len(P.PERSONAS), "rejected_by_SubscribeIn": problems})
    if args.verify_teardown:
        # Seed two, then actually run retrieval for them so `digest_runs` has rows — a
        # cascade proved against empty child tables proves nothing. Only then delete.
        two = {P.PERSONAS[0]["key"], P.PERSONAS[1]["key"]}
        real_before = real_profile_count()
        print(f"real profiles before: {real_before}")
        show({"seed": seed(keys=two)})
        # The quarantine matters more than the cascade: teardown is the exit you take when the
        # session ends normally, and the 2026-07-27 incident was the session that did not.
        assert exportable_profile_count() == real_before, \
            "a seeded persona is visible to the production export"
        ids = [str(r["id"]) for r in persona_rows()]
        export("/tmp/verify-shortlists.json")
        show({"child_rows_after_export": _child_counts(ids)})
        result = teardown()
        show({"teardown": result})
        assert result["real_profiles_untouched"] == real_before, "a real profile was affected"
        assert all(v == 0 for v in result["child_rows_after"].values()), "cascade did not fire"
        print("OK: cascade fired, real profiles untouched")
    if args.seed:
        show(seed())
    if args.negatives:
        show(negative_cases())
    if args.export:
        show(export(args.export, args.audit_file))
    if args.audit:
        findings = audit_report(args.audit_file)
        show({"findings": findings, "count": len(findings)})
    if args.merge:
        show(merge_picks(args.merge, args.merge_out))
    if args.import_path:
        show({"imported": matcher.import_picks(args.import_path)})
    if args.send:
        show(send(dry_run=args.dry_run))
    if args.grade:
        show(grade())
    if args.teardown:
        show(teardown())


if __name__ == "__main__":
    main()
