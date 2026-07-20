# JobDigest — Complete Build Plan (v1 MVP)

> Working name: **JobDigest**. A daily, personalised job digest by email.
> Decided: self-hosted VPS · cloud cron · heuristic ranking + Claude (subscription) polish · Cloudflare in front.
> Tech roles / CZ+EU first; architecture built to expand to **any role** and **US + Europe**.

---

## 1. Product in one sentence

A user fills a short profile (what they do, what they want, their email). Once a day we
email them a hand-picked shortlist of jobs that fit, each with a link and a one-line
"why this fits you." No dashboard to check, no searching — the matches come to them.

### v1 scope (what we ship)
- Public landing page + profile form.
- Double opt-in email confirmation.
- Daily digest email (heuristic-ranked, Claude-polished summary text).
- Preferences page (edit profile) + one-click unsubscribe.
- Tech roles, CZ + EU-remote to start.

### Explicitly NOT in v1 (later)
- Login / accounts / dashboard / live search UI.
- Payments (freemium/Stripe).
- Non-tech verticals + US sources (data model supports it; we turn it on later).
- Mobile app.

---

## 2. UX / UI design

### 2.1 Pages (the whole surface area of v1)

| Page | Purpose | Notes |
|---|---|---|
| `/` Landing + form | Explain value, capture profile + email | The entire funnel. One screen. |
| `/check-inbox` | "We sent you a confirmation link" | After form submit. |
| `/confirmed` | Opt-in success | Reached via email link `?token=`. |
| `/preferences?token=` | Edit profile, pause, change frequency | Token-based, no login. |
| `/unsubscribe?token=` | One-click off + reason (optional) | Required by bulk-sender rules. |
| `/privacy`, `/terms` | Legal (GDPR) | Static. |
| **The email itself** | The actual product surface | Design matters more than the site. |

### 2.2 Landing page flow

```
┌─────────────────────────────────────────────┐
│  JobDigest                                   │
│  The right jobs, in your inbox, every morning│
│  ─────────────────────────────────────────  │
│  Tell us what you're after → we do the       │
│  searching → one email a day, only good fits.│
│                                              │
│  [ What roles?      e.g. Data Engineer     ] │
│  [ Skills/keywords  dbt, Python, SQL       ] │
│  [ Seniority ▾ Junior ] [ Work ▾ Full+Free ]│
│  [ Where? ▾ CZ · EU-remote · Worldwide     ] │
│  [ Open to part-time?  ☐ ]                   │
│  [ Your email        you@example.com       ] │
│  [ ☐ I agree to the privacy policy ]         │
│         [  Send me matching jobs  ]          │
│         (invisible Cloudflare Turnstile)     │
│                                              │
│  ▸ Sample digest (see what you'd get)        │
│  ▸ How it works · Privacy · No spam, 1/day   │
└─────────────────────────────────────────────┘
```

Design principles:
- **One screen, no scroll to convert.** Progressive fields; sensible defaults preselected.
- **Show a sample digest** (static example) so people know exactly what they're signing up for → higher-quality signups, lower unsubscribe.
- **Frictionless bot protection:** Cloudflare Turnstile invisible widget, not a CAPTCHA.
- Mobile-first, responsive, dark/light aware.

### 2.3 The digest email (the real product)

```
Subject: 5 new Data Engineer roles for you — 18 Jul

Good morning. 5 fresh matches today (from 214 new postings):

★ 9/10  Analytics Engineer II — Affirm            [EU · remote · perm]
        Matches dbt, Snowflake, Python, SQL. Fintech.
        → View & apply

★ 8/10  Data Engineer — Bitpanda                  [EU · remote · perm]
        Snowflake + Python core. Trading/crypto.
        → View & apply
  ...
─────────────────────────────────────────────
Not right? [Refine preferences] · [Pause 2 weeks] · [Unsubscribe]
```

Email rules:
- **Ranked, capped (top 5–7).** Curation is the value — never dump 40 rows.
- Every row: score, tags, one-line reason, **direct apply link**.
- Never repeat a job already sent to that person (dedup via `digest_sends`).
- If < N good matches that day, send fewer or skip — quality over cadence.
- Plain, robust HTML (email clients are hostile to CSS) + plaintext part.
- Footer: preferences / pause / **one-click unsubscribe** (List-Unsubscribe header).

### 2.4 Design system
- Keep it minimal: one accent color, system font stack, generous whitespace.
- Tokens shared between site and email where possible (email inlined).
- Component list is tiny: form fields, button, job-card, tag chip, alert banner.

---

## 3. System architecture

```
                          ┌──────────────── Cloudflare ────────────────┐
   user browser ─────────►│ DNS (proxied) · WAF · Turnstile · rate-limit │
                          │ TLS · caching · origin-IP hidden             │
                          └───────────────────┬─────────────────────────┘
                                              │ (only Cloudflare IPs allowed in)
                     ┌────────────────────────▼────────────────────────┐
                     │                VPS (Docker Compose)              │
                     │                                                  │
                     │  caddy  ── reverse proxy / origin TLS            │
                     │    ├─► web        (Next.js: landing/prefs pages) │
                     │    └─► api        (FastAPI: /profiles /confirm …)│
                     │                                                  │
                     │  postgres         (profiles, postings, sends)    │
                     │  n8n              (orchestrates daily pipeline)   │
                     │                                                  │
                     │  cron/n8n (daily):                               │
                     │    ingest.py → match → digest.py → email send    │
                     │           └─ claude -p (subscription) for polish │
                     └───────────────────────┬──────────────────────────┘
                                              │ SMTP/API
                                     ┌────────▼────────┐
                                     │ Email provider  │  (Resend → SES at scale)
                                     │ SPF/DKIM/DMARC  │
                                     └─────────────────┘
```

Single VPS runs everything in Docker Compose. Cloudflare is the only thing the public
touches. Cron/n8n on the box drives the once-a-day pipeline.

---

## 4. Tech stack (concrete)

| Layer | Choice | Why |
|---|---|---|
| VPS | Hetzner CX22 (~€4/mo, 2vCPU/4GB) or DO $6 | Cheap, plenty for v1. |
| Orchestration | Docker Compose | One `up`, reproducible, matches your dev setup. |
| Reverse proxy | Caddy | Auto-HTTPS, dead-simple config, routes web+api. |
| DB | Postgres 16 (self-hosted) | **Reuses `service/db/schema.sql` unchanged.** |
| Backend/API | FastAPI (existing `service/`) | Already built; Python reuses ingestion + classifiers. |
| Ingestion | existing `ingestion/` + `search_jobs.py` | Single source of truth for how a posting is parsed. |
| Scheduler | n8n (you already run it) + systemd timer fallback | Cron + email nodes + Execute Command for `claude -p`. |
| LLM (polish) | Claude Code headless `claude -p` on Max subscription | Flat cost. Behind a flag; digest works without it. |
| Frontend | Next.js (static export where possible) | Future-proof for later dashboard; SSG landing = fast + cacheable. |
| Email send | Resend (free tier) → Amazon SES at scale | Deliverability + generous free tier. |
| Bot protection | Cloudflare Turnstile | Invisible, no user friction. |
| CDN/WAF/DNS | Cloudflare | Their explicit requirement. |

> Alt if Next.js feels heavy for a 5-page site: **Astro** (ships near-zero JS, perfect for
> mostly-static + one form). Recommend Next.js only because of the planned dashboard.

---

## 5. What we store (database)

Extends the existing schema. **Store the minimum** — email is PII, EU users ⇒ GDPR.

### 5.1 Tables

**`postings`** (exists) — one enriched row per job. Keep `last_seen_at` / `is_active` for
freshness. No user data. Retention: deactivate after 7 days unseen, hard-delete after 30.

**`profiles`** (pivot from user_id → email-first, token-based, no login in v1):
```
id              uuid pk
email           text (lowercased, unique-ish per active)
label           text
stack           text[]          -- skills/keywords
role_categories text[]
seniorities     text[]
regions         text[]
work_types      text[]
part_time_only  bool
eligible_only   bool
sectors         text[]
frequency       text            -- 'daily' | 'weekly' | 'paused'
status          text            -- 'pending' | 'active' | 'unsubscribed' | 'bounced'
confirm_token   text            -- double opt-in
manage_token    text            -- prefs + unsubscribe links (unguessable)
consent_at      timestamptz     -- GDPR: when + that they agreed
created_at, confirmed_at, last_digest_at, unsubscribed_at
```

**`digest_sends`** (NEW) — what we sent, so we never repeat a job + for compliance:
```
profile_id, posting_id, sent_at, score      -- pk (profile_id, posting_id)
```

**`suppression`** (NEW) — hard bounces / complaints / unsubscribes, never email again:
```
email, reason ('unsub'|'bounce'|'complaint'), created_at   -- pk email
```

**`matches`** (exists) — keep for cached scores if we later add on-demand; not required
for the pure batch flow (digest computes + records in `digest_sends`).

### 5.2 Data/privacy rules
- **Double opt-in**: no email sent until they click confirm. Blocks abuse + is clean consent.
- **Tokens** (`confirm_token`, `manage_token`): random 32-byte, so prefs/unsub need no login.
- **Right to erasure**: unsubscribe = soft-stop; a delete link / request = hard-delete row.
- **Minimal PII**: email only. No names, no resumes stored in v1 (paste-to-parse is
  processed in memory, not persisted).
- Privacy policy + terms pages; consent checkbox with timestamp.

---

## 6. The daily pipeline

Runs once/day via n8n cron (systemd timer as fallback), all on the VPS:

```
1. ingest      python -m service.ingest [--cz]      # sources → postings (upsert, dedup)
2. deactivate  drop postings unseen > 7d
3. for each active, confirmed profile:
     a. SQL prefilter (region/seniority/work_type/eligible)  → ~200 candidates  [FREE]
     b. heuristic_score all → rank                            → top 5–7          [FREE]
     c. drop any posting already in digest_sends for them     (no repeats)
     d. Claude polish (optional): claude -p summarises the shortlist nicely      [SUBSCRIPTION]
     e. render email (HTML + text)
     f. send via provider; on success write digest_sends rows + last_digest_at
4. handle bounces/complaints → suppression (webhook or daily poll)
```

**Cost surface = zero on-demand.** Nothing the public does triggers inference. The only
LLM use is step 3d, once per profile per day, on your schedule, on the subscription.

New file to build: **`service/digest.py`** — takes a profile, returns ranked shortlist +
rendered email; runnable standalone. **`service/mailer.py`** — send + templates.
**`service/pipeline.py`** — the loop above (what cron calls).

---

## 7. Scoring / curation engine

- **Heuristic (free, exists in `service/scoring.py`)** does the actual ranking + reason.
  This is authoritative — the product is fully functional on it alone.
- **Claude via subscription (`claude -p`)** only *rewrites the shortlist copy* to read
  warmly and dedupe reasoning — it does **not** decide ranking, so if it's rate-limited or
  down, we send the heuristic version. Behind `USE_CLAUDE_POLISH` flag.
- Setup note: running `claude -p` headless on a VPS needs a one-time authenticated login
  (subscription token) on the box. Fallback = heuristic-only, always safe.
- Migration path: when paying users arrive, swap step 3d to Anthropic API (batch, cents/day,
  commercial-safe) — the interface stays identical.

---

## 8. Cloudflare protection (their requirement)

1. **Proxied DNS (orange cloud)** → origin VPS IP hidden from the world.
2. **Lock the origin**: VPS firewall (ufw) allows :443 **only from Cloudflare IP ranges**
   (+ Authenticated Origin Pulls) so attackers can't bypass CF by hitting the raw IP.
3. **SSL/TLS = Full (strict)**; Caddy holds a real origin cert; CF holds the edge cert.
4. **Turnstile** on the profile form → stops bot signups without CAPTCHA friction.
5. **WAF / rate-limiting rules**:
   - `POST /profiles` and `/confirm`: e.g. 5/min/IP, challenge on burst.
   - Managed WAF ruleset on; block known-bad ASNs if abused.
   - "Under Attack" mode available as a switch.
6. **Caching**: static landing/assets cached at edge; API paths bypass cache.
7. **DNS email records** live here too (SPF/DKIM/DMARC — see §9).
8. Bonus: Cloudflare Tunnel is an option instead of opening ports at all (origin never
   exposed). Consider for extra hardening.

---

## 9. Email deliverability (make-or-break for a daily-email product)

- **Custom domain** (e.g. `jobdigest.eu`), dedicated sending subdomain (`mail.` / `send.`).
- **SPF + DKIM + DMARC** DNS records (in Cloudflare) — without these, Gmail dumps you.
- **Double opt-in** (already in flow) — protects sender reputation + is consent.
- **One-click unsubscribe** + `List-Unsubscribe` / `List-Unsubscribe-Post` headers —
  **required** by Gmail/Yahoo bulk-sender rules; skipping = spam folder.
- **Bounce/complaint handling** → suppression list; keep complaint rate < 0.3%.
- Warm up volume gradually; monitor with provider analytics / Google Postmaster Tools.
- Plaintext alternative part + clean, table-based HTML.

---

## 10. Security & privacy checklist
- HTTPS everywhere (CF edge + Caddy origin).
- Secrets in `.env` on the VPS, never in git (`.gitignore` already covers `.env`).
- Tokens are unguessable (secrets.token_urlsafe(32)); prefs/unsub authorised by token only.
- GDPR: consent timestamp, privacy policy, right to access/erase, minimal data, EU-region VPS.
- Rate-limit + Turnstile on all write endpoints.
- Regular Postgres backups (nightly `pg_dump` to object storage / off-box).
- Docker images pinned; unattended-upgrades on the host.

---

## 11. Cost model (v1)

| Item | Cost |
|---|---|
| VPS (Hetzner CX22) | ~€4–5 / mo |
| Domain | ~€10 / yr |
| Cloudflare | Free tier (Turnstile, WAF basics, DNS incl.) |
| Email (Resend free) | €0 up to ~3k/mo; SES ~€0.10/1k after |
| LLM | €0 marginal (your Max subscription) |
| **Total** | **~€5 / mo** until real scale |

Cost is bounded by infra, not traffic. No path where user activity spikes a metered bill.

---

## 12. Build plan (phased, with milestones)

**Phase A — Backend batch (no UI needed to prove value)**
- A1. Pivot `profiles` schema (email/tokens/status/frequency) + add `digest_sends`,
      `suppression`. Migration SQL.
- A2. `service/digest.py` — profile → ranked shortlist → rendered HTML/text email.
- A3. `service/mailer.py` — Resend integration + templates + List-Unsubscribe.
- A4. `service/pipeline.py` — the daily loop; dedup via digest_sends.
- A5. Optional `claude -p` polish step behind flag.
- A6. **CV fast-path (parse & discard)** — `service/cvparse.py`: read PDF/DOCX in memory,
      extract derived signals only (skills, role categories, seniority, sectors, years),
      then discard the file. Signals merged into the profile via `cvparse.merge_into_profile`
      (user-entered values always win); stored as `profiles.has_cv/cv_summary/years_experience`
      (migration 003). Heuristic now, Claude pass later — return shape is stable.
- ✅ **Milestone:** run pipeline locally → a real digest email lands in your inbox.

**Phase B — Public capture**
- ✅ B1. **DONE** — public API `service/webapp.py` (separate from internal `api.py`):
      `POST /subscribe` (pending), `GET /confirm`, `GET/POST /preferences`, `POST /pause`,
      `POST /resume`, `GET/POST /unsubscribe` (human page + RFC 8058 one-click). All
      token-based, no accounts. Turnstile verify helper (no-op when `TURNSTILE_SECRET`
      unset). Suppressed addresses can't be silently re-subscribed; responses never reveal
      whether an email exists. Validated end-to-end (24 checks) via TestClient.
- ✅ B3. **DONE** — double opt-in wired: `service/transactional.py` (Direction-A confirm +
      welcome emails); `/subscribe` sends confirm, `/confirm` activates + sends welcome.
- ✅ B4. **DONE** — `POST /cv/parse` (multipart → `cvparse` signals JSON, behind Turnstile,
      file discarded). `/subscribe` also accepts `cv_signals` and merges server-side.
- ✅ B2. **DONE** — Next.js app in `web/` (App Router, TS, `output: export` static). Pages:
      `/` landing (Direction-A wizard + CV drop-zone POSTing to `/cv/parse` + live email
      preview → `POST /subscribe`), `/check-inbox`, `/preferences` (GET/POST by token, pause/
      resume, unsubscribe), `/privacy`, `/terms`. Shared chrome + theme toggle; API client in
      `web/lib/api.ts` (`NEXT_PUBLIC_API_URL`). CORS added to `webapp.py` (`CORS_ORIGINS`).
      `npm run build` green (8 static routes); full flow verified in a real browser end-to-end
      (subscribe → check-inbox → preferences GET → pause POST all persisted).
- ✅ **Milestone:** stranger can sign up → confirm → is in the DB as active. **Phase B complete.**

**Phase C — Deploy + protect** (stack built + smoke-tested locally; awaiting a VPS + the two
open decisions below to go live)
- ✅ C1. **DONE (built + validated locally)** — Docker Compose stack in `deploy/`: `db`
      (postgres:16, schema.sql auto-seed), `api` (backend image, repo-root `Dockerfile`), `web`
      (multi-stage: Next static export → Caddy edge that serves `/` and reverse-proxies `/api`
      → same-origin, **no CORS in prod**), optional `n8n` (automation profile) + `pipeline`
      (manual profile). Both images build clean; full stack smoke-tested (frontend 200,
      `/api/health`, subscribe→pending row) via `docker-compose.smoke.yml`. Email links fixed
      for prod split via `service/links.py` (SITE_URL vs API_PUBLIC_URL: `/confirm` +
      `/unsubscribe` → API, `/preferences` → frontend).
- ⏳ C2. Domain + Cloudflare (proxied DNS, Turnstile keys, WAF/rate rules, origin lock) —
      **documented** in `deploy/README.md §3`; needs the domain + a live CF zone.
      **Turnstile is fully wired**: real widget on the landing (`web/components/Turnstile.tsx`),
      token → `/subscribe` (strictly gated), `/cv/parse` edge-rate-limited. No-op when the
      site key is unset (dev). Verified with Cloudflare test keys (widget renders + passes,
      token accepted, missing-token rejected). Just needs the real site+secret keys at launch.
- ⏳ C3. Email domain auth (SPF/DKIM/DMARC) + deliverability test — **documented** §4; needs
      the domain + chosen provider (Resend vs SMTP).
- ✅ C4. Daily cron — systemd timer (`deploy/jobdigest-pipeline.{service,timer}`, 07:00) +
      n8n option documented (§6). Runs `docker compose run --rm pipeline`.
- ✅ C5. Backups + monitoring — pg_dump cron + uptime ping + Telegram error alerts
      documented (§7).
- ⏳ **Milestone:** live site; real daily digests going out reliably — blocked only on a VPS +
      the domain + email-provider decisions.

**Phase D — Expand (post-launch, data-model already supports it)**
- Role taxonomy table + LLM classification at ingest → "any role."
- Add US sources + `countries`/eligibility as data → US + Europe.
- Salary normalization; weekly-frequency option; then accounts + Stripe (freemium).

---

## 13. Using subagents to build efficiently (your question)

Yes — several build chunks are independent and parallelizable. When you say go, I'd dispatch:

| Agent | Job | When |
|---|---|---|
| **Explore** | Recon existing `service/` + `ingestion/` before edits (read-only, cheap) | Start of Phase A |
| **Plan** | Detailed implementation plan for a tricky module (e.g. deliverability) if needed | As needed |
| **general-purpose / fork** (parallel) | Build **independent** modules concurrently: (1) `digest.py`+`mailer.py`, (2) Next.js frontend, (3) Cloudflare/VPS config + Compose. They don't touch each other's files. | Phases A–C |

Rules I'll follow: I only spawn agents when you ask; each agent gets a tightly-scoped brief;
I relay their results back to you (their raw output isn't shown to you). Sequential,
dependency-heavy work (schema → digest → pipeline) I do inline, not via agents.

For **your own** ongoing use, the built-in `Explore`/`Plan`/`general-purpose` agents are all
we need — no external "project" of agents required.

---

## 14. Decisions — LOCKED (2026-07-18)
1. **Name/brand**: **JobDigest**.
2. **Email provider**: **provider-agnostic mailer**, final choice at deploy (Phase C).
   Lean Resend for launch.
3. **Frontend**: **Next.js** (static-export landing; future dashboard).
4. **VPS host + region**: **Hetzner EU** (GDPR-friendly, ~€4/mo).
5. **`/matches` live API**: **keep the code, don't expose it in v1** (pure batch product).
6. **Scoring engine**: **heuristic (free) + Claude subscription polish** behind a flag.
7. **Daily engine**: **cloud cron** on the VPS (n8n schedule + systemd timer fallback).
8. **Design direction**: **A — "Morning Briefing"** (locked 2026-07-18). Calm/editorial,
   warm paper + teal + amber, Georgia display; sign-up = chip-based 4-step wizard with a
   live-building email preview. Canonical prototype: `design/prototype.html` (all screens).
9. **CV upload**: **parse & discard** — store only derived signals, delete the file
   immediately; extraction **heuristic now, Claude later** (locked 2026-07-18).
10. Still open: exact **domain** (needed at deploy for email auth + Cloudflare).

---

*Design first: interactive UX/UI prototype for sign-off before any product code.
Then Phase A: schema pivot → `digest.py` → a real digest email, on free heuristics.*
