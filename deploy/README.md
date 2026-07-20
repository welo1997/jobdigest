# Deploying JobDigest (Phase C)

Self-contained Docker Compose stack: **Caddy** (static frontend + `/api` reverse proxy) →
**FastAPI** backend → **Postgres**, with an optional **n8n** for automation. Designed for a
small Hetzner VPS behind Cloudflare.

```
Internet ──▶ Cloudflare (proxied, TLS, WAF, Turnstile)
          └─▶ VPS :443  Caddy ──┬─ /            static Next.js export  (web image)
                                └─ /api/*  ──▶   api:8000  FastAPI  ──▶  db:5432  Postgres
```

Same-origin `/api` means **no CORS in production**. Email links resolve to:
`/api/confirm`, `/api/unsubscribe` (backend pages) and `/preferences` (frontend page).

---

## 1. Prerequisites
- A VPS (Hetzner CX22 or similar, Ubuntu 24.04), Docker + compose plugin installed.
- A domain, added to Cloudflare (nameservers pointed at CF).
- A transactional email provider account (Resend recommended) **or** SMTP creds.

## 2. Configure
```bash
git clone <repo> /opt/jobdigest && cd /opt/jobdigest/deploy
cp .env.example .env && $EDITOR .env        # set SITE_DOMAIN, POSTGRES_PASSWORD, mail, Turnstile
```

## 3. DNS + Cloudflare (C2)
1. **DNS**: `A @ <vps-ip>` (proxied/orange cloud). Optionally `CNAME www @`.
2. **SSL/TLS mode**: Full (strict). Create an **Origin Certificate** (CF → SSL/TLS → Origin
   Server), and either install it in Caddy or, simplest, keep Caddy's own Let's Encrypt cert
   and set CF to **Full (strict)** — Caddy auto-issues via TLS-ALPN on :443. If issuance
   fails behind the proxy, grey-cloud the record for 5 min while Caddy gets its cert, then
   re-enable the proxy.
3. **Turnstile**: create a widget → put the **site key** in `NEXT_PUBLIC_TURNSTILE_SITE_KEY`
   and the **secret** in `TURNSTILE_SECRET`. (Until both are set, bot-check is a no-op.)
4. **WAF / rate limiting**: add a rate-limit rule on `/api/subscribe` and `/api/cv/parse`
   (e.g. 10 req/min per IP). Enable Bot Fight Mode.
5. **Origin lock**: allow inbound :80/:443 only from Cloudflare IP ranges (ufw or CF Tunnel).

## 4. Email domain auth (C3)
Set these DNS records (values from your provider, e.g. Resend dashboard):
- **SPF**: `TXT @  "v=spf1 include:<provider> ~all"`
- **DKIM**: the provider's `CNAME`/`TXT` selector records.
- **DMARC**: `TXT _dmarc  "v=DMARC1; p=quarantine; rua=mailto:dmarc@<domain>"`
- Verify the sending domain in the provider, set `MAIL_FROM=JobDigest <hello@<domain>>`.
- Test deliverability: send yourself a digest and check the score at https://mail-tester.com.

## 5. Launch
```bash
docker compose up -d --build
docker compose ps                 # db healthy, api up, web up
curl -s https://$SITE_DOMAIN/api/health
```
Schema is auto-created on first DB boot from `service/db/schema.sql`. To load postings, run
your ingest (`docker compose run --rm pipeline python -m service.pipeline --ingest --cz`).

## 6. Daily pipeline (C4)
Send digests every morning at 07:00. Two options — pick one:

**systemd timer (fallback, always works):**
```bash
sudo cp jobdigest-pipeline.service jobdigest-pipeline.timer /etc/systemd/system/
sudo sed -i "s#/opt/jobdigest/deploy#$(pwd)#" /etc/systemd/system/jobdigest-pipeline.service
sudo systemctl daemon-reload && sudo systemctl enable --now jobdigest-pipeline.timer
systemctl list-timers jobdigest-pipeline.timer
```

**n8n (schedule + Telegram alerts):** `docker compose --profile automation up -d`, then add a
Schedule trigger → Execute Command `docker compose run --rm pipeline` → Telegram node on error.

Manual run any time: `docker compose run --rm pipeline`.

## 7. Backups + monitoring (C5)

### DB backup (required)
The `pgdata` volume is the only copy of every subscriber and of `digest_sends` — the
never-repeat-a-job ledger, which **cannot be reconstructed**. Lose it and every subscriber
gets re-sent jobs they've already seen.

```bash
sudo install -m 755 deploy/jobdigest-backup.sh /usr/local/bin/jobdigest-backup.sh
sudo cp deploy/jobdigest-backup.{service,timer} /etc/systemd/system/
# Edit the .service: uncomment JOBDIGEST_BACKUP_REMOTE and point it at your rclone remote.
sudo systemctl daemon-reload && sudo systemctl enable --now jobdigest-backup.timer
```

Nightly at 03:30 (clear of the 07:00 digest). `pg_dump -Fc` → `/var/backups/jobdigest/`,
30-day rotation, ~13 MB per dump at current size. Each dump is checked for the `PGDMP`
magic header before it's kept, so a failed dump can never rotate away good ones.

```bash
jobdigest-backup.sh backup            # run now
jobdigest-backup.sh verify            # test-restore newest dump into a scratch DB
jobdigest-backup.sh restore <file>    # DESTRUCTIVE — prompts for confirmation
```

**Set `JOBDIGEST_BACKUP_REMOTE`.** A backup sitting on the same VPS does not survive losing
the VPS. rclone is already installed for the matcher routine. Run `verify` monthly — an
untested backup is a hypothesis.

### Retention
The daily pipeline prunes automatically (`service/pipeline.py`); no setup needed:
- Descriptions of postings inactive **>90 days** are blanked (rows kept for analytics).
  Descriptions are ~81% of table size — ~7.2 KB of the ~9.1 KB per posting.
- `matches` rows for postings inactive **>180 days** are deleted, *except* `saved`/`applied`
  (the user's own history). `digest_sends` is never pruned.

Tune with `RETENTION_DESC_DAYS` / `RETENTION_MATCH_DAYS` in `.env`; skip with
`docker compose run --rm pipeline python -m service.pipeline --no-prune`.

### Monitoring
- **Uptime**: external ping on `https://$SITE_DOMAIN/api/health` (UptimeRobot / n8n).
- **Errors**: pipeline failures → Telegram via n8n; `docker compose logs -f api`.
- **Backup failures**: `systemctl status jobdigest-backup.service`; the unit exits non-zero
  on a bad dump, so an n8n/systemd `OnFailure` hook will catch it.

## 8. Updating
```bash
git pull && docker compose up -d --build
```
Schema changes: apply new `service/db/migration_00X_*.sql` with
`docker compose exec -T db psql -U jobmatch -d jobmatch < ../service/db/migration_00X_*.sql`.

---

### Still to decide before go-live
- **Domain** (`SITE_DOMAIN`) and **email provider** (Resend vs SMTP) — the only open items;
  everything else is wired and parameterised via `.env`.
