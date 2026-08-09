#!/usr/bin/env bash
# JobDigest title classification — VPS side (no metered API; free tier).
#
# The same two-phase shape as jobdigest-match.sh, for a different question: what role
# category do the titles carry that neither the patterns nor the publishers' occupation
# codes could read. JSON travels via the same private Drive folder, in its own subfolder.
#
#   export : uncategorised titles -> titles.json -> Drive/categorize/
#   (routine, weekly: reads titles.json -> writes categories.json to Drive/categorize/)
#   import : pull categories.json -> validate every row -> `title_categories`
#
# **This is deliberately NOT part of the daily digest window.** That window runs 03:00 ->
# ~06:00 and its deadline is the matcher routine, not the import: work added to it does not
# make the digest late, it makes the digest *miss*, silently, for every subscriber. This runs
# weekly, hours away from it, and a total failure here costs a slower-shrinking residue and
# nothing else — no digest, no subscriber, no email depends on it.
#
# Usage:  jobdigest-categorize.sh export | import
set -euo pipefail

cd /opt/jobdigest/deploy
EX=/opt/jobdigest/exchange
REMOTE="${JOBDIGEST_GDRIVE_REMOTE:-gdrive:JobDigest}/categorize"
COMPOSE="sudo docker compose"

# Same ownership reasoning as jobdigest-match.sh: the pipeline container runs as uid 1000
# (`app`), the same numeric uid as the host's `deploy` user, so the directory can stay 0700.
# Unlike shortlists.json these files carry no personal data at all — public job titles and
# integer indices — but the directory is shared with files that do.
mkdir -p "$EX"
chown 1000:1000 "$EX"
chmod 700 "$EX"

case "${1:-}" in
  export)
    $COMPOSE run --rm pipeline python -m service.categorize_exchange \
      export /exchange/titles.json
    rclone copyto "$EX/titles.json" "$REMOTE/titles.json"
    echo "categorize export: titles.json pushed to $REMOTE"
    ;;
  import)
    # Drive permits duplicate filenames and the routine writes a NEW file each run rather
    # than overwriting, so copies accumulate — collapse to the newest before pulling, or
    # `copyto` picks one non-deterministically. (Learned on the matcher, 2026-07-20.)
    rclone dedupe --dedupe-mode newest "$REMOTE" || echo "import: dedupe failed (continuing)" >&2

    if ! rclone copyto "$REMOTE/categories.json" "$EX/categories.json"; then
      echo "categorize import: no categories.json on $REMOTE — routine has not run yet" >&2
      exit 0
    fi
    # rclone runs as root under systemd, so the pulled file lands root-owned and the
    # container (uid 1000) cannot read it out of a 0700 directory.
    chown 1000:1000 "$EX/categories.json"

    # Refuse answers the routine has not refreshed. Re-importing last week's file would be
    # harmless in itself (the writes are idempotent), but it would hide a routine that has
    # stopped running behind a clean exit — the residue would simply stop shrinking and
    # nothing would say why. A week's cadence plus a day of slack is 8 days.
    age_d=$(( ( $(date +%s) - $(date -r "$EX/categories.json" +%s) ) / 86400 ))
    if [ "$age_d" -gt "${JOBDIGEST_CATEGORIES_MAX_AGE_D:-8}" ]; then
      echo "categorize import: categories.json is ${age_d}d old (> ${JOBDIGEST_CATEGORIES_MAX_AGE_D:-8}d)" \
           "— refusing stale answers. Check the claude.ai routine." >&2
      exit 1
    fi

    # `--asked` is not a convenience: answers arrive by integer index, and the export file is
    # the only thing that says which index was which title. Without it nothing could be
    # validated, and the import refuses rather than trusting the answer file's own account.
    $COMPOSE run --rm pipeline python -m service.categorize_exchange \
      import /exchange/categories.json --asked /exchange/titles.json

    # Archive the consumed file so it can never be re-imported and duplicates cannot pile up.
    stamp=$(date -u +%Y%m%d-%H%M%S)
    rclone moveto "$REMOTE/categories.json" "$REMOTE/processed/categories-$stamp.json" \
      || echo "categorize import: could not archive categories.json (continuing)" >&2
    echo "categorize import: answers loaded (archived as processed/categories-$stamp.json)"

    # The corpus does not move here. `upsert_postings` rewrites `role_category` on conflict
    # and every active posting is re-seen daily, so the newly-cached answers reach the
    # postings table on the next 03:00 ingest — and `categorization_daily` records the drop
    # the morning after. Nothing needs backfilling, and nothing should be run here to hurry it.
    ;;
  *)
    echo "usage: $0 export|import" >&2
    exit 2
    ;;
esac
