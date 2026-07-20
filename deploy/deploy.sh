#!/usr/bin/env bash
# JobDigest deploy — one command, run from the repo root on a workstation.
#
#   deploy/deploy.sh                 deploy HEAD (must be clean and pushed)
#   deploy/deploy.sh --sha <sha>     deploy a specific commit (rollback)
#   deploy/deploy.sh --dry-run       show what would ship, touch nothing
#
# Replaces hand-assembled `scp` lists. Those were the standing drift risk: /opt/jobdigest
# was a pile of copied files, so "merged" and "deployed" could silently disagree and you
# found out at 05:00 when a timer fired. This ships the *committed tree* and records the
# sha on the box, so `deployed == a commit you can `git show`` is an invariant, not a hope.
#
# No credential is added to the VPS: the tree travels as `git archive | ssh tar -x`, so the
# box never needs a GitHub deploy key. Files git doesn't track — deploy/.env, exchange/,
# pgdata — are untouched, because tar only writes archive members.
#
# On a failed health check it restores the previously deployed sha and rebuilds. If that
# also fails it exits loudly rather than leaving you guessing which tree is live.
set -euo pipefail

SHA=""
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sha) SHA="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "usage: $0 [--sha <sha>] [--dry-run]" >&2; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

# The target host is deliberately NOT in this file. It is a public repo, and the origin IP
# is precisely what the Cloudflare proxy exists to hide — publishing it would undo the
# origin-lock from the 18 Jul review. Real values live in the gitignored
# deploy/.deploy-target (see deploy/.deploy-target.example) or in the environment.
# shellcheck source=/dev/null
[ -f deploy/.deploy-target ] && . deploy/.deploy-target

HOST="${JOBDIGEST_HOST:-}"
KEY="${JOBDIGEST_SSH_KEY:-$HOME/.ssh/id_ed25519_personal}"
REMOTE_DIR="${JOBDIGEST_REMOTE_DIR:-/opt/jobdigest}"
SHA_FILE="$REMOTE_DIR/.deployed-sha"

if [ -z "$HOST" ]; then
  echo "deploy: no target host. Set JOBDIGEST_HOST, or copy" \
       "deploy/.deploy-target.example to deploy/.deploy-target and fill it in." >&2
  exit 2
fi
SSH=(ssh -o BatchMode=yes -i "$KEY" "$HOST")

# --- refuse to deploy something you cannot point at -----------------------------
if [ -z "$SHA" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "deploy: REFUSING — working tree is dirty. Commit or stash first; a deploy that" \
         "isn't a commit can't be reproduced or rolled back." >&2
    exit 1
  fi
  SHA="$(git rev-parse HEAD)"
  # Deployed-but-unpushed is how a box ends up running code nobody else can see.
  if ! git merge-base --is-ancestor "$SHA" "origin/$(git rev-parse --abbrev-ref HEAD)" 2>/dev/null; then
    echo "deploy: REFUSING — HEAD is not pushed. Push first so the deployed commit exists" \
         "somewhere other than this laptop." >&2
    exit 1
  fi
fi
git cat-file -e "${SHA}^{commit}" 2>/dev/null || { echo "deploy: no such commit: $SHA" >&2; exit 1; }

SHORT="$(git rev-parse --short "$SHA")"
echo "deploy: shipping $SHORT — $(git log -1 --format=%s "$SHA")"

if [ "$DRY" = 1 ]; then
  echo "deploy: [dry-run] files that would ship:"
  git ls-tree -r --name-only "$SHA" | sed 's/^/  /' | head -40
  echo "  ... $(git ls-tree -r --name-only "$SHA" | wc -l) files total"
  exit 0
fi

PREV="$("${SSH[@]}" "cat $SHA_FILE 2>/dev/null || true")"
PREV="$(printf '%s' "$PREV" | tr -d '[:space:]')"     # strip the newline tar/echo leaves
PREV_SHORT="${PREV:0:7}"
[ -n "$PREV_SHORT" ] || PREV_SHORT="none"

ship() {   # ship <sha>  — extract the committed tree onto the box
  local sha="$1"
  git archive --format=tar "$sha" | "${SSH[@]}" "tar -x -C $REMOTE_DIR && echo $sha > $SHA_FILE"
}

build_and_check() {
  "${SSH[@]}" "set -e
    cd $REMOTE_DIR/deploy
    # Scripts run from the deployed tree, but the *units* live in /etc/systemd/system, so
    # they are the one thing a tree sync cannot update. Sync them here or a changed
    # schedule/Environment= silently never takes effect.
    sudo cp $REMOTE_DIR/deploy/jobdigest-*.service $REMOTE_DIR/deploy/jobdigest-*.timer /etc/systemd/system/
    sudo chmod +x $REMOTE_DIR/deploy/*.sh
    sudo systemctl daemon-reload
    sudo docker compose build api web pipeline
    sudo docker compose up -d api web
    for i in \$(seq 1 20); do
      if sudo docker compose exec -T api curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo 'deploy: api healthy'; exit 0
      fi
      sleep 3
    done
    echo 'deploy: api did NOT come up healthy' >&2; exit 1"
}

ship "$SHA"

if build_and_check; then
  echo "deploy: $SHORT live (previous: $PREV_SHORT)"
else
  echo "deploy: health check FAILED for $SHORT" >&2
  if [ -z "$PREV" ]; then
    echo "deploy: no previously deployed sha recorded — cannot auto-roll back." \
         "The box is running $SHORT; fix forward or deploy --sha <known-good>." >&2
    exit 1
  fi
  echo "deploy: rolling back to $PREV_SHORT..." >&2
  ship "$PREV"
  if build_and_check; then
    echo "deploy: rolled back to $PREV_SHORT. $SHORT was NOT deployed." >&2
    exit 1
  fi
  echo "deploy: ROLLBACK ALSO FAILED. The box needs hands — check" \
       "'docker compose logs api' on $HOST." >&2
  exit 2
fi
