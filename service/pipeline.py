"""The once-a-day pipeline — what cron (n8n / systemd timer) runs.

    (optional) ingest fresh postings
    (optional) match      -> AI reranks each subscriber's shortlist -> matches  (service.matcher)
    for each active, confirmed, non-paused, due subscriber:
        build_digest  -> read AI picks from `matches` + drop already-sent
        render        -> Direction-A HTML + plaintext
        send          -> provider-agnostic mailer (file backend by default)
        record        -> digest_sends (never repeat a job) + last_digest_at

Inference is batched in the --match step (needs ANTHROPIC_API_KEY); the send loop itself
does no inference. `--match` is where the daily AI cost lives — bounded by subscribers.

    DATABASE_URL=... python -m service.pipeline [--ingest] [--cz] [--match] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import digest as digestmod  # noqa: E402
from service import mailer, store         # noqa: E402

logger = logging.getLogger("service.pipeline")
BASE_URL = os.environ.get("BASE_URL", "https://jobdigest.eu")

# Retention: blank descriptions of postings inactive this long (rows are kept), and drop
# un-acted-on matches for postings gone this long. Descriptions are ~81% of table size.
RETENTION_DESC_DAYS = int(os.environ.get("RETENTION_DESC_DAYS", "90"))
RETENTION_MATCH_DAYS = int(os.environ.get("RETENTION_MATCH_DAYS", "180"))
# Raw analytics events. Rolled up to events_daily first, so pruning loses no trend data.
RETENTION_EVENT_DAYS = int(os.environ.get("RETENTION_EVENT_DAYS", "180"))
# Unsubscribed profiles. This one is a promise, not an optimisation: the privacy policy says
# the profile is deleted within 30 days of unsubscribing. Suppression rows are kept forever.
RETENTION_UNSUB_DAYS = int(os.environ.get("RETENTION_UNSUB_DAYS", "30"))


def _is_due(profile: dict, now: datetime) -> bool:
    """Frequency gate + don't send twice in one day."""
    last = profile.get("last_digest_at")
    if last and last.date() == now.date():
        return False
    freq = profile.get("frequency", "daily")
    if freq == "weekdays" and now.weekday() >= 5:      # Sat/Sun
        return False
    if freq == "weekly" and now.weekday() != 0:        # Mondays only
        return False
    return True


def send_one(profile: dict, base_url: str = BASE_URL, dry_run: bool = False) -> dict:
    """Build and send one subscriber's digest. Returns {"sent": bool, "n": int}.

    The single per-subscriber send unit, shared by the daily loop and the on-demand endpoint.
    `build_digest` reads the matcher's picks from `matches` and drops anything already emailed
    (`digest_sends`), so an on-demand send can never repeat a job the daily run already sent —
    the dedup guarantee holds across both paths for free. On a real send it records
    `digest_sends` + `last_digest_at`, which is also why an on-demand run earlier in the day
    stops that day's automatic run from sending the same subscriber twice (`_is_due`)."""
    jobs = digestmod.build_digest(profile, limit=digestmod.DEFAULT_LIMIT)
    if not dry_run:
        # Recorded before the empty-return: "this subscriber had nothing to send" is the
        # outcome most worth seeing, and the one that otherwise leaves no trace anywhere.
        store.record_digest_run(profile["id"], sendable_n=len(jobs))
    if not jobs:
        return {"sent": False, "n": 0}

    total = store.match_count(profile["id"])         # all matches, for the "see all N" CTA
    subject = digestmod.subject_line(profile, jobs)
    html = digestmod.render_html(profile, jobs, base_url=base_url, total_matches=total)
    text = digestmod.render_text(profile, jobs, base_url=base_url, total_matches=total)
    unsub = digestmod._manage_url(profile, "unsub")

    if dry_run:
        logger.info("[dry-run] would send %d jobs to %s (%s)", len(jobs), profile["email"], subject)
        return {"sent": True, "n": len(jobs)}

    ref = mailer.send(profile["email"], subject, html, text, list_unsubscribe=unsub)
    store.record_sends(profile["id"], [(j["posting_id"], j["score"]) for j in jobs])
    store.mark_digest_sent(profile["id"])
    store.record_digest_run(profile["id"], sent=True)
    logger.info("Sent %d jobs to %s -> %s", len(jobs), profile["email"], ref)
    return {"sent": True, "n": len(jobs)}


def run(ingest: bool = False, cz: bool = False, match: bool = False,
        limit: int | None = None, dry_run: bool = False, prune: bool = True) -> dict:
    now = datetime.now(timezone.utc)

    if ingest:
        from service.ingest import run as ingest_run
        logger.info("Ingesting fresh postings (cz=%s)...", cz)
        ingest_run(include_cz=cz, stale_days=7)

    # Retention runs after ingest (which sets is_active) and before sending, so a pruned
    # posting can never be picked for a digest mid-run. Cheap: only rows crossing the
    # threshold today are touched.
    if prune and not dry_run:
        descs = store.prune_descriptions(RETENTION_DESC_DAYS)
        stale_matches = store.prune_matches(RETENTION_MATCH_DAYS)
        # Roll up yesterday BEFORE pruning, so a trend line never loses a day even if the
        # retention window and the rollup ever meet.
        rolled = store.rollup_events()
        events = store.prune_events(RETENTION_EVENT_DAYS)
        unsubbed = store.prune_unsubscribed(RETENTION_UNSUB_DAYS)
        sessions = store.prune_expired_sessions()
        intents = store.prune_expired_signup_intents()
        logger.info("Retention: blanked %d description(s) >%dd inactive, "
                    "deleted %d stale match row(s) >%dd, "
                    "rolled up %d event group(s), deleted %d raw event(s) >%dd, "
                    "erased %d unsubscribed profile(s) >%dd, "
                    "pruned %d expired session(s), %d expired signup intent(s)",
                    descs, RETENTION_DESC_DAYS, stale_matches, RETENTION_MATCH_DAYS,
                    rolled, events, RETENTION_EVENT_DAYS,
                    unsubbed, RETENTION_UNSUB_DAYS, sessions, intents)

    # Compute the send set once. `due` is who actually gets an email today (frequency +
    # not-already-sent), and it is what the matcher is pointed at — matching only the due
    # subscribers is what stops a weekly subscriber being AI-matched, and billed, on the five
    # days no digest goes out for them. `nulls-first` priority order is preserved.
    profiles = store.sendable_profiles()
    if limit:
        profiles = profiles[:limit]
    due = [p for p in profiles if _is_due(p, now)]
    skipped_notdue = len(profiles) - len(due)
    logger.info("%d sendable profiles, %d due today", len(profiles), len(due))

    # Link liveness: deactivate postings whose page positively says the role is gone, BEFORE the
    # matcher reads is_active and before /matches shows them. Off unless LIVENESS_ENABLED; a
    # dry-run pipeline can only turn writing off, never on (apply=not dry_run). Non-fatal by
    # construction — this must never cost a digest. See service/liveness.py.
    try:
        from service import liveness
        liveness.run(due_profiles=due, apply=not dry_run)
    except Exception:                                  # pragma: no cover - defensive
        logger.exception("liveness sweep failed (digest unaffected)")

    if match:
        from service.matcher import run as match_run
        logger.info("Matching %d due subscriber(s) (AI rerank)...", len(due))
        match_run(profiles=due, dry_run=dry_run)

    sent = skipped_nojobs = 0
    for p in due:
        r = send_one(p, base_url=BASE_URL, dry_run=dry_run)
        if r["sent"]:
            sent += 1
        else:
            skipped_nojobs += 1                        # only when the matcher found nothing new

    summary = {"sendable": len(profiles), "sent": sent,
               "skipped_no_matches": skipped_nojobs, "skipped_not_due": skipped_notdue}
    logger.info("Digest run complete: %s", summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", action="store_true", help="fetch fresh postings first")
    ap.add_argument("--cz", action="store_true", help="include Czech sources when ingesting")
    ap.add_argument("--match", action="store_true", help="AI-rerank shortlists before sending (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--limit", type=int, help="cap profiles processed (testing)")
    ap.add_argument("--dry-run", action="store_true", help="build + log, but don't send/record")
    ap.add_argument("--no-prune", action="store_true",
                    help="skip the retention step (descriptions / stale matches)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(ingest=args.ingest, cz=args.cz, match=args.match, limit=args.limit,
        dry_run=args.dry_run, prune=not args.no_prune)


if __name__ == "__main__":
    main()
