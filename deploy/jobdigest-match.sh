#!/usr/bin/env bash
# JobDigest daily matching — VPS side (no metered API; free tier).
#
# Two phases, with the claude.ai cloud routine running in between. JSON travels via a
# private Google Drive folder (rclone remote), because the routine has a Drive connector
# but no DB access. See deploy/matcher-routine.md for the routine + one-time setup.
#
#   export : ingest fresh postings -> write shortlists.json -> push to Drive
#   (routine, ~1h later: reads shortlists.json from Drive -> writes picks.json to Drive)
#   import : pull picks.json from Drive -> load into `matches` -> send digests
#
# Usage:  jobdigest-match.sh export | import
set -euo pipefail

cd /opt/jobdigest/deploy
EX=/opt/jobdigest/exchange
REMOTE="${JOBDIGEST_GDRIVE_REMOTE:-gdrive:JobDigest}"   # rclone remote:folder
COMPOSE="sudo docker compose"

# The pipeline container runs as uid 1000 (`app`), the same numeric uid as the host's
# `deploy` user — so this can be owner-private and the container still writes fine. It was
# 0777, which made shortlists.json (subscriber preferences + CV summaries) readable by any
# local account, and let any local process swap picks.json between the rclone pull below
# and the container reading it — i.e. choose what every subscriber gets emailed.
mkdir -p "$EX"
chown 1000:1000 "$EX"
chmod 700 "$EX"

case "${1:-}" in
  export)
    $COMPOSE run --rm pipeline python -m service.ingest --cz
    $COMPOSE run --rm pipeline python -m service.matcher --export /exchange/shortlists.json
    rclone copyto "$EX/shortlists.json" "$REMOTE/shortlists.json"
    echo "export: shortlists.json pushed to $REMOTE"
    ;;
  import)
    # Google Drive permits duplicate filenames, and the claude.ai routine writes a NEW
    # picks.json each run instead of overwriting — so copies accumulate. Collapse to the
    # newest before pulling, or rclone copyto would pick one non-deterministically.
    # Errors are tolerated (|| true) but no longer hidden: `2>/dev/null` previously made a
    # silently-failing dedupe indistinguishable from a successful one (seen 2026-07-20,
    # where duplicates survived a service run but collapsed fine when run by hand).
    rclone dedupe --dedupe-mode newest "$REMOTE" || echo "import: dedupe failed (continuing)" >&2

    # Refuse to run on picks the routine hasn't refreshed. Without this, a routine that
    # didn't run means yesterday's picks.json is silently re-imported — matches get
    # re-dated and a digest can go out built from stale shortlists.
    if ! rclone copyto "$REMOTE/picks.json" "$EX/picks.json"; then
      echo "import: no picks.json on $REMOTE — routine has not run yet, nothing to do" >&2
      exit 0
    fi
    # rclone runs as root under systemd, so the pulled file lands root-owned. Hand it to
    # uid 1000 explicitly rather than relying on root's umask leaving it world-readable —
    # now that the directory is 0700, a 0600 root-owned file would be unreadable to the
    # container and the matcher would fail silently, which is the exact failure shape that
    # bit us with RCLONE_CONFIG on 19 Jul.
    chown 1000:1000 "$EX/picks.json"
    age_h=$(( ( $(date +%s) - $(date -r "$EX/picks.json" +%s) ) / 3600 ))
    if [ "$age_h" -gt "${JOBDIGEST_PICKS_MAX_AGE_H:-20}" ]; then
      echo "import: picks.json is ${age_h}h old (> ${JOBDIGEST_PICKS_MAX_AGE_H:-20}h) — refusing to" \
           "import stale picks. Check the claude.ai routine." >&2
      exit 1
    fi

    $COMPOSE run --rm pipeline python -m service.matcher --import /exchange/picks.json
    $COMPOSE run --rm pipeline python -m service.pipeline   # send digests from matches

    # Archive the consumed file so it can never be re-imported, and so duplicates cannot
    # accumulate in the first place. This is what actually makes the dedupe above a
    # belt-and-braces step rather than load-bearing.
    stamp=$(date -u +%Y%m%d-%H%M%S)
    rclone moveto "$REMOTE/picks.json" "$REMOTE/processed/picks-$stamp.json" \
      || echo "import: could not archive picks.json (continuing)" >&2
    echo "import: picks loaded + digests sent (archived as processed/picks-$stamp.json)"

    # Did the matcher actually answer for every subscriber the export asked about? A file
    # covering 5 of 30 profiles imports cleanly, exits 0, and leaves 25 people with no digest
    # and no error anywhere — the checks above only prove picks.json exists and is fresh, which
    # a truncated file also is. This is deliberately the LAST step, and under `set -e` its
    # non-zero exit is what raises the alert: everything that matters (import, send, archive)
    # has already happened, so noticing costs nobody their digest.
    $COMPOSE run --rm pipeline python -m service.matcher --check-coverage /exchange/picks.json
    ;;
  *)
    echo "usage: $0 export|import" >&2
    exit 2
    ;;
esac
