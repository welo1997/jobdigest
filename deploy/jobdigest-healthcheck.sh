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
ORIGIN_CERT=/opt/jobdigest/deploy/origin/cert.pem
THRESHOLD=2                       # consecutive failures before alerting (5 min each)
REALERT_EVERY=12                  # ...then re-alert roughly hourly while it stays broken
HOST="$(hostname)"
DOMAIN="${SITE_DOMAIN:-jobdigest.eu}"

mkdir -p "$(dirname "$STATE_FILE")"
cd "$COMPOSE_DIR" 2>/dev/null || exit 0

problems=""

# 1) API health via the local origin (bypasses Cloudflare; exercises Caddy -> api -> db).
#
# In production Caddy serves a Cloudflare Origin Certificate, and its issuer is deliberately
# NOT a public root — so plain curl cannot build a chain and returns HTTP 000 on a perfectly
# healthy box. That is not hypothetical: it silently pinned this check red for 3.5 h on
# 2026-07-29. Pin to the origin cert instead of dropping verification, because verification
# still carries signal here: Cloudflare is Full (strict), so if Caddy ever served something
# else (an ACME fallback, the wrong file) real users would get a 526 and this check should be
# the thing that says so. Where the file is absent — local dev, any ACME-served box — fall
# back to the system store, the same "missing means no-op" shape as web/Caddyfile's import.
ca_opt=()
[ -r "$ORIGIN_CERT" ] && ca_opt=(--cacert "$ORIGIN_CERT")
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${ca_opt[@]}" \
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

# 3) Disk. This check watched the box die twice without saying a word: on 2026-09-06 and
# again on 2026-09-20 the root filesystem hit 100%, Postgres PANICked on its end-of-recovery
# checkpoint ("could not write to file ... No space left on device") and crash-looped for
# ~24 h. Sections 1 and 2 did eventually notice — but only once the DB was already down and
# subscribers had missed a digest. Disk exhaustion is the rare failure that is completely
# predictable days ahead, so the useful alert is the one that fires while there is still room
# to act, not the one that confirms the outage.
#
# Threshold is on FREE BYTES as well as percent, deliberately. Percent alone is the wrong
# unit for the thing that actually breaks: Postgres needs only a few MB for a checkpoint, but
# a nightly pg_dump needs one dump's worth (~850 MB and growing ~10 MB/day) and the off-box
# upload briefly needs another for the ciphertext. A 38 GB disk at 90% still has 3.8 GB and is
# fine; a 200 GB disk at 90% has 20 GB and is also fine — but either at 1 GB free is not.
DISK_PCT_MAX=85                   # warn while there is still ~5 GB of headroom on 38 GB
DISK_FREE_MIN_MB=3072             # ...or whenever a backup + its ciphertext no longer fit
disk_pct=$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc '0-9')
disk_free_mb=$(df --block-size=1M --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$disk_pct" ] && [ "$disk_pct" -ge "$DISK_PCT_MAX" ]; then
  problems="${problems}- disk / is ${disk_pct}% full (${disk_free_mb:-?} MB free); largest consumers:\n    $(du -xh --max-depth=2 /var 2>/dev/null | sort -rh | head -3 | tr '\n' ' ')\n"
elif [ -n "$disk_free_mb" ] && [ "$disk_free_mb" -lt "$DISK_FREE_MIN_MB" ]; then
  problems="${problems}- disk / has only ${disk_free_mb} MB free (a nightly dump needs ~850 MB)\n"
fi

prev=$(cat "$STATE_FILE" 2>/dev/null); prev=${prev//[^0-9]/}; prev=${prev:-0}

if [ -n "$problems" ]; then
  cur=$((prev + 1))
  echo "$cur" > "$STATE_FILE"
  # Alert on the check that crosses the threshold, then roughly hourly for as long as it
  # stays broken. Alerting *exactly once* was the original design and it failed in the way
  # that matters: on 2026-07-29 one mail went out at strike 2 and the next 36 consecutive
  # failures were silent, so the only evidence of a dead monitor was a counter file nobody
  # reads. A repeat is the cheap half of the fix — an outage that is still an outage should
  # keep saying so.
  if [ "$cur" -eq "$THRESHOLD" ] ||
     { [ "$cur" -gt "$THRESHOLD" ] && [ $(( (cur - THRESHOLD) % REALERT_EVERY )) -eq 0 ]; }; then
    stamp=$(date -u +'%Y-%m-%d %H:%M:%S UTC')
    msg="JobDigest health check FAILED on ${HOST} at ${stamp} (${cur} checks in a row, ~$((cur * 5)) min):\n\n${problems}\nOn the VPS:\n  cd ${COMPOSE_DIR} && sudo docker compose ps\n  sudo docker compose logs --tail=50"
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
