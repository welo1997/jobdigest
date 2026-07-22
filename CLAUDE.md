# CLAUDE.md — Job Market Intelligence + JobDigest

## What this repo is

**Two systems sharing an ingestion layer.** Read this section before changing anything —
the biggest source of mistakes here is assuming there is only one.

| | Market intelligence | JobDigest |
|---|---|---|
| Purpose | Portfolio dataset for analysing the job market | Live product, real subscribers |
| Store | Snowflake `JOB_MARKET` | Postgres on a Hetzner VPS |
| Transform | dbt Core | `service/` Python |
| Runs on | GitHub Actions (`workflow_dispatch`) | systemd timers on the VPS |
| Output | `fct_postings` (analysis dataset) | Daily digest email, jobdigest.eu |
| Blast radius if broken | A stale dataset | Real people's inboxes and personal data |

Shared: `ingestion/` (source adapters), `search_jobs.py` (classifiers),
`service/taxonomy.py` (role taxonomy). Nothing else is shared — Snowflake code must never
reach into `service/`, and JobDigest must never require Snowflake.

> **Status (2026-07-22): the market-intelligence / Snowflake side is DECOMMISSIONED.** The
> `JOB_MARKET` database, the `JOB_MARKET_CI` user and `JOB_MARKET_ETL` role were dropped, and
> the two cloud routines (pipeline trigger, skill extraction) are paused. `COMPUTE_WH` and
> `JOB_MARKET_MONITOR` were kept — the warehouse is shared with other projects. The `dbt/`,
> `enrichment/`, `ingestion/` and workflow code is **kept as a portfolio snapshot** and no
> longer runs; `dbt_docs.yml`'s schedule is disabled. **JobDigest is the sole live system.**
> To revive market-intel you'd re-create the Snowflake objects (`setup_snowflake.py`) and
> re-enable the routines. Do not spend effort maintaining this side — see the memory note.

**JobDigest handles real personal data.** Treat changes there with the care that implies.

```
ingestion/        BaseSource adapters, Snowflake load, migrations
enrichment/       Claude skill extraction + the routine file exchange (Snowflake side)
dbt/              staging → intermediate → marts
service/          JobDigest: webapp, matcher, digest, mailer, store, taxonomy, cvparse
  service/tests/  the tests that matter most — see "Testing"
web/              Next.js 15 static export (landing, preferences, matches, privacy)
deploy/           Compose, Caddy, systemd units, backup + matcher runbooks
notes/            session logs, security review; notes/INFRA.local.md is gitignored
```

---

## JobDigest

### Flow

```
05:00 UTC  export   ingest sources → Postgres → write shortlists.json → Google Drive
~06:00     routine  claude.ai reads shortlists.json → writes picks.json      (no DB, no key)
07:00 UTC  import   pull picks.json → validate → matches → build + send digests
03:30 UTC  backup   pg_dump → encrypt → off-box
```

Retrieve-then-rerank: a cheap full-text prefilter builds a ~120-posting shortlist per
subscriber, then one Claude call reads the whole shortlist in context and picks what fits.
Cost scales with subscribers, not inventory. Candidates go to the model by integer index —
posting IDs never round-trip — and invented indices are dropped.

Picks ≥ `MATCH_FLOOR` (4) are stored; only ≥ `EMAIL_MIN_SCORE` (6) are emailed. The rest
show on the subscriber's `/matches` page. `digest_sends` guarantees a job is never emailed
twice and is never pruned.

### Tables (Postgres)

`postings` · `profiles` (a subscription: preferences, tokens, CV summary) · `matches` ·
`digest_sends` · `suppression` (never-contact list, outlives the profile) · `events`
(cookieless analytics) · `events_daily` (rollups).

### The file exchange is a trust boundary

`picks.json` is written by a claude.ai routine and travels via cloud storage. Nothing in it
is trusted. `import_picks` validates **both** sides of every match against the DB —
`posting_id` must be a real active posting, `profile_id` a real subscriber — clamps scores,
and skips a malformed record rather than aborting everyone's digest. Do not relax this.

`shortlists.json` must **never** contain email addresses. A profile is an opaque UUID. This
is what makes the transfer non-identifying and is load-bearing for the privacy policy.

### Deployment reality

`/opt/jobdigest` on the VPS is **scp'd, not a git checkout**. Local `master` and the box can
drift, and have. After changing anything under `service/`, `deploy/` or `web/`, copy it up
and rebuild the affected image — a merged commit is not a deployed commit.

```bash
scp <files> deploy@<VPS>:/opt/jobdigest/<path>/
ssh deploy@<VPS> 'cd /opt/jobdigest/deploy && sudo docker compose build pipeline web'
```

Host `deploy` and the container's `app` user are **both uid 1000**, which is why
`exchange/` can be 0700 and the container still writes to it.

---

## Market intelligence

Ingest broadly, store everything, model in dbt, analyse later.

- **Scope is company-type, not role-type**: all roles at tech companies. Exclude non-tech
  industries via `industry_blocklist.csv`. When in doubt, keep the posting.
- No row is ever dropped for its `role_category`; `uncategorised` is a first-class value.
- `stg_job_postings` (view) → `int_postings_enriched`, `int_skill_exploded` (tables) →
  `fct_postings` (incremental). There is no personal/alerting mart — see below.
- **The `agg_*` analysis layer is deliberately unbuilt.** Design it after exploring real
  data, not before. Do not add aggregation models speculatively.
- Enrichment (`enrichment/`) must be idempotent, must validate Claude's JSON, and must log
  and skip errors rather than crash the run. **But a run that enriched *nothing* must fail
  loudly** — skipping one bad posting is intended, skipping all of them is an outage, and
  exiting 0 on it hides the cause two steps downstream. Both entry points enforce this.
- **Enrichment runs on subscription compute, not API credits.** `ANTHROPIC_API_KEY` is
  deliberately absent from `pipeline.yml`; the LLM work belongs in a claude.ai routine
  behind a file exchange, the same shape as `deploy/matcher-routine.md`. With no key set,
  skill extraction logs a skip and exits 0. **Do not add the key back to a workflow** — its
  absence is what enforces the billing decision. The routine side is built and running:
  `enrichment/exchange.py` + `enrichment/ROUTINE.md`, Steps 4a and 9 of `pipeline.yml`.
- **Personal scoring was retired 2026-07-21.** A per-posting "how well does this fit Vojta"
  score fed a Telegram alert feed; JobDigest, subscribed to as an ordinary user, replaces it.
  Gone: `fct_personal_matches`, `enrichment/personal_scorer.py`, `enrichment/curator.py`,
  `notify.py`, `raw.personal_scores`, the `TELEGRAM_*` secrets. The market-intelligence side
  is now purely an analysis dataset — **do not reintroduce a personal-fit column into it.**
  `raw.job_postings` keeps its `notified` / `notified_at` columns — they are `FALSE`/`NULL`
  for all 18 816 rows (the alert feed never actually wrote them back), so there is no history
  to preserve and no reason to run destructive DDL on the one table the pipeline depends on.
  The dbt models deliberately no longer select them; treat them as vestigial.

---

## Security and privacy rules

These are not style preferences. Breaking one has consequences outside this repo.

1. **Never commit** `.env`, `exchange/`, `notes/INFRA.local.md`, `candidates*.json`, or any
   file containing a subscriber address. Check `.gitignore` before adding data files.
2. **Never put a secret in a transcript.** Generate credentials directly into files on the
   target host; when a human needs one, have them read it out of band. This applies to
   session transcripts as much as to commits.
3. **A GET must not change state.** `/unsubscribe` renders a confirm button; the POST acts.
   Link scanners and mail clients fetch URLs found in email.
4. **The privacy policy is a specification.** `web/app/privacy/page.tsx` makes concrete
   promises — no IP stored, CV discarded, unsubscribed profiles erased within 30 days, data
   sent to the matcher carries no email. Changing retention, adding a processor, or storing
   a new field means changing that page in the same commit, or the policy becomes false.
5. **Backups contain bearer tokens.** `manage_token` grants full control of a subscription.
   Dumps are encrypted before leaving the box and must never be written into the matcher's
   Drive folder — `jobdigest-backup.sh` enforces this.
6. **Least privilege.** No SYSADMIN in CI; no root SSH; the API container runs non-root.
   For Snowflake specifically: **a scoped role granted to a personal admin login does not
   restrict anything.** Snowflake sessions carry secondary roles by default — the union of
   every role the connecting user holds — so `role: JOB_MARKET_ETL` on a session
   authenticated as an `ACCOUNTADMIN`-holding user still has `ACCOUNTADMIN`'s privileges.
   Verified the hard way (2026-07-20): this let a "restricted" session run
   `DROP DATABASE JOB_MARKET` for real. CI uses `JOB_MARKET_CI`, a dedicated user with no
   other role and `DEFAULT_SECONDARY_ROLES = ()` — see README's "Snowflake least-privilege
   role" for the full DDL and how to verify it's genuinely restricted (connect as the
   service user itself, not an admin session with `role=` overridden).
7. **Test the credential, not the grant statement.** `GRANT ... TO ROLE X; SHOW GRANTS TO
   ROLE X` looking correct is not proof anything is restricted — see rule 6. Connect as the
   actual identity that will be used and try the operation that should be refused.
8. **Never create a Snowflake object as an admin role.** There is no separate dev database:
   `dev_local` and every script in `scripts/` write to the *same* `JOB_MARKET` that CI
   writes to. A new object is owned by the **primary role that created it**, and changing
   an existing object — dbt's `create or replace view`, or an `ALTER TABLE` — requires
   `OWNERSHIP`, not a `CREATE`-class grant. So one local run as `SYSADMIN` leaves an object
   `JOB_MARKET_CI` can never rebuild, and CI fails with *"Insufficient privileges to operate
   on ..."* in a step that has nothing to do with what you changed. This has broken the
   pipeline twice (2026-07-20, runs `29766309781` and `29773894574`) — the second time it
   was the run *verifying the first fix* that caused it.

   `dbt/profiles.yml` pins `dev_local` to `role: JOB_MARKET_ETL` for this reason — do not
   revert it to `env_var('SNOWFLAKE_ROLE')`. The Python entry points are **not** pinned;
   they read `SNOWFLAKE_ROLE`, which is `SYSADMIN` in a normal local `.env`. Before running
   anything that does DDL (`setup_snowflake.py`, `ingestion/migrations`, `scripts/fix_*`),
   set `SNOWFLAKE_ROLE=JOB_MARKET_ETL` for that command. Reserve `SYSADMIN` for work that
   genuinely needs it, such as the recovery itself:

   ```sql
   GRANT OWNERSHIP ON <TABLE|VIEW> JOB_MARKET.<schema>.<obj>
     TO ROLE JOB_MARKET_ETL COPY CURRENT GRANTS;   -- COPY CURRENT GRANTS, or grants are dropped
   ```

   To audit: `SHOW TABLES`/`SHOW VIEWS` in each schema and check the `owner` column —
   anything not `JOB_MARKET_ETL` is a future CI failure waiting to happen.

---

## Conventions

- Python: type hints on public functions, `black`, `ruff`. Each source subclasses
  `BaseSource` — no ad-hoc scripts.
- Credentials via env vars only. `python-dotenv` locally.
- dbt naming: `stg_` / `int_` / `agg_` / `fct_`, no exceptions.
- No hardcoded thresholds or weights — seeds and env vars.
- `role_category` has **one** definition: `service/taxonomy.py`. The dbt YAML and
  `web/app/page.tsx` cannot import it, so tests assert they do not drift. Adding a category
  means: pattern, subject word, shortlist keywords, dbt `accepted_values`, run tests.
- Shell scripts and systemd units are LF-only (`.gitattributes`) — CRLF breaks them on the
  VPS in ways that look like unrelated failures.
- Commits: conventional (`feat:` `fix:` `chore:` `docs:` `refactor:` `analysis:`).
  Explain *why*, and name the failure the change prevents.

## Testing

```bash
python -m pytest                # ingestion, enrichment, service
cd dbt && dbt test
```

`service/tests/` covers the places where being wrong is expensive, and the tests are
written to fail when the guarantee breaks — not merely to pass:

- `test_matcher_exchange.py` — untrusted `picks.json` cannot reach the DB; the export
  carries no email.
- `test_webapp_lifecycle.py` — a GET never unsubscribes; one-click still works; `/event`
  is bounded.
- `test_taxonomy.py` — classification, ordering, and drift against dbt + the frontend.

When adding a guard, mutation-check it: break the thing deliberately and confirm the test
goes red. A test that cannot fail documents nothing.

---

## Known constraints and decisions

- **Adzuna** 250 req/day across CZ/DE/NL/GB/US. **LinkedIn** RSS only, never Playwright —
  account ban risk. **Greenhouse/Lever/Ashby** curated company list, no domain-wide crawls.
- **Haiku for both enrichment passes** — well-calibrated at ~10× lower cost than Sonnet.
- Salary coverage is ~30–40%; no row is dropped for a missing salary.
- **Snowflake stores Prague wall-clock time, not UTC.** The account `TIMEZONE` is
  `Europe/Prague` (changed 2026-07-21 from Snowflake's `America/Los_Angeles` default, which
  had been silently storing US Pacific times in a Czech job-market dataset — `loaded_at`
  read 7 hours stale on a run that had just finished). `CURRENT_TIMESTAMP()` into a
  `TIMESTAMP_NTZ` column therefore records the wall clock you'd read in Prague: UTC+1 in
  winter, UTC+2 in summer. The 16 836 existing rows were converted with `convert_timezone`
  in the same change, so the column has one basis throughout — **do not "fix" older rows
  again.** `sysdate()` is the escape hatch when you genuinely need UTC.
  Two consequences worth knowing before changing anything here: comparisons must stay on
  one basis — a freshness filter comparing `last_seen_at` against `current_timestamp()` is
  correct because both resolve session-local, whereas comparing against `sysdate()` would
  silently be off by an hour or two — and anything that changes what
  `loaded_at` *means* needs `fct_postings` rebuilt with the workflow's `full_refresh`
  input, because its incremental watermark is a stored wall-clock value.
- **`JOB_MARKET_MONITOR` caps Snowflake spend** (added 2026-07-21; there was no monitor at
  all before). 30 credits/month on `COMPUTE_WH`: notify at 60% and 80%, suspend at 100%,
  suspend immediately at 125%. Sized from measured usage — ~4.7 credits/30 days, busiest
  day 0.99 — so the quota is ~6× normal. The threat it guards is not steady state but a
  warehouse left running: an X-Small burns ~24 credits/day, so a runaway trips this in
  ~30 hours instead of arriving as a surprise invoice.
  Scoped to `COMPUTE_WH`, **not the account**, so it cannot suspend `ZALANDO_SCRAPER_WH`,
  which belongs to a different project. Two things to know: the 100%→125% gap is deliberate
  (`SUSPEND` lets a mid-flight pipeline run finish rather than leave a half-built mart;
  only `SUSPEND_IMMEDIATE` kills queries), and **`NOTIFY` only emails account admins who
  have notifications enabled in their Snowflake profile** — if that was never set up, the
  60%/80% warnings go nowhere and the first symptom is a suspended warehouse.
- Metabase is local Docker only; portfolio evidence is screenshots + dbt docs.
- The claude.ai routine reaches Drive through the **owner's own connector**. The folder is
  owner-only and should stay unshared — sharing it is what would create third-party access.
- `service/api.py` is the pre-v1 internal API. It has no auth and is **not** deployed
  (`Dockerfile` runs `service.webapp`). Do not expose it without adding auth.

## Open items

Tracked from `notes/2026-07-18-security-review.md`. **Resolved since that review** (do not
reopen): confirm-token expiry *and* single-use — the token is consumed on confirm, so a
replayed confirm link can no longer be exchanged for the manage token
(`store.confirm_subscription`); job-`url` scheme validation in both the digest email
(`safe_url`) and the `/matches` page (`web/lib/url.ts` `safeHref`); `pip-audit` / `npm audit`
/ Dependabot in CI (`.github/workflows/audit.yml`, `.github/dependabot.yml`); off-box backup
retention — `jobdigest-backup.sh` prunes both local and the encrypted Drive copies past
`KEEP_DAYS` (30), verified running on the VPS 2026-07-22.

Still open:

- Cert renewal: Caddy's LE cert cannot renew behind the Cloudflare proxy. Switch to a
  Cloudflare Origin Certificate before ~mid-Sept 2026 — runbook: `deploy/cert-renewal.md`.
  **This is the only item left.**
