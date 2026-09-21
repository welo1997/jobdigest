#!/usr/bin/env bash
# Is what master says running on the box?
#
# On 2026-09-06 a fix for a disk-full outage was merged. On 2026-09-20 the same outage
# happened again — Postgres PANICked on a checkpoint it could not write and crash-looped for
# ~24 h — because that fix had sat in master for fifteen days and never been deployed. The box
# was running the tree from 2026-08-27. Nothing anywhere said so.
#
# CLAUDE.md already states the rule ("a merged commit is not a deployed commit"); this is the
# thing that checks it. Deliberately a LOCAL script and not a box-side timer: the repo is
# private, so a check running on the VPS would need a repo credential installed on the
# production host, and the disclosure that buys is worse than the drift it detects. Here it
# runs where both halves are already readable — your git remote and your SSH key.
#
# Usage:  scripts/check_deploy_drift.sh            # compare origin/master to the box
#         scripts/check_deploy_drift.sh --quiet    # print only on drift (for a shell hook)
#
# Exit codes:  0 in sync | 1 drifted | 2 could not determine (network, SSH, missing target)
set -uo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "drift: not inside a git checkout" >&2; exit 2; }
cd "$repo_root" || exit 2

# Same target resolution as deploy/deploy.sh, on purpose: two ways to name the box is two
# things to keep in step, and this check is worthless if it can look at a different host than
# the one deploy.sh ships to.
[ -f deploy/.deploy-target ] && . deploy/.deploy-target
HOST="${JOBDIGEST_HOST:-}"
KEY="${JOBDIGEST_SSH_KEY:-$HOME/.ssh/id_ed25519_personal}"
[ -n "$HOST" ] || { echo "drift: no target host. Set JOBDIGEST_HOST, or copy" \
                         "deploy/.deploy-target.example to deploy/.deploy-target." >&2; exit 2; }

# Ask the remote directly rather than trusting a possibly stale local ref — the whole failure
# being guarded against is a local view that disagrees with reality.
remote_sha=$(git ls-remote origin refs/heads/master 2>/dev/null | cut -f1)
[ -n "$remote_sha" ] || { echo "drift: could not read origin/master" >&2; exit 2; }

box_sha=$(ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$HOST" \
            'cat /opt/jobdigest/.deployed-sha 2>/dev/null' 2>/dev/null | tr -dc '0-9a-f')
[ -n "$box_sha" ] || { echo "drift: could not read .deployed-sha from $HOST" >&2; exit 2; }

if [ "$remote_sha" = "$box_sha" ]; then
  [ "$QUIET" -eq 1 ] || echo "drift: in sync — ${box_sha:0:7} is master and is deployed"
  exit 0
fi

# Count and name what is missing. `git log` needs the objects locally, so fetch quietly first;
# if that fails we can still report the shas, which is the load-bearing half.
git fetch -q origin master 2>/dev/null
behind=$(git rev-list --count "${box_sha}..${remote_sha}" 2>/dev/null)

echo "drift: BOX IS BEHIND MASTER"
echo "  master     ${remote_sha:0:7}"
echo "  deployed   ${box_sha:0:7}   on ${HOST#*@}"
if [ -n "$behind" ] && [ "$behind" -gt 0 ] 2>/dev/null; then
  echo "  ${behind} undeployed commit(s):"
  git log --oneline "${box_sha}..${remote_sha}" 2>/dev/null | sed 's/^/    /'
fi
echo "  deploy with:  deploy/deploy.sh"
exit 1
