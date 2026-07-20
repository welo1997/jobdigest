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
| Output | `fct_postings`, Telegram alerts | Daily digest email, jobdigest.eu |
| Blast radius if broken | A stale dataset | Real people's inboxes and personal data |

Shared: `ingestion/` (source adapters), `search_jobs.py` (classifiers),
`service/taxonomy.py` (role taxonomy). Nothing else is shared — Snowflake code must never
reach into `service/`, and JobDigest must never require Snowflake.

**JobDigest handles real personal data.** Treat changes there with the care that implies.

```
ingestion/        BaseSource adapters, Snowflake load, migrations
enrichment/       Claude skill extraction + personal scoring (Snowflake side)
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
  `fct_postings` (incremental), `fct_personal_matches`.
- **The `agg_*` analysis layer is deliberately unbuilt.** Design it after exploring real
  data, not before. Do not add aggregation models speculatively.
- Enrichment (`enrichment/`) must be idempotent, must validate Claude's JSON, and must log
  and skip errors rather than crash the run. **But a run that enriched *nothing* must fail
  loudly** — skipping one bad posting is intended, skipping all of them is an outage, and
  exiting 0 on it hides the cause two steps downstream. Both entry points enforce this.
- **Enrichment runs on subscription compute, not API credits.** `ANTHROPIC_API_KEY` is
  deliberately absent from `pipeline.yml`; the LLM work belongs in a claude.ai routine
  behind a file exchange, the same shape as `deploy/matcher-routine.md`. With no key set,
  skill extraction and personal scoring log a skip and exit 0, and `notify.py`'s curator
  falls back to ranking by score. **Do not add the key back to a workflow** — its absence
  is what enforces the billing decision. The routine side is not built yet; until it is,
  `raw.skill_tags` and `raw.personal_scores` simply stay empty.

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
- Metabase is local Docker only; portfolio evidence is screenshots + dbt docs.
- The claude.ai routine reaches Drive through the **owner's own connector**. The folder is
  owner-only and should stay unshared — sharing it is what would create third-party access.
- `service/api.py` is the pre-v1 internal API. It has no auth and is **not** deployed
  (`Dockerfile` runs `service.webapp`). Do not expose it without adding auth.

## Open items

Tracked from `notes/2026-07-18-security-review.md`:

- `GET /unsubscribe` fixed, but **confirm-token expiry/single-use** is still open.
- Job `url` from third-party feeds is HTML-escaped but not scheme-validated before going
  into an email `href`.
- No `pip-audit` / `npm audit` / Dependabot in CI.
- Off-box backup retention is the remote's business — nothing prunes it yet.
- Cert renewal: Caddy's LE cert cannot renew behind the Cloudflare proxy. Switch to a
  Cloudflare Origin Certificate before ~mid-Sept 2026.
