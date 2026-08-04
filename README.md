# Job Market Intelligence + JobDigest

Two systems in one repository, sharing an ingestion layer.

1. **Market intelligence** — a daily pipeline that ingests job postings from 18 sources
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
   18 sources ───► │  ingestion/  +  search_jobs classifiers │
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
   │                      │                   │                        │
   └──────────────────────┘                   └────────────────────────┘
```

---

## JobDigest

A subscriber fills in a short profile (roles, stack, region, work type — optionally
autofilled from a CV), confirms by email, and receives a daily digest of jobs ranked for
them, each with a one-line explanation of the fit.

The site serves in **eight languages** (en cs de sk pl es fr it) under locale-prefixed URLs,
and the digest arrives in the one they signed up in — `profiles.language`, since a mail sent
from a timer has no browser to ask.

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

- **No passwords, ever.** Management is via 256-bit `secrets.token_urlsafe(32)` links from
  the email, optionally exchanged for a session cookie so a device stays signed in; "Continue
  with Google" is the other way in, and Google returns nothing but a confirmed address. There
  is no credential database to breach.
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
| Languages | en cs de sk pl es fr it — `web/i18n/` for the site, `service/i18n.py` for the mail |
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
| Enrichment | Claude skill extraction, via a claude.ai routine (no metered API) |
| Orchestration | GitHub Actions |

```
JOB_MARKET
├── RAW            job_postings · skill_tags
├── STAGING        stg_job_postings                    (views)
├── INTERMEDIATE   int_postings_enriched · int_skill_exploded
└── MARTS
    └── ANALYSIS   fct_postings
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
-- migration needs to alter skill_tags, extend this then — don't grant
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
| National registers | **MPSV** (CZ Úřad práce open data), **Platsbanken** (SE Arbetsförmedlingen open data) |
| Czech | StartupJobs, Cocuma |
| Aggregators | The Muse, Adzuna (AT/BE/CA/DE/ES/FR/GB/IT/NL/PL/US) |
| ATS boards | Greenhouse, Lever, Ashby, SmartRecruiters, Workday, **Oracle Recruiting Cloud**, Recruitee, Workable — curated company list only |

**A national employment service publishing open data is the best source shape there is, and
Sweden is the second one** (added 2026-08-04). `platsbanken` reads Arbetsförmedlingen's
JobSearch API — no key, `robots.txt` 404, and open data the agency says is "free for anyone to
use". **15 201 white-collar ads, 100% with a company, a date and a description at a median of
3 775 characters** — the richest text in the corpus by a wide margin. Scope is SSYK occupation
field, the same call MPSV's ISCO 1–3 filter makes: seven fields of 21, excluding healthcare
(5 012), hotel and restaurant, transport, construction and the rest of the ~21 000 ads the
nine role categories cannot rank. Like MPSV it carries personal data — `application_contacts`
names a recruiter on 29% of ads — so those fields are never read and free text is scrubbed.

**Germany is the largest job source in Europe and it is excluded.** The Bundesagentur für
Arbeit's Jobbörse holds **820 599 vacancies** and answers an undocumented public API. Its
terms of use §2a(3) forbid precisely this: *"use robots, web spiders or similar technologies,
or to use existing communication or programming interfaces contrary to the BA's intended
purpose, and thus to read out content from the portal or apps for the purpose of data
collection and evaluation"*. The BA has publicly called the community-documented interface
"technisch wie rechtlich kritisch". That is the Alma Career decision again, at 55× the scale
of what Sweden adds — **do not add it because the digest looks thin.** Checked 2026-08-04.

**EURES is the biggest source in Europe after that, and it is refused on a carve-out rather
than a prohibition.** The European Labour Authority's portal answers an undocumented public API
at `europa.eu/eures/api/jv-searchengine/public/jv-search/search` — no key, **2 835 594**
vacancies, **846 936** of them in the EU-27 within ISCO major groups 1–3, with descriptions in
the list row, per-posting NUTS 3 codes and ESCO/ISCO occupation URIs that filter hierarchically.
It works, and it is the best-shaped payload probed so far. The problem is that ELA's *"Re-use is
authorised, provided that ELA is acknowledged"* runs on the Commission's reuse policy, which
licenses **content owned by the EU** and then says: *"To use or reproduce content that is not
owned by the EU, you may need to seek permission directly from the rightholders"*, and *"You may
be required to clear additional rights if a specific content depicts identifiable private
individuals."* Vacancy text is the employer's, routed through the national employment services;
ELA owns the portal, not the corpus. Both clauses bite — **27 of 50 sampled descriptions carried
a named contact's email address**. Unlike MPSV, whose metadata expressly disclaims the database
right, and Platsbanken, whose agency states the data is free for anyone to use, no publisher has
licensed *this* dataset, and there is no EURES dataset on `data.europa.eu`. It also disposes of
the tempting route to Germany: ELA's notice sends you to the rightholder, which is the
Bundesagentur, which has already said no — **an aggregator does not launder a refusal.**
Separately, `europa.eu/robots.txt` permits the paths but sets `Crawl-delay: 10` on the `*`
group, which alone puts a full pull at ~47 hours. **Reopen only on an answer from ELA**, not on
a re-reading of the same notice. Checked 2026-08-04; see `notes/2026-08-04-eures.md`.

**Oracle Recruiting Cloud reaches the large industrials, which no other adapter here did**
(added 2026-08-04). Vertiv, Honeywell, Emerson, Brembo and Cummins — 4 225 postings, 678 in the
EU-27 and 62 in Czechia and Slovakia, from plants in Nové Mesto nad Váhom, Stará Turá, Volyně,
Nišovice, Bratislava and Brno. It is the highest-quality feed in the stack: 100% of rows carry
an ISO country code and a posting date, and 85% a real description (median 401 characters,
against jobs.cz's median of 35). Unusually for an enterprise ATS it is **not** an N+1 adapter —
descriptions arrive in the list response — so a full pull is ~20 requests and 43 seconds, and
there is no keyword ceiling because there are no detail calls to bound.

**SAP SuccessFactors was checked and rejected the same day.** It is the other enterprise ATS
behind large European employers (Lidl CZ, Allegro, ZF, Brose, Deloitte), and its per-company
career sites are reachable — but the served page is a JS shell: 188 KB, 195 `<script>` tags,
424 characters of visible text and zero job ids in the HTML, behind `loginFlowRequired`.
Reading it would need a browser, and Playwright is out. Recorded so it is not re-probed.

**Slovakia has no MPSV equivalent, and that is a checked conclusion rather than an omission.**
The Slovak labour office publishes no per-vacancy open data — the national and EU catalogues
carry only Štatistický úrad SR's aggregate vacancy counts — and worki.sk (successor to ISTP,
run by TREXIMA Bratislava) states that further dissemination of its job offers without written
consent infringes copyright, the same bar that excluded Alma Career. Company ATS boards are
the only open route into Slovak inventory; that is what `workable` exists for.

**Jobs.cz and Profesia are no longer here** (2026-08-03). Both are Alma Career brands whose
terms of use forbid automated processing of their data, so the adapters were removed from
`gather()` and kept only as code; `ingestion/tests/test_source_exclusions.py` fails if either
returns. That cost 92% of Czech and 99% of Slovak inventory.

**MPSV is the replacement, and it is not the same kind of source.** It is the Czech public
employment service's own vacancy register, published as open data by the Ministry of Labour,
whose licence metadata *expressly disclaims* the sui generis database right — the exact right
at issue with Alma Career. ~39 000 vacancies nationwide, filtered at the adapter to the
~7 300 white-collar ones (ISCO major groups 1–3), 100% carrying salary. It also declares that
it contains personal data: every record names a contact person with their email and phone, and
`ingestion/sources/mpsv.py` never reads that block and scrubs contacts out of description text
as well. See the module docstring before changing any of that.

Each source subclasses `BaseSource` and implements `fetch()` / `normalize()`. `posting_id`
is `md5(url)`, so re-ingesting is an upsert that refreshes `last_seen_at`. Postings not seen
for 7 days are marked inactive rather than deleted.

The list above is what `search_jobs.gather()` actually runs. Two adapters exist in
`ingestion/sources/` and are deliberately **not** wired in: `eurojobs.py` (the RSS feed now
answers with a Cloudflare interstitial) and `linkedin.py` (the public RSS endpoint returns no
entries). Both were listed here as working sources until 2026-08-01, when each was measured
at 0 postings. They are kept as code rather than deleted because either could come back, but
a source that fetches nothing must not be advertised as coverage — that is how a gap gets
counted as filled.

---

## Layout

```
ingestion/        source adapters (BaseSource) + Snowflake load
enrichment/       Claude skill extraction + the routine file exchange
dbt/              staging → intermediate → marts
service/          JobDigest: API, matcher, digest, mailer, store, taxonomy
web/              Next.js landing + preferences/matches pages, in eight languages
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
