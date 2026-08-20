"""Daily link-liveness sweep — deactivate postings whose page positively says the role is gone.

The gap this closes
-------------------
`deactivate_stale` asks one question: *did the source's feed still mention this job in the last
7 days?* Nothing else in the product has ever fetched a stored URL. So a posting the feed keeps
re-listing stays `is_active` even after its own page has 404'd or started saying "this offer is no
longer valid" — the feed and the page disagree, and we only ever looked at the feed. The symptom
is a subscriber clicking a `/matches` link and landing on a dead page (measured 2026-08-20:
startupjobs and cocuma rows, re-listed that morning, whose pages were already expired).

What this does
--------------
Once a day, after ingest and before the matcher runs, it probes the postings a subscriber can
actually reach today — **the union of every scored `/matches` row and every due subscriber's
shortlist** — and deactivates the ones a page *positively* reports as gone. Because it runs
before the matcher (which reads `is_active`) and because `/matches` filters on `is_active`, one
`deactivate_postings` call both stops a dead job being emailed and removes it from the page.

The safety argument (why this cannot quietly delete live jobs)
-------------------------------------------------------------
It acts **only** on `link_probe`'s `CLOSED` verdict — real server-rendered text that says the
role expired or was not found — and abstains on every ambiguous verdict (`SHELL`, `BLOCKED`,
`UNCONFIRMED`, `UNREACHABLE`, `THROTTLED`, `MISMATCH`, `WEAK`, `OK`). A bare 404 is *not* enough
(`form3`/`roblox` answer 404 while rendering fine in a browser — the whole reason `link_probe`
never returns a gone-verdict off a status code). So a false deactivation would require a *live*
posting's page to contain closed-listing prose, which is why the action is safe to automate at all.

It is also **non-fatal and bounded**, on the same principle as the embed/shadow step: this runs
inside the one job that decides whether real people get email tomorrow (`jobdigest-match.sh` under
`set -euo pipefail`), so a slow host, a probe that raises, or a DB hiccup must cost a measurement,
never a digest. Every probe is wrapped, `run()` never raises, a wall-clock budget stops it walking
off, and a per-run cap bounds how many URLs it will touch.

Rollout
-------
Two env switches, so it can land dark and be proven on live data before it writes:

  * ``LIVENESS_ENABLED``  (default off) — master switch. Off ⇒ the step does nothing at all.
  * ``LIVENESS_APPLY``    (default off) — when off, it is a **dry run**: it probes and logs
    exactly what it *would* deactivate, and writes nothing. Flip on after reading a dry-run log.

  * ``LIVENESS_MAX_URLS``     (default 3000) — hard cap on URLs probed per run.
  * ``LIVENESS_TIME_BUDGET``  (default 1200s) — stop probing past this wall-clock budget; the
    rows not yet reached are simply left active (abstain), never guessed.
"""

from __future__ import annotations

import logging
import os
import time

from ingestion import link_probe
from service import store

logger = logging.getLogger("service.liveness")


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


LIVENESS_ENABLED = _flag("LIVENESS_ENABLED", False)
LIVENESS_APPLY = _flag("LIVENESS_APPLY", False)
LIVENESS_MAX_URLS = int(os.environ.get("LIVENESS_MAX_URLS", "3000"))
LIVENESS_TIME_BUDGET = int(os.environ.get("LIVENESS_TIME_BUDGET", "1200"))


def _target_ids(due_profiles: list[dict] | None) -> set[str]:
    """Posting ids to probe: every scored `/matches` row ∪ every due shortlist candidate.

    The shortlist half reuses the matcher's own prefilter (`query_shortlist_meta`), so the two
    stay one definition of "what this subscriber would be shown". A profile whose shortlist
    query fails is logged and skipped — one subscriber's retrieval hiccup must not sink the
    sweep for everyone."""
    from service.matcher import SHORTLIST_SIZE

    ids = store.matched_active_posting_ids()
    for p in due_profiles or []:
        try:
            shortlist, _ = store.query_shortlist_meta(p, limit=SHORTLIST_SIZE)
        except Exception:                                    # noqa: BLE001 — see docstring
            logger.exception("liveness: shortlist for profile %s failed; skipping it",
                             p.get("id"))
            continue
        ids.update(c["posting_id"] for c in shortlist if c.get("posting_id"))
    return ids


def run(due_profiles: list[dict] | None = None, apply: bool | None = None) -> dict:
    """Probe the matched+shortlist postings and deactivate the confidently-closed ones.

    `apply` overrides `LIVENESS_APPLY` **only downward** — the pipeline passes `apply=not dry_run`
    so a `--dry-run` pipeline never writes, but it can never turn writing *on* past the env flag.
    Never raises; returns a summary dict for the caller's log."""
    if not LIVENESS_ENABLED:
        logger.info("liveness: disabled (set LIVENESS_ENABLED=1 to run)")
        return {"enabled": False}

    do_apply = LIVENESS_APPLY if apply is None else (LIVENESS_APPLY and apply)
    summary = {"enabled": True, "apply": do_apply, "probed": 0, "closed": 0,
               "deactivated": 0, "budget_hit": False, "verdicts": {}}

    try:
        target_ids = _target_ids(due_profiles)
        targets = store.posting_targets(target_ids)
    except Exception:                                        # noqa: BLE001 — non-fatal by design
        logger.exception("liveness: could not gather targets; skipping the sweep")
        return summary

    if len(targets) > LIVENESS_MAX_URLS:
        logger.warning("liveness: %d targets exceeds LIVENESS_MAX_URLS=%d; probing the first %d",
                       len(targets), LIVENESS_MAX_URLS, LIVENESS_MAX_URLS)
        targets = targets[:LIVENESS_MAX_URLS]

    deadline = time.monotonic() + LIVENESS_TIME_BUDGET
    closed: list[str] = []
    verdicts: dict[str, int] = {}
    for t in targets:
        if time.monotonic() >= deadline:
            summary["budget_hit"] = True
            logger.warning("liveness: time budget %ds hit after %d/%d probes; the rest stay "
                           "active (abstain, never guessed)", LIVENESS_TIME_BUDGET,
                           summary["probed"], len(targets))
            break
        try:
            verdict, note = link_probe.probe(t.get("url") or "", t.get("title") or "",
                                             t.get("company"))
        except Exception:                                    # noqa: BLE001 — one bad probe is not fatal
            logger.exception("liveness: probe raised for %s (%s); abstaining",
                             t.get("posting_id"), t.get("url"))
            verdict, note = "ERROR", "probe raised"
        summary["probed"] += 1
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict in link_probe.DEACTIVATE_VERDICTS:
            closed.append(t["posting_id"])
            logger.info("liveness: CLOSED %s [%s] %s — %s", t.get("posting_id"), t.get("source"),
                        t.get("url"), note)

    summary["verdicts"] = verdicts
    summary["closed"] = len(closed)

    if closed and do_apply:
        try:
            summary["deactivated"] = store.deactivate_postings(closed)
        except Exception:                                    # noqa: BLE001 — non-fatal by design
            logger.exception("liveness: deactivate_postings failed for %d id(s)", len(closed))
    elif closed:
        logger.info("liveness: DRY RUN — would deactivate %d posting(s); set LIVENESS_APPLY=1 "
                    "to act", len(closed))

    logger.info("liveness: probed %d, closed %d, deactivated %d (apply=%s, budget_hit=%s); "
                "verdicts=%s", summary["probed"], summary["closed"], summary["deactivated"],
                do_apply, summary["budget_hit"], verdicts)
    return summary
