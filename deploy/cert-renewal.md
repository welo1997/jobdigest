# Cert renewal — switch Caddy to a Cloudflare Origin Certificate

**Do this before ~mid-September 2026.** Deadline is not cosmetic: see "Why" below.

The edge (`web` service = Caddy) currently auto-provisions a Let's Encrypt cert via
TLS-ALPN on :443. That worked for *initial* issuance (grey-cloud the record for 5 min while
Caddy gets its cert, then re-proxy — `deploy/README.md` §3). It will **not renew**: once the
record is orange-clouded, Cloudflare terminates TLS at its edge and intercepts the ACME
challenge, so Caddy's ~60-day renewal silently fails. The public still sees Cloudflare's own
edge cert, so the first symptom is Caddy logging renewal errors and eventually serving an
expired origin cert to Cloudflare — which, under **Full (strict)**, makes the site return
CF error 526 for everyone.

A **Cloudflare Origin Certificate** fixes this permanently: CF issues it, trusts it at the
edge, and it is valid for **15 years** with nothing to renew.

## Steps

### 1. Cloudflare dashboard — create the Origin Certificate (YOU)
CF → SSL/TLS → **Origin Server** → **Create Certificate**.
- Key type: ECDSA (smaller/faster) or RSA — either is fine.
- Hostnames: `jobdigest.eu`, `*.jobdigest.eu`.
- Validity: 15 years.
- CF shows the **certificate** (PEM) and **private key** (PEM) **once**. Keep them out of
  chat, commits, and issues — the private key is a credential (CLAUDE.md security rule 2).

Confirm SSL/TLS mode is **Full (strict)** (it already should be — `deploy/PROVISION.md` §B).

### 2. On the VPS — place the files (YOU, on the box; never commit)
```bash
cd /opt/jobdigest/deploy
mkdir -p origin && chmod 700 origin
# Paste each PEM directly into these files on the box (do not scp through a transcript):
#   origin/cert.pem   <- the Origin Certificate
#   origin/key.pem    <- the private key
chmod 600 origin/key.pem
chown -R deploy:deploy origin        # deploy = uid 1000, same as the container's app user
```
`origin/` must be gitignored and must never be written into the matcher's Drive folder —
same rule as the encrypted backups (CLAUDE.md security rule 5). Add `deploy/origin/` to
`.gitignore` if it is not already covered.

### 3. Mount the cert into the Caddy (`web`) container
`deploy/docker-compose.yml`, `web` service → `volumes:` — add:
```yaml
      - ./origin:/etc/caddy/origin:ro
```

### 4. Point Caddy at it
`web/Caddyfile`, inside the `{$SITE_DOMAIN:localhost} { … }` site block, add as the first
line of the block:
```
	tls /etc/caddy/origin/cert.pem /etc/caddy/origin/key.pem
```
This overrides ACME entirely — Caddy stops trying to renew and simply serves the origin
cert. Leave the rest of the block unchanged. (Local dev is unaffected only if you make this
edit on the box / at deploy time: with `SITE_DOMAIN=localhost` and no cert files present,
this `tls` line would fail, so do **not** commit it to `web/Caddyfile` — apply it as part of
this switch. `/opt/jobdigest` is scp'd, not a git checkout, so a box-local edit is expected.)

### 5. Rebuild + restart the edge
```bash
cd /opt/jobdigest/deploy
sudo docker compose build web
sudo docker compose up -d web
sudo docker compose logs --tail=30 web        # expect no ACME/renewal lines
```

### 6. Verify
```bash
# From the box, hit the origin directly (bypass CF) and read the cert issuer:
curl -sv --resolve jobdigest.eu:443:127.0.0.1 https://jobdigest.eu -o /dev/null 2>&1 \
  | grep -Ei 'issuer|subject'
# Issuer should be "CloudFlare Origin Certificate Authority", not Let's Encrypt.
```
Then load https://jobdigest.eu in a browser — the padlock is still Cloudflare's public edge
cert (unchanged); nothing user-visible should change. No CF 526.

## Rollback
Remove the `tls` line and the `./origin` mount, rebuild `web` — Caddy falls back to ACME.
Only useful if issuance still works (i.e. before the LE cert has fully expired), so prefer
fixing forward.

## Afterwards
Tick the cert item off CLAUDE.md's "Open items". With a 15-year cert there is no recurring
renewal to track.
