# Job Market Intelligence + JobDigest

Two systems in one repository, sharing an ingestion layer.

1. **Market intelligence** — a daily pipeline that ingests job postings from 15+ sources
   into Snowflake, enriches them with Claude, and models them with dbt Core. A portfolio
   dataset for analysing the tech job market over time.
2. **JobDigest** ([jobdigest.eu](https://jobdigest.eu)) — a live product built on the same
   ingestion code. Subscribers get one email a morning with jobs an AI matcher picked for
   them. Postgres, FastAPI, Next.js, self-hosted.

They share `ingestion/` (the source adapters), `search_jobs.py` (the classifiers) and
`service/taxonomy.py` (the role taxonomy). Everything downstream is separate: the
intelligence side writes to Snowflake and runs in GitHub Actions; JobDigest writes to
Postgres and runs on a VPS.

```
                    ┌─────────────────────────────────────────┐
   15+ sources ───► │  ingestion/  +  search_jobs classifiers │
   RSS · REST · ATS └───────────────┬─────────────────────────┘
                                    │
              ┌─────────────────────┴──────────────────────┐
              ▼                                            ▼
   ┌──────────────────────┐                   ┌────────────────────────┐
   │ MARKET INTELLIGENCE  │                   │      JOBDIGEST         │
   │ GitHub Actions       │                   │ Hetzner VPS · Docker   │
   ├──────────────────────┤                   ├────────────────────────┤
   │ Snowflake RAW        │                   │ Postgres postings      │
   │  + Claude skill tags │                   │  ▼                     │
   │  ▼                   │                   │ AI matcher (Claude)    │
   │ dbt staging/int/mart │                   │  ▼                     │
   │  ▼                   │                   │ digest → Resend email  │
   │ fct_postings         │                   │ Next.js + Caddy + CF   │
   │ → Telegram alerts    │                   │                        │
   └──────────────────────┘                   └────────────────────────┘
```

---

## JobDigest

A subscriber fills in a short profile (roles, stack, region, work type — optionally
autofilled from a CV), confirms by email, and receives a daily digest of jobs ranked for
them, each with a one-line explanation of the fit.

### How the matching works

Retrieve, then rerank. A cheap SQL/full-text prefilter builds a shortlist of ~120 candidate
postings per subscriber; Claude then reads the whole shortlist *in context* and picks what
genuinely fits, scoring 0–10. One model call per subscriber, so cost scales with
subscribers rather than with inventory. Postings are passed by short integer index, so the
32-char posting IDs never round-trip through the model, and any index the model invents is
dropped.

Strong picks (≥6) go in the email; weaker ones appear on the subscriber's private
`/matches` page. A job is never emailed twice — `digest_sends` is an append-only ledger.

### The file exchange

Free-tier matching runs inside a claude.ai cloud routine, which has no database access and
no API key. So the credentialed side exports a JSON shortlist, the routine reads it and
writes back picks, and the credentialed side imports them:

```
 shortlists.json                                picks.json
 ──────────────►   claude.ai routine   ──────────────►
 (export, has DB)   (no DB, no key)      (import, has DB)
```

That file is a trust boundary, and it is treated as one. `picks.json` is written outside
our control, so **both** sides of every match are validated against the database on import:
`posting_id` must be a real active posting (so no invented job or URL can reach an inbox)
and `profile_id` must be a real subscriber. Scores are clamped, and one malformed record is
logged and skipped rather than costing every subscriber their digest. `shortlists.json`
carries **no email addresses** — a profile is an opaque UUID — so the file cannot by itself
identify anyone. See `deploy/matcher-routine.md`.

### Privacy design

- **No accounts, no passwords.** Management is via 256-bit `secrets.token_urlsafe(32)`
  links from the email. No credential database exists to breach.
- **Double opt-in.** Nothing is sent until the address is confirmed.
- **CVs are parse-and-discard.** The file is read in memory, a short summary line is
  derived, and the document is dropped. It is never stored or transmitted.
- **Cookieless analytics.** No IP stored raw or hashed, country only, coarse browser
  family, a session ID that dies with the tab. Do Not Track and GPC are honoured.
- **Retention is enforced in code**, not just promised in the policy: descriptions are
  blanked after 90 days, raw analytics events after 180 (rolled up first), and
  unsubscribed profiles are erased after 30 days.
- **Backups are encrypted before leaving the server**, and the backup script refuses to
  write into the folder used for the matcher file exchange.

### Stack

| Layer | Tool |
|---|---|
| API | FastAPI + Pydantic, `service/webapp.py` |
| Store | Postgres 16 (`postings`, `profiles`, `matches`, `digest_sends`, `suppression`, `events`) |
| Matching | Claude (`claude-haiku-4-5`) or the claude.ai routine |
| Web | Next.js 15 static export, Caddy, Cloudflare |
| Email | Resend (EU), RFC 8058 one-click unsubscribe |
| Host | Hetzner VPS, Docker Compose, systemd timers |

---

## Market intelligence

The original pipeline: ingest broadly, store everything, model in dbt, analyse later.

| Layer | Tool |
|---|---|
| Ingestion | Python 3.12 — `requests`, `feedparser`, `beautifulsoup4` |
| Warehouse | Snowflake (`JOB_MARKET`) |
| Transform | dbt Core |
| Enrichment | Claude Haiku — skill extraction + personal fit scoring |
| Orchestration | GitHub Actions |
| Alerts | Telegram Bot API |

```
JOB_MARKET
├── RAW            job_postings · skill_tags · personal_scores
├── STAGING        stg_job_postings                    (views)
├── INTERMEDIATE   int_postings_enriched · int_skill_exploded
└── MARTS
    ├── ANALYSIS   fct_postings
    └── PERSONAL   fct_personal_matches → Telegram
```

Scope is deliberately broad: **all roles at tech companies**, not just engineering titles.
Filtering happens at the company level, never the role level — nothing is dropped for being
`uncategorised`. Over-collection beats under-collection for a dataset project.

The analysis layer (`agg_*` models) is deliberately unbuilt. It gets designed after enough
real data accumulates to show what is actually interesting, rather than guessed at upfront.

### Snowflake least-privilege role — and the secondary-roles trap

The pipeline authenticates as its own dedicated user (`JOB_MARKET_CI`), not a personal
login wearing a restricted role. That distinction is load-bearing, not stylistic:

**A role grant on your own admin user does not restrict CI.** Snowflake sessions carry a
primary role *and*, by default, secondary roles — the union of every role the connecting
user holds. If CI authenticates as a personal account that also holds `ACCOUNTADMIN` or
`SYSADMIN`, specifying `role: JOB_MARKET_ETL` in the connector only sets the primary
role; `current_role()` reports it correctly, but privilege checks still use the union, so
the admin membership leaks straight through. Verified the hard way (2026-07-20): a role
with only `USAGE`/`CREATE TABLE`/`CREATE VIEW`/DML grants was still able to run
`DROP DATABASE` under exactly this setup, because the session testing it was authenticated
as an `ACCOUNTADMIN`-holding personal login. The only durable fix is a user that holds
**no other role**, so there is nothing for secondary roles to leak in from — plus
`DEFAULT_SECONDARY_ROLES = ()` as defence-in-depth against that ever changing.

Run the following as `SECURITYADMIN`/`SYSADMIN`/`ACCOUNTADMIN`:

```sql
create role if not exists JOB_MARKET_ETL;

grant usage on warehouse COMPUTE_WH to role JOB_MARKET_ETL;
grant usage on database  JOB_MARKET to role JOB_MARKET_ETL;

-- dbt creates and replaces objects here, and only here.
grant usage, create table, create view on schema JOB_MARKET.RAW            to role JOB_MARKET_ETL;
grant usage, create table, create view on schema JOB_MARKET.STAGING        to role JOB_MARKET_ETL;
grant usage, create table, create view on schema JOB_MARKET.INTERMEDIATE   to role JOB_MARKET_ETL;
grant usage, create table, create view on schema JOB_MARKET.MARTS_ANALYSIS to role JOB_MARKET_ETL;
grant usage, create table, create view on schema JOB_MARKET.MARTS_PERSONAL to role JOB_MARKET_ETL;

grant select, insert, update, delete on all    tables in database JOB_MARKET to role JOB_MARKET_ETL;
grant select, insert, update, delete on future tables in database JOB_MARKET to role JOB_MARKET_ETL;
grant select on all views    in database JOB_MARKET to role JOB_MARKET_ETL;
grant select on future views in database JOB_MARKET to role JOB_MARKET_ETL;

-- ingestion/migrate.py runs ALTER TABLE ... ADD COLUMN IF NOT EXISTS on this one table as
-- a normal, idempotent part of every pipeline run. Snowflake has no separate DDL-only
-- privilege for a table's own structure (confirmed against the docs: MODIFY applies to
-- Database/Schema/Warehouse/etc, not Table) — altering a table requires OWNERSHIP of it.
-- Scoped to exactly this one table, not the schema or database: this role can still not
-- drop the database, drop the schema, or touch a table it doesn't own. If a future
-- migration needs to alter skill_tags or personal_scores, extend this then — don't grant
-- ownership ahead of actual need.
grant ownership on table JOB_MARKET.RAW.JOB_POSTINGS to role JOB_MARKET_ETL copy current grants;

-- A dedicated user, not a personal login: no ACCOUNTADMIN/SYSADMIN membership, no
-- password (the account requires MFA for password auth, which a headless credential can
-- never satisfy — keypair only), and DEFAULT_SECONDARY_ROLES = () so this exact bug
-- can't come back even if a second role is ever granted to it by mistake.
create user if not exists JOB_MARKET_CI;
alter user JOB_MARKET_CI set
    rsa_public_key = '<paste the public key, base64, no PEM header/footer>',
    default_role = JOB_MARKET_ETL,
    default_warehouse = COMPUTE_WH,
    default_secondary_roles = ();
grant role JOB_MARKET_ETL to user JOB_MARKET_CI;
```

GitHub secrets: `SNOWFLAKE_USER=JOB_MARKET_CI`, `SNOWFLAKE_ROLE=JOB_MARKET_ETL`,
`SNOWFLAKE_PRIVATE_KEY` = the matching private key (PEM). No `SNOWFLAKE_PASSWORD` — the
workflow writes the key secret to a runner-temp file and points
`SNOWFLAKE_PRIVATE_KEY_PATH` at it (see `.github/workflows/pipeline.yml`).

Verify the identity is actually restricted — not just that the grants look right on
paper — by connecting **as the service user, with its own credential**, not as an admin
session with `role=` overridden:

```sql
-- as JOB_MARKET_CI:
select current_role(), current_secondary_roles();  -- roles must be empty
drop database JOB_MARKET;                          -- must be refused
```

---

## Sources

Shared by both systems. RSS, public REST APIs and ATS boards only — no scraping behind
logins, no Playwright, no domain-wide crawls.

| Group | Sources |
|---|---|
| Remote boards | Remotive, WeWorkRemotely, RemoteOK, Himalayas, Jobicy, WorkingNomads, Arbeitnow |
| Czech / Slovak | Jobs.cz, StartupJobs, Profesia, Cocuma |
| Aggregators | Adzuna (CZ/DE/NL/GB/US), EuroJobs, LinkedIn public RSS |
| ATS boards | Greenhouse, Lever, Ashby — curated company list only |

Each source subclasses `BaseSource` and implements `fetch()` / `normalize()`. `posting_id`
is `md5(url)`, so re-ingesting is an upsert that refreshes `last_seen_at`. Postings not seen
for 7 days are marked inactive rather than deleted.

---

## Layout

```
ingestion/        source adapters (BaseSource) + Snowflake load
enrichment/       Claude skill extraction + personal scoring
dbt/              staging → intermediate → marts
service/          JobDigest: API, matcher, digest, mailer, store, taxonomy
web/              Next.js landing + preferences/matches pages
deploy/           Docker Compose, Caddy, systemd timers, runbooks
notes/            session logs and the security review
```

---

## Running it

```bash
pip install -r requirements.txt          # intelligence pipeline
pip install -r service/requirements.txt  # JobDigest API

cp .env.example .env                     # fill in credentials

python -m ingestion.run                  # ingest → Snowflake
python -m enrichment.skill_extractor     # Claude skill tags
cd dbt && dbt seed && dbt run && dbt test

# JobDigest, locally
docker compose -f service/db/docker-compose.yml up -d
DATABASE_URL=postgresql://jobmatch:jobmatch@localhost:5433/jobmatch \
  MAIL_BACKEND=file uvicorn service.webapp:app --reload
```

Deployment, backups and the matcher routine are documented in `deploy/README.md` and
`deploy/matcher-routine.md`.

## Tests

```bash
python -m pytest                # ingestion, enrichment, service
cd dbt && dbt test              # schema tests
```

`service/tests/` covers the parts where being wrong is expensive: the picks.json trust
boundary, the unsubscribe lifecycle (a GET must never unsubscribe anyone), the `/event`
abuse bounds, and drift between the role taxonomy and its non-Python consumers.

## License

Private project.
