# Go-live provisioning checklist (JobDigest → jobdigest.eu)

The **account / infrastructure** steps only you can do (they need your logins + payment).
Everything in the repo (`deploy/`) is ready. After each account step, come back and I'll wire
the config + run/verify. Full technical detail is in `deploy/README.md`; this is the ordered
"who does what" list.

Decisions locked: domain **jobdigest.eu**, email provider **Resend**, host **Hetzner**.

## A. Register the domain  — YOU
- [ ] Register **jobdigest.eu** at an EU-capable registrar (Gandi, INWX, Namecheap — .eu
      needs an EU registrant; you qualify from CZ). ~€5–10/yr.

## B. Cloudflare  — YOU (I'll supply record values)
- [ ] Create a free Cloudflare account; **Add site** jobdigest.eu.
- [ ] At the registrar, point the domain's **nameservers** to the two Cloudflare gave you.
- [ ] Create a **Turnstile** widget (hostname jobdigest.eu) → copy **site key** + **secret**.
- [ ] (After the VPS exists) DNS: `A  @  <vps-ip>` proxied; SSL/TLS mode **Full (strict)**.

## C. Hetzner VPS  — YOU
- [ ] Hetzner Cloud account → new **CX22**, **Ubuntu 24.04**, add your **SSH key**, note the IP.
- [ ] On the box: install Docker + compose plugin, open ports 22/80/443 (ufw).
      `curl -fsSL https://get.docker.com | sh`

## D. Resend  — YOU (I'll supply record values / verify)
- [ ] Create Resend account → **Add Domain** jobdigest.eu.
- [ ] Add the DKIM/SPF (+ return-path) records Resend shows into Cloudflare DNS; verify.
- [ ] Add DMARC: `TXT _dmarc  "v=DMARC1; p=quarantine; rua=mailto:dmarc@jobdigest.eu"`.
- [ ] Create an **API key** → this is `RESEND_API_KEY`.

## E. Ship the code to the VPS  — ME + YOU
- [ ] Commit the new work + push to GitHub (needs your OK — it publishes the code).
- [ ] On the VPS: `git clone <repo> /opt/jobdigest && cd /opt/jobdigest/deploy`
- [ ] `cp .env.example .env` and fill: SITE_DOMAIN, POSTGRES_PASSWORD, ACME_EMAIL,
      MAIL_FROM, RESEND_API_KEY, TURNSTILE_SECRET, NEXT_PUBLIC_TURNSTILE_SITE_KEY.
- [ ] `docker compose up -d --build` → check `curl https://jobdigest.eu/api/health`.

## F. Make it recurring + safe  — ME (guiding) 
- [ ] Load postings (ingest) into the DB.
- [ ] Install the systemd timer (07:00 daily digest) — `deploy/README.md §6`.
- [ ] Deliverability test at mail-tester.com; backups + uptime ping — `README.md §7`.

---
### What's blocking, in order
1. Register jobdigest.eu (A) → 2. Cloudflare zone + Turnstile keys (B) →
3. Hetzner VPS IP (C) → 4. Resend domain verified + API key (D) → 5. deploy (E).

Tell me when you've done a step (paste the DNS records Resend/Cloudflare ask for, or the
VPS IP) and I'll generate the exact records/commands and verify each one with you.
