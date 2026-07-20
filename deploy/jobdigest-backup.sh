#!/usr/bin/env bash
# JobDigest Postgres backup — nightly pg_dump, gzipped, with rotation.
#
# The pgdata Docker volume is the only copy of every subscriber, their preferences, and
# the digest_sends never-repeat ledger. digest_sends in particular CANNOT be rebuilt:
# lose it and every subscriber gets re-sent jobs they've already seen.
#
# Dump is custom-format (-Fc): compressed, and restorable selectively with pg_restore.
# At current size (~180 MB DB) this takes seconds and produces a ~40 MB file.
#
# Usage:  jobdigest-backup.sh [backup|restore <file>|verify]
#
# Off-box copies (STRONGLY recommended — a backup on the same VPS does not survive losing
# the VPS): set JOBDIGEST_BACKUP_REMOTE to an rclone remote, e.g. "gdrive:JobDigest/backups".
# rclone is already installed for the matcher routine (see matcher-routine.md).
set -euo pipefail

COMPOSE_DIR=/opt/jobdigest/deploy
BACKUP_DIR="${JOBDIGEST_BACKUP_DIR:-/var/backups/jobdigest}"
KEEP_DAYS="${JOBDIGEST_BACKUP_KEEP_DAYS:-30}"
REMOTE="${JOBDIGEST_BACKUP_REMOTE:-}"
COMPOSE="docker compose"

cd "$COMPOSE_DIR"
mkdir -p "$BACKUP_DIR"

# Service name in deploy/docker-compose.yml is `db`; POSTGRES_USER/DB are both `jobmatch`.
do_backup() {
  local stamp file
  # Skip cleanly when the stack is down — a stopped site must not look like a backup
  # failure, and pg_dump against a dead container would just emit an error into the file.
  local cid
  cid="$($COMPOSE ps -q db 2>/dev/null || true)"
  if [ -z "$cid" ] || [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" != "true" ]; then
    echo "backup: db container not running, skipping"
    exit 0
  fi

  stamp=$(date -u +%Y%m%d-%H%M%S)
  file="$BACKUP_DIR/jobdigest-$stamp.dump"

  # -Fc = custom format. Write to a temp name first so a crashed dump is never mistaken
  # for a good backup by the rotation or restore paths.
  $COMPOSE exec -T db pg_dump -U jobmatch -d jobmatch -Fc > "$file.partial"
  mv "$file.partial" "$file"

  # Fail loudly on an empty dump rather than silently rotating good ones away.
  if [ ! -s "$file" ]; then
    echo "backup: FAILED — dump is empty ($file)" >&2
    rm -f "$file"
    exit 1
  fi
  # Custom-format archives start with the magic string "PGDMP". Checking it needs no
  # tooling on the host and catches the realistic failure modes: a zero/short file, or
  # an error message captured instead of a dump. (A full TOC parse via `pg_restore -l`
  # would need a seekable file inside the container — the weekly `verify` subcommand
  # does the real end-to-end restore test instead.)
  if [ "$(head -c 5 "$file")" != "PGDMP" ]; then
    echo "backup: FAILED — not a valid pg_dump archive ($file)" >&2
    head -c 200 "$file" >&2
    rm -f "$file"
    exit 1
  fi
  # If the host happens to have pg_restore, take the stronger structural check for free.
  if command -v pg_restore >/dev/null 2>&1 && ! pg_restore -l "$file" >/dev/null 2>&1; then
    echo "backup: FAILED — archive TOC does not parse ($file)" >&2
    rm -f "$file"
    exit 1
  fi

  echo "backup: wrote $file ($(du -h "$file" | cut -f1))"

  # A failed off-box copy must NOT abort the run before rotation — the local dump is
  # already valid, and skipping rotation would slowly fill the disk. Record the failure
  # and surface it in the exit code so systemd/n8n still alerts.
  local remote_failed=0
  if [ -n "$REMOTE" ]; then
    if rclone copy "$file" "$REMOTE/"; then
      echo "backup: copied off-box to $REMOTE"
    else
      echo "backup: ERROR — off-box copy to $REMOTE failed; local dump is intact" >&2
      remote_failed=1
    fi
  else
    echo "backup: WARNING — JOBDIGEST_BACKUP_REMOTE unset, backup is on the same VPS only" >&2
  fi

  # Rotate local copies only. Remote retention is the remote's business.
  find "$BACKUP_DIR" -name 'jobdigest-*.dump' -mtime "+$KEEP_DAYS" -delete
  find "$BACKUP_DIR" -name 'jobdigest-*.partial' -mtime +1 -delete

  return "$remote_failed"
}

# Restore is destructive: --clean drops existing objects first. Guarded by an explicit
# confirmation so a stray invocation can't wipe live subscriber data.
do_restore() {
  local file="${1:-}"
  [ -f "$file" ] || { echo "restore: no such file: $file" >&2; exit 2; }
  echo "About to RESTORE $file into the live jobdigest database."
  echo "This DROPS and replaces current data (subscribers, matches, digest_sends)."
  read -rp "Type 'restore' to proceed: " ok
  [ "$ok" = "restore" ] || { echo "aborted"; exit 1; }
  $COMPOSE exec -T db pg_restore -U jobmatch -d jobmatch --clean --if-exists < "$file"
  echo "restore: done from $file"
}

# Verify the newest dump actually loads, by restoring into a throwaway database.
# A backup you have never restored is a hypothesis, not a backup.
do_verify() {
  local file
  file=$(ls -1t "$BACKUP_DIR"/jobdigest-*.dump 2>/dev/null | head -1) || true
  [ -n "$file" ] || { echo "verify: no dumps found in $BACKUP_DIR" >&2; exit 1; }
  echo "verify: test-restoring $file into scratch db jobdigest_verify..."
  $COMPOSE exec -T db psql -U jobmatch -d postgres \
      -c "drop database if exists jobdigest_verify" -c "create database jobdigest_verify" >/dev/null
  $COMPOSE exec -T db pg_restore -U jobmatch -d jobdigest_verify < "$file" >/dev/null 2>&1 || true
  $COMPOSE exec -T db psql -U jobmatch -d jobdigest_verify -c \
      "select 'profiles' t, count(*) from profiles
       union all select 'postings', count(*) from postings
       union all select 'digest_sends', count(*) from digest_sends
       union all select 'matches', count(*) from matches;"
  $COMPOSE exec -T db psql -U jobmatch -d postgres \
      -c "drop database if exists jobdigest_verify" >/dev/null
  echo "verify: OK — counts above came out of the dump, not the live DB"
}

case "${1:-backup}" in
  backup)  do_backup ;;
  restore) do_restore "${2:-}" ;;
  verify)  do_verify ;;
  *) echo "usage: $0 [backup|restore <file>|verify]" >&2; exit 2 ;;
esac
