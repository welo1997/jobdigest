"""Per-subscriber watchdog — fail loudly when the pipeline quietly fails one person.

    DATABASE_URL=... python -m service.watchdog [--days 3] [--demand] [--quiet]

The failure mode this exists for has happened, repeatedly, and never once raised an error.
On 2026-07-26 one subscriber review turned up five independent matching defects; every timer
was green through all of them, because the pipeline's own health and a subscriber's outcome
are different things. A run that emails nobody anything is, to systemd, a successful run.

So this checks the thing that actually matters — *did each subscriber get a digest* — and
exits non-zero when someone has gone `--days` without one. The systemd unit carries
`OnFailure=jobdigest-alert@%n.service`, and that alerter mails the last journal lines, so
the report printed here becomes the body of the alert. No second notification path.

It reports the diagnosis alongside, because the two ways a subscriber gets nothing look
identical from outside and want opposite responses:

  RETRIEVAL   shortlist_n at or near zero, or `widened` — we never put a fair set of jobs in
              front of the model. This is a bug in our code: their categories or keywords
              match nothing, which is what happens to anyone whose field the taxonomy does
              not model. Fix the retrieval, not the subscriber.
  MATCHER     a healthy shortlist_n with picks_n zero — the model saw a fair shortlist and
              judged nothing good enough. Not a bug. Possibly thin inventory that week.
  DELIVERY    picks exist but sendable_n is zero — everything scored under EMAIL_MIN_SCORE,
              so it is on /matches but not in an inbox. Working as designed; only a problem
              if it persists.

Subscriber addresses are masked. The operator can identify anyone from the profile id, and
this text leaves the box in an email — the same reason `shortlists.json` carries no address.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import store  # noqa: E402

logger = logging.getLogger("service.watchdog")

#: Days without a digest before a subscriber counts as starved. Three rather than one: a
#: weekly subscriber, a quiet inventory day and a single skipped run are all normal, and an
#: alert that cries wolf gets filtered into a folder nobody opens.
STARVED_DAYS = int(os.environ.get("WATCHDOG_STARVED_DAYS", "3"))


def _mask(email: str | None) -> str:
    """`subscriber-a@example.com` -> `sub***@example.com`. Enough to recognise, not to contact."""
    if not email or "@" not in email:
        return "(no address)"
    local, _, domain = email.partition("@")
    return f"{local[:3]}***@{domain}"


def diagnose(row: dict) -> str:
    """Which of the three failure shapes this profile is in. See the module docstring."""
    if row.get("last_run_day") is None:
        return "NO RUN    never appeared in a digest run — check the profile is sendable"
    shortlist, picks = row.get("shortlist_n") or 0, row.get("picks_n") or 0
    if row.get("widened"):
        return (f"RETRIEVAL shortlist widened to {shortlist} (their filters matched almost "
                f"nothing) — likely a role the taxonomy does not model")
    if shortlist < store.SHORTLIST_FLOOR:
        return f"RETRIEVAL only {shortlist} candidates retrieved — the shortlist is starved"
    if picks == 0:
        return f"MATCHER   {shortlist} candidates -> 0 picks — model found nothing fitting"
    if (row.get("sendable_n") or 0) == 0:
        return f"DELIVERY  {picks} picks, none above EMAIL_MIN_SCORE — on /matches, not email"
    return f"UNKNOWN   {shortlist} candidates, {picks} picks, {row.get('sendable_n')} sendable"


def report(days: int = STARVED_DAYS, demand: bool = False) -> tuple[str, int]:
    """Build the report text and the count of starved subscribers."""
    starved = store.starved_profiles(days)
    lines: list[str] = []

    if starved:
        lines.append(f"{len(starved)} subscriber(s) with no digest in {days} days:\n")
        for row in starved:
            last = row.get("last_digest_at")
            lines.append(f"  {_mask(row.get('email'))}  profile {str(row['id'])[:8]}")
            lines.append(f"    last digest: {last.date() if last else 'never'}")
            lines.append(f"    {diagnose(row)}")
        lines.append("")
        lines.append("Look at one of them end to end before changing anything:")
        lines.append("  python -m service.matcher --email <address> --dry-run")
    else:
        lines.append(f"OK — every active subscriber has had a digest within {days} days.")

    if demand:
        terms = store.unmet_demand_terms()
        lines.append("")
        if terms:
            lines.append("Words subscribers asked for that no category models "
                         "(a report — never promote one automatically):")
            for t in terms[:25]:
                lines.append(f"  {t['n']:>3}x  {t['term']}")
        else:
            lines.append("No unmodelled role terms in subscriber preferences.")

    return "\n".join(lines), len(starved)


def main() -> int:
    ap = argparse.ArgumentParser(description="Alert when a subscriber gets no digest.")
    ap.add_argument("--days", type=int, default=STARVED_DAYS,
                    help=f"days without a digest before alerting (default {STARVED_DAYS})")
    ap.add_argument("--demand", action="store_true",
                    help="also list role words subscribers asked for that no category models")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when healthy (still exits non-zero when not)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")

    text, n_starved = report(days=args.days, demand=args.demand)
    if n_starved or not args.quiet:
        print(text)
    # Non-zero is the alert: systemd's OnFailure hands the journal to jobdigest-alert.py.
    return 1 if n_starved else 0


if __name__ == "__main__":
    sys.exit(main())
