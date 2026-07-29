# Origin TLS — the Cloudflare Origin Certificate

**Done 2026-07-29.** The edge no longer uses Let's Encrypt. This file is now the record of
what was installed and the runbook for the only two things that can still need doing:
rotating the key, or rebuilding the box from scratch.

## Why it was changed

Caddy (`web`) auto-provisioned a Let's Encrypt cert via TLS-ALPN on :443. That worked for
*initial* issuance (grey-cloud the record for 5 min, `deploy/README.md` §3) but could never
**renew**: once the record is orange-clouded, Cloudflare terminates TLS at its edge and
intercepts the ACME challenge, so Caddy's ~60-day renewal silently fails. The public keeps
seeing Cloudflare's own edge cert, so the first symptom would have been Caddy logging renewal
errors and eventually serving an expired origin cert — which under **Full (strict)** is CF
error 526 for everyone. The LE cert in place expired 16 Oct 2026; renewals would have started
failing around mid-September.

A Cloudflare Origin Certificate is issued by CF, trusted by CF at the edge, and valid for
**15 years**. There is nothing left to renew.

## What is installed

| | |
|---|---|
| Issuer | CloudFlare Origin SSL Certificate Authority |
| Hostnames | `jobdigest.eu`, `*.jobdigest.eu` |
| Key | ECDSA P-256, **generated on the VPS** |
| Valid | 2026-07-29 → **2041-07-25** |
| Files | `/opt/jobdigest/deploy/origin/{cert.pem,key.pem,jobdigest.csr,tls.caddy}` |
| SSL/TLS mode | Full (strict), automatic mode disabled |

`origin/` is `0700`, `key.pem` is `0600`, owned by `deploy` (uid 1000). The directory is
gitignored (`deploy/origin/` **and** `*.pem`) and must never be written into the matcher's
Drive folder — same rule as the encrypted backups (CLAUDE.md security rule 5).

## Two decisions worth keeping

**The private key was never shown to anyone.** Cloudflare's default flow generates the key in
the browser and displays it once, which would have put a credential into a session transcript
(security rule 2). Instead the key and a CSR were generated on the box with `openssl`, only the
**CSR** was pasted into the dashboard ("Use my private key and CSR"), and Cloudflare returned
only the certificate. Cloudflare never held the key and it never left the VPS. Verified by
comparing public keys — `openssl x509 -pubkey` and `openssl pkey -pubout` must give the same
md5, and did (`13368aff…`).

**The `tls` directive is not a box-local edit.** `web/Caddyfile` is **baked into the image**
(`COPY Caddyfile /etc/caddy/Caddyfile`), so an uncommitted edit on the VPS would be reverted by
the next `scp web/Caddyfile` and would take the site down at the following rebuild — silently,
because nothing would fail until then. Instead the committed Caddyfile carries:

```
import /etc/caddy/origin/*.caddy
```

and the box-specific line lives in `deploy/origin/tls.caddy`, which the compose file mounts at
`/etc/caddy/origin:ro`. A glob matching nothing is a **no-op**, not an error — verified with
`caddy validate` (`Valid configuration`, exit 0) — so local dev with no `origin/` directory
still gets Caddy's automatic/internal certs. One Caddyfile, everywhere, in git.

## Rotating the key (the only recurring risk, and it isn't scheduled)

Only needed if `key.pem` is believed compromised. Same shape as the original install:

```bash
cd /opt/jobdigest/deploy/origin
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out key.new.pem
chmod 600 key.new.pem
openssl req -new -sha256 -key key.new.pem -subj "/CN=jobdigest.eu" \
  -addext "subjectAltName=DNS:jobdigest.eu,DNS:*.jobdigest.eu" -out jobdigest.new.csr
cat jobdigest.new.csr        # a CSR is public — safe to paste into the dashboard
```

CF → SSL/TLS → **Origin Server** → **Create Certificate** → *Use my private key and CSR* →
paste → hostnames `jobdigest.eu` + `*.jobdigest.eu` → 15 years → **Create**. Save the returned
PEM over `cert.pem`, move `key.new.pem` to `key.pem`, then:

```bash
# the pair MUST match, or Caddy serves a cert it has no key for
openssl x509 -in cert.pem -noout -pubkey | openssl md5
openssl pkey -in key.pem -pubout | openssl md5
cd /opt/jobdigest/deploy && sudo docker compose restart web
```

Revoke the old certificate in the dashboard afterwards. No rebuild is needed — the cert is
mounted, not baked.

## Rebuilding the box from scratch

`origin/` is not in git and is **not** in the database backups, so a fresh VPS has no cert.
Either restore the directory from wherever you keep it, or just issue a new one with the
rotation steps above — it takes about five minutes and costs nothing. Until `origin/` exists
the glob import is a no-op and Caddy will try ACME, which fails behind the proxy; the site is
down at that point regardless, so this is not an extra failure mode.

## Verify (any time)

```bash
# From the box, read the cert the origin actually serves:
echo | openssl s_client -connect 127.0.0.1:443 -servername jobdigest.eu 2>/dev/null \
  | openssl x509 -noout -issuer -dates
# Expect: CloudFlare Origin SSL Certificate Authority, notAfter 2041.

# Caddy should say it is NOT managing certs:
sudo docker compose logs --tail=40 web | grep -i "automatic certificate management"
# Expect: "skipping automatic certificate management because one or more matching
# certificates are already loaded". Any ACME/renewal line means the import did not apply.
```

Then load https://jobdigest.eu from a normal client — expect 200 and no CF 526. The padlock is
still Cloudflare's public edge cert; nothing user-visible changed.

Two things that look like faults and are not: `curl` **from the VPS itself** can return 403
(Cloudflare refusing the origin's own IP — check from outside), and Caddy logs
`no OCSP stapling … no URL to issuing certificate` for every origin cert, because CF origin
certs carry no OCSP responder.

## Rollback

Remove `origin/tls.caddy` and `docker compose restart web` — Caddy falls back to ACME. This is
only useful if ACME issuance still works, which behind the proxy it does not. Prefer fixing
forward: re-issue the origin cert.
