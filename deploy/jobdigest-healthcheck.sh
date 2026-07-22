#!/usr/bin/env bash
# JobDigest liveness watchdog — runs every ~5 min (jobdigest-healthcheck.timer).
#
# Checks the API actually answers and the containers are healthy, and alerts to Telegram/email
# (via jobdigest-alert.py) on the transition INTO failure and again on recovery. De-duped via
# a small counter file so a sustained outage pings once, not every 5 minutes, and a 2-strike
# threshold (~10 min) keeps a deploy restart or a transient blip from crying wolf.
#
# What it canNOT catch: a fully-dead box (nothing running on the box can report that) — that
# needs an external monitor. What it DOES catch: a crashed api/web/db container, the DB
# wedging, the app 500ing, Caddy misrouting — the common "up but broken" failures the
# per-job OnFailure alerts don't see.
set -uo pipefail

COMPOSE_DIR=/opt/jobdigest/deploy
STATE_FILE=/var/lib/jobdigest/health.state
ALERT=/opt/jobdigest/deploy/jobdigest-alert.py
THRESHOLD=2                       # consecutive failures before alerting (5 min each)
HOST="$(hostname)"
DOMAIN="${SITE_DOMAIN:-jobdigest.eu}"

mkdir -p "$(dirname "$STATE_FILE")"
cd "$COMPOSE_DIR" 2>/dev/null || exit 0

problems=""

# 1) API health via the local origin (bypasses Cloudflare; exercises Caddy -> api -> db).
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
  --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health" 2>/dev/null)
if [ "$code" != "200" ]; then
  problems="${problems}- /api/health returned HTTP ${code:-none} (expected 200)\n"
fi

# 2) Containers: api + db must be healthy; web must be running.
for svc in api db web; do
  cid=$(docker compose ps -q "$svc" 2>/dev/null)
  if [ -z "$cid" ]; then
    problems="${problems}- container '$svc' is not running\n"
    continue
  fi
  state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)
  if [ "$state" != "running" ]; then
    problems="${problems}- container '$svc' state=${state:-unknown}\n"
  elif [ "$health" = "unhealthy" ]; then
    problems="${problems}- container '$svc' is unhealthy\n"
  fi
done

prev=$(cat "$STATE_FILE" 2>/dev/null); prev=${prev//[^0-9]/}; prev=${prev:-0}

if [ -n "$problems" ]; then
  cur=$((prev + 1))
  echo "$cur" > "$STATE_FILE"
  # Alert exactly once, on the check that crosses the threshold.
  if [ "$cur" -eq "$THRESHOLD" ]; then
    stamp=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
    msg="JobDigest health check FAILED on ${HOST} at ${stamp} (2 checks in a row):\n\n${problems}\nOn the VPS:\n  cd ${COMPOSE_DIR} && sudo docker compose ps\n  sudo docker compose logs --tail=50"
    python3 "$ALERT" --subject "🔴 JobDigest DOWN on ${HOST}" --message "$(printf '%b' "$msg")"
  fi
else
  echo 0 > "$STATE_FILE"
  if [ "$prev" -ge "$THRESHOLD" ]; then
    stamp=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
    python3 "$ALERT" --subject "🟢 JobDigest recovered on ${HOST}" \
      --message "JobDigest health check is passing again on ${HOST} at ${stamp} — API 200, containers healthy."
  fi
fi
exit 0
