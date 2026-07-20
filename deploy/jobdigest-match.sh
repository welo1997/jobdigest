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

mkdir -p "$EX"; chmod 777 "$EX"   # container 'app' user must be able to write here

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
    rclone dedupe --dedupe-mode newest "$REMOTE" 2>/dev/null || true
    rclone copyto "$REMOTE/picks.json" "$EX/picks.json"
    $COMPOSE run --rm pipeline python -m service.matcher --import /exchange/picks.json
    $COMPOSE run --rm pipeline python -m service.pipeline   # send digests from matches
    echo "import: picks loaded + digests sent"
    ;;
  *)
    echo "usage: $0 export|import" >&2
    exit 2
    ;;
esac
