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
        logger.info("Retention: blanked %d description(s) >%dd inactive, "
                    "deleted %d stale match row(s) >%dd, "
                    "rolled up %d event group(s), deleted %d raw event(s) >%dd, "
                    "erased %d unsubscribed profile(s) >%dd",
                    descs, RETENTION_DESC_DAYS, stale_matches, RETENTION_MATCH_DAYS,
                    rolled, events, RETENTION_EVENT_DAYS,
                    unsubbed, RETENTION_UNSUB_DAYS)

    if match:
        from service.matcher import run as match_run
        logger.info("Matching subscribers (AI rerank)...")
        match_run(limit_profiles=limit)

    profiles = store.sendable_profiles()
    if limit:
        profiles = profiles[:limit]
    logger.info("%d sendable profiles", len(profiles))

    sent = skipped_nojobs = skipped_notdue = 0
    for p in profiles:
        if not _is_due(p, now):
            skipped_notdue += 1
            continue
        jobs = digestmod.build_digest(p, limit=digestmod.DEFAULT_LIMIT)
        if not jobs:
            skipped_nojobs += 1                        # only when the matcher found nothing new
            continue

        total = store.match_count(p["id"])           # all matches, for the "see all N" CTA
        subject = digestmod.subject_line(p, jobs)
        html = digestmod.render_html(p, jobs, base_url=BASE_URL, total_matches=total)
        text = digestmod.render_text(p, jobs, base_url=BASE_URL, total_matches=total)
        unsub = digestmod._manage_url(p, "unsub")

        if dry_run:
            logger.info("[dry-run] would send %d jobs to %s (%s)", len(jobs), p["email"], subject)
            sent += 1
            continue

        ref = mailer.send(p["email"], subject, html, text, list_unsubscribe=unsub)
        store.record_sends(p["id"], [(j["posting_id"], j["score"]) for j in jobs])
        store.mark_digest_sent(p["id"])
        sent += 1
        logger.info("Sent %d jobs to %s -> %s", len(jobs), p["email"], ref)

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
