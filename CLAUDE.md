# CLAUDE.md — Tech Job Market Intelligence Pipeline

## Project overview

A **data collection and market intelligence pipeline** that ingests all job postings
at tech companies from 10+ sources (Czech Republic, Europe, US) on a daily schedule,
stores the full history in Snowflake, and builds a broad dataset for statistical
analysis — with a personal job alert layer on top as a filtered view.

**Two distinct purposes, one pipeline:**

1. **Market intelligence** (primary): collect, store, and analyse the tech job
   market over time. Trends in skill demand, salary distributions, stack
   co-occurrence, posting volume by source/country/role. Public portfolio piece.

2. **Personal job alerts** (secondary): a filtered mart on top of the same data
   that scores postings for personal relevance and sends Telegram notifications
   for early-career Data Engineer / Analytics Engineer / Data Analyst / ML roles.
   Work setup (remote / on-site CZ / hybrid) is not filtered — only role category and score.

Built to demonstrate: multi-source ingestion, Snowflake warehousing, dbt Core
modelling (Kimball-style layering), Claude API enrichment, GitHub Actions CI/CD.

---

## Goals

1. Ingest **all roles at tech companies** daily — broadest possible net, minimal
   filtering at source; include engineering, product, design, ops, finance, marketing
   roles if they appear at tech companies
2. Store full history in Snowflake; never delete raw rows
3. Extract and normalise skills/tools from descriptions via Claude API for all postings
4. Keep analysis marts deliberately minimal during collection phase (Phases 1–10) —
   placeholder `fct_postings` only; full analysis layer designed after 4–8 weeks
   of real data when patterns are visible
5. Personal alert layer: score data/engineering roles for personal fit → Telegram
6. Public portfolio: clean repo, README, dbt docs, CI badge

---

## Tech stack

| Layer | Tool |
|---|---|
| Ingestion | Python 3.11 — `feedparser`, `requests`, `beautifulsoup4` |
| Warehouse | Snowflake — new free trial account, DB: `JOB_MARKET` |
| Transform | dbt Core (open source) |
| AI enrichment | Claude API — `claude-haiku-4-5` for both skill extraction (high volume) and personal scoring (Haiku produces well-calibrated scores at 10× lower cost than Sonnet) |
| Orchestration | GitHub Actions — daily cron, weekdays 07:00 UTC |
| Notification | Telegram Bot API via `python-telegram-bot` |
| Visualisation | Metabase (self-hosted Docker, same pattern as Footshop) |
| Secrets | GitHub Actions secrets — never committed |
| Testing | dbt schema tests + `pytest` for ingestion + enrichment |

---

## What counts as "tech-related"

**Scope: all roles at tech companies** — not just engineering titles.

The dataset captures every role posted by tech companies (software, SaaS, fintech,
e-commerce, data/AI companies etc.). This means engineering, product, design, data,
marketing, sales, ops, finance, HR — if the company is a tech company, the posting
is in scope. Filtering is done at the company-type level, not the role title level.

At source, only exclude clearly non-tech industries (retail, hospitality, healthcare,
manufacturing) using `seeds/industry_blocklist.csv`. When in doubt, keep the posting.

**Role categories** (assigned in `stg_job_postings` for segmentation, not filtering):

| `role_category` | Example titles |
|---|---|
| `data_engineering` | Data Engineer, Analytics Engineer, DataOps |
| `data_analysis` | Data Analyst, BI Analyst, Business Intelligence |
| `machine_learning` | ML Engineer, AI Engineer, Data Scientist |
| `software_engineering` | Software Engineer, Backend, Frontend, Fullstack, Mobile |
| `devops_platform` | DevOps, Platform Engineer, SRE, Cloud Engineer |
| `product` | Product Manager, Product Owner, TPM |
| `design` | UX Designer, Product Designer, UI Engineer |
| `other_tech_function` | Marketing, Sales, Finance, HR, Legal at tech companies |
| `uncategorised` | Did not match any pattern — kept, never dropped |

No rows are ever dropped based on `role_category`. All categories feed the
collection dataset. Only the personal alert mart filters on category.

**Why collect all roles at tech companies (not just tech roles):**
- Cross-functional hiring patterns reveal company growth signals
- Non-engineering roles at tech companies still use technical tooling (SQL, Figma, Salesforce)
- Broader dataset = more interesting statistical surface area once analysis starts

---

## Repository structure

```
job-market-intelligence/
│
├── CLAUDE.md
├── README.md                             ← public-facing, with findings + architecture
├── requirements.txt
├── .env.example
├── .gitignore
│
├── .github/
│   └── workflows/
│       ├── pipeline.yml                  ← daily ingestion + enrichment + dbt + notify
│       └── dbt_docs.yml                  ← weekly: generate + publish to GitHub Pages
│
├── ingestion/
│   ├── __init__.py
│   ├── base.py                           ← abstract BaseSource(fetch, normalize)
│   ├── load.py                           ← MERGE upsert into raw.job_postings
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── remotive.py                   ← RSS /feed/all, all tech categories
│   │   ├── weworkremotely.py             ← RSS /remote-jobs/feeds/all
│   │   ├── adzuna.py                     ← REST API: CZ, DE, NL, GB, US
│   │   ├── jobscz.py                     ← Jobs.cz RSS
│   │   ├── startupjobs.py                ← Startupjobs.cz RSS
│   │   ├── profesia.py                   ← Profesia.cz RSS (CZ/SK)
│   │   ├── linkedin.py                   ← LinkedIn public RSS, f_WT=2
│   │   ├── greenhouse.py                 ← Greenhouse ATS board scraper
│   │   ├── lever.py                      ← Lever ATS board scraper
│   │   └── eurojobs.py                   ← EuroJobs RSS
│   └── tests/
│       ├── test_remotive.py
│       ├── test_adzuna.py
│       └── test_load.py
│
├── enrichment/
│   ├── __init__.py
│   ├── skill_extractor.py                ← Haiku: extract skill list from description
│   ├── personal_scorer.py                ← Sonnet: personal fit score + summary
│   └── tests/
│       ├── test_skill_extractor.py
│       └── test_personal_scorer.py
│
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml.example
│   ├── packages.yml
│   ├── models/
│   │   ├── staging/
│   │   │   ├── stg_job_postings.sql
│   │   │   └── stg_job_postings.yml
│   │   ├── intermediate/
│   │   │   ├── int_postings_enriched.sql  ← joins skill_tags + personal_scores
│   │   │   ├── int_postings_enriched.yml
│   │   │   ├── int_skill_exploded.sql     ← one row per skill per posting
│   │   │   └── int_skill_exploded.yml
│   │   └── marts/
│   │       ├── analysis/
│   │       │   ├── fct_postings.sql           ← full enriched fact table
│   │       │   ├── fct_postings.yml
│   │       │   ├── agg_skill_demand.sql        ← skill counts by week/country/category
│   │       │   ├── agg_salary_stats.sql        ← salary percentiles by role/country
│   │       │   └── agg_stack_cooccurrence.sql  ← skill pair frequency matrix
│   │       └── personal/
│   │           ├── fct_personal_matches.sql
│   │           └── fct_personal_matches.yml
│   ├── seeds/
│   │   ├── score_weights.csv             ← personal scoring keyword weights
│   │   ├── target_companies.csv          ← companies for ATS scraping
│   │   ├── industry_blocklist.csv        ← non-tech industries to skip at source
│   │   ├── company_taxonomy.csv          ← known tech companies + industry tag
│   │   └── skill_taxonomy.csv            ← canonical skill names (normalise variants)
│   ├── macros/
│   │   ├── keyword_score.sql
│   │   └── parse_salary.sql
│   └── tests/
│       └── generic/
│           └── assert_score_range.sql
│
└── notify.py                             ← reads fct_personal_matches → Telegram
```

---

## Snowflake schema

### Database: `JOB_MARKET`

```
JOB_MARKET
├── RAW
│   ├── JOB_POSTINGS          ← immutable raw store, upsert on posting_id
│   ├── SKILL_TAGS            ← Haiku-extracted skills array, one row per posting
│   └── PERSONAL_SCORES       ← Sonnet personal fit scores, one row per posting
├── STAGING        (dbt)
├── INTERMEDIATE   (dbt)
└── MARTS          (dbt)
    ├── ANALYSIS              ← statistical portfolio marts
    └── PERSONAL              ← personal job alert mart
```

### `raw.job_postings`

```sql
CREATE TABLE raw.job_postings (
    posting_id       VARCHAR        NOT NULL PRIMARY KEY,  -- md5(url)
    source           VARCHAR        NOT NULL,
    title            VARCHAR,
    company          VARCHAR,
    url              VARCHAR,
    description      TEXT,
    location         VARCHAR,
    country_code     VARCHAR(2),    -- best-effort from location
    remote_signal    BOOLEAN,       -- TRUE if source is inherently remote
    salary_raw       VARCHAR,       -- unparsed as seen in posting
    currency         VARCHAR,
    posted_at        DATE,
    loaded_at        TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP,
    notified         BOOLEAN        DEFAULT FALSE,
    notified_at      TIMESTAMP_NTZ
);
```

### `raw.skill_tags`

```sql
CREATE TABLE raw.skill_tags (
    posting_id       VARCHAR        NOT NULL PRIMARY KEY,
    skills           ARRAY,         -- e.g. ['dbt', 'snowflake', 'python']
    raw_response     TEXT,          -- full Claude response for debugging
    extracted_at     TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP
);
```

### `raw.personal_scores`

```sql
CREATE TABLE raw.personal_scores (
    posting_id       VARCHAR        NOT NULL PRIMARY KEY,
    personal_score   INTEGER,       -- 0–10
    summary          TEXT,          -- 2-sentence fit explanation
    scored_at        TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP
);
```

---

## Enrichment architecture

Two separate Claude API enrichment passes, run sequentially after ingestion:

### 1. Skill extraction — Haiku (all new postings)

Runs on every new `raw.job_postings` row without a matching `raw.skill_tags` row.
Haiku chosen for cost — structured extraction is low-ambiguity.

**Prompt:**
```
Extract all technical skills, tools, and technologies from this job posting.
Return ONLY a JSON array of lowercase strings. Normalise variants
(e.g. "PostgreSQL" → "postgresql", "React.js" → "react"). Max 30 items.
If none found, return [].

Title: {title}
Description: {description[:1500]}
```

**Cost:** ~300 tokens/call × $0.0008/1K ≈ $0.00024/posting.
100–200 new postings/day ≈ **$0.03–0.05/day**.

### 2. Personal fit scoring — Haiku (data roles, unscored)

Runs where `role_category IN ('data_engineering','data_analysis','machine_learning')`
AND no `personal_scores` row yet AND `description IS NOT NULL`. Work setup is **not**
filtered — remote, on-site CZ, and hybrid all flow through the scorer; the prompt
treats relocation outside CZ/EU as a soft factor. Haiku produces well-calibrated
scores with explicit soft-factor reasoning and matches Sonnet quality on this task.

**Prompt** (lives in `enrichment/personal_scorer.py:PROMPT_TEMPLATE`):
```
Score this job posting for an early-career data professional with strong fundamentals
in dbt, Snowflake, Python, and SQL — growing into mid-level. Open to stretch roles
where the stack matches even if the seniority is one step above current level.

Candidate profile:
- Core stack: dbt, Snowflake, Python, SQL (production experience)
- Working knowledge: Docker, Metabase, Google Sheets
- Strong interest in AI/ML tooling; learns quickly
- Based in Czech Republic; remote, on-site in CZ, hybrid in CZ are all fine
- ~1-2 years professional experience; credible growth into mid-level
- Open to full-time or freelance/contract
- Languages: English (fluent), Czech (native)
- Background in ecommerce (Footshop)
- Bonus alignment: trading firms or ecommerce companies

Hard dealbreakers (score 0 only if these apply):
- Requires security clearance
- Requires native-level fluency in a language other than English/Czech
- Requires relocation outside the Czech Republic / EU

Soft factors (reduce the score, do NOT zero it):
- Posted as 5+ years required: -2 to -3
- Stack mismatch with core stack: -2 to -4
- Lead / Manager / Principal title with little hands-on: -2 to -3
- Senior IC with strong stack overlap: -0 to -2

Rate 0-10. Senior IC + perfect stack overlap can score 6-8. Reserve 9-10 for near-perfect mid-level matches.
Respond ONLY in JSON: {"personal_score": <int>, "summary": "<2 sentences>"}
```

**Cost:** ~700 tokens/call × $0.0008/1K ≈ $0.00056/posting.
20–80 relevant postings/day ≈ **$0.01–0.05/day**.

Both scripts must be **idempotent** — re-running must not insert duplicates.
JSON parse errors must be caught, logged, and skipped without crashing the run.

---

## dbt models

### `stg_job_postings`
- Clean, cast, `is_remote` flag, `role_category` via CASE on title, `country_code`
- Salary: `salary_min` / `salary_max` from `salary_raw` via `parse_salary` macro
- `is_tech_company`: TRUE if company appears in `company_taxonomy.csv` seed OR
  if source is Remotive/WeWorkRemotely/Startupjobs (inherently tech-focused)
- No rows dropped — all categories and all companies pass through staging
- Materialised as VIEW

### `int_postings_enriched`
- Left join with `raw.skill_tags` (LATERAL FLATTEN → comma-separated string)
- Left join with `raw.personal_scores`
- Adds `has_skills` BOOLEAN, `skills_count` INT
- Materialised as TABLE

### `int_skill_exploded`
- LATERAL FLATTEN of `raw.skill_tags.skills` ARRAY → one row per (posting, skill)
- Join with `skill_taxonomy.csv` seed to normalise variant names
- Backbone for all future analysis aggregations
- Materialised as TABLE, incremental on `extracted_at`

### `marts/analysis/fct_postings` — **collection phase placeholder**
- One row per posting, all enriched fields + week/month date dimensions
- Materialised as TABLE, incremental on `loaded_at`
- This is the only analysis mart built during Phases 1–10
- **All other analysis aggregations (skill demand, salary stats, co-occurrence,
  company hiring trends etc.) are deferred to Phase 11**, designed after 4–8 weeks
  of real data. Do not build `agg_*` models speculatively — let the data shape them.

### `marts/personal/fct_personal_matches`
- Filter: `personal_score >= var('personal_score_threshold', 5) AND notified = FALSE`
  AND `role_category IN ('data_engineering','data_analysis','machine_learning')`
- No `is_remote` filter — work setup isn't a hard requirement; the scorer's prompt handles location nuance
- Ordered by `personal_score DESC, posted_at DESC`

---

## Telegram notification format

```
🟢 Personal match  ·  score: 8/10

💼 Analytics Engineer — Kiwi.com
🌐 startupjobs  ·  CZ  ·  posted 2026-04-17
🛠 dbt · snowflake · python · airflow
💰 80,000 – 110,000 CZK

🤖 Strong fit: uses your exact dbt + Snowflake stack in a data platform context.
   Fully remote with Prague office optional — matches your preference.

🔗 https://startupjobs.cz/job/12345
```

After sending, `notify.py` batch-updates `notified = TRUE` for all sent IDs.

---

## GitHub Actions workflows

### `pipeline.yml` — daily, Mon–Fri 07:00 UTC

Steps in order:
1. Ingest all sources → `raw.job_postings`
2. Skill extraction (Haiku) → `raw.skill_tags`
3. Personal scoring (Sonnet) → `raw.personal_scores`
4. `dbt run` — all models
5. `dbt test`
6. `notify.py` — Telegram alerts

Secrets required: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`,
`ADZUNA_APP_ID`, `ADZUNA_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_TOKEN`,
`TELEGRAM_CHAT_ID`

### `dbt_docs.yml` — weekly, Sunday 10:00 UTC

`dbt docs generate` → publish `dbt/target/` to GitHub Pages via
`peaceiris/actions-gh-pages`. Requires `GITHUB_TOKEN` (automatic).

---

## Environment variables

```bash
# .env.example
SNOWFLAKE_ACCOUNT=            # e.g. abc12345.eu-central-1
SNOWFLAKE_USER=
SNOWFLAKE_PASSWORD=
SNOWFLAKE_DATABASE=JOB_MARKET
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ROLE=

ADZUNA_APP_ID=                # developer.adzuna.com (free)
ADZUNA_API_KEY=

ANTHROPIC_API_KEY=            # console.anthropic.com

TELEGRAM_TOKEN=               # from @BotFather
TELEGRAM_CHAT_ID=             # send /start to bot, call getUpdates API

PERSONAL_SCORE_THRESHOLD=7    # min Sonnet score for Telegram alert
```

---

## Build order for Claude Code

### Phase 1 — Repo scaffold
- [ ] `git init job-market-intelligence`, create GitHub repo
- [ ] `.gitignore`: Python cache, dbt `target/`, `.env`, `profiles.yml`
- [ ] `requirements.txt` pinned: `feedparser requests beautifulsoup4
      snowflake-connector-python python-dotenv dbt-snowflake anthropic
      python-telegram-bot pytest pytest-mock black ruff`
- [ ] `.env.example`, `README.md` skeleton

### Phase 2 — Snowflake setup
- [ ] Create trial account (EU — AWS Frankfurt preferred)
- [ ] DDL: database `JOB_MARKET`, schemas `RAW / STAGING / INTERMEDIATE /
      MARTS_ANALYSIS / MARTS_PERSONAL`
- [ ] DDL: `raw.job_postings`, `raw.skill_tags`, `raw.personal_scores`
- [ ] Service user + role + grants (no SYSADMIN)
- [ ] Verify Python connection

### Phase 3 — Ingestion: BaseSource + Remotive + Adzuna
- [ ] `ingestion/base.py` — abstract `BaseSource` with `fetch()`, `normalize()`,
      `source_name` property, common `posting_id` generation (md5 of URL)
- [ ] `ingestion/sources/remotive.py` — RSS, `/feed/all`
- [ ] `ingestion/sources/adzuna.py` — REST API, loop CZ/DE/NL/GB/US
- [ ] `ingestion/load.py` — MERGE upsert on `posting_id`
- [ ] pytest green, manual Snowflake verify

### Phase 4 — Ingestion: remaining sources
- [ ] `jobscz.py`, `startupjobs.py`, `profesia.py`
- [ ] `weworkremotely.py`, `linkedin.py`, `eurojobs.py`
- [ ] `greenhouse.py`, `lever.py`
- [ ] Populate `target_companies.csv` (20 companies) and `role_blocklist.csv`
- [ ] Each source: pytest before wiring into `load.py`

### Phase 5 — dbt: staging
- [ ] `dbt init`, `profiles.yml.example` using `env_var()` for all creds
- [ ] `stg_job_postings`: clean + cast, `is_remote`, `role_category`, `country_code`,
      `salary_min`/`salary_max` via `parse_salary` macro
- [ ] Seeds: `skill_taxonomy.csv`, `score_weights.csv`, `role_blocklist.csv`
- [ ] Schema tests: `not_null` + `unique` on `posting_id`
- [ ] `dbt run && dbt test` green

### Phase 6 — Enrichment: skill extraction (Haiku)
- [ ] `enrichment/skill_extractor.py`:
  - Query new postings (no skill_tags row)
  - Batch 50 at a time, call Haiku, parse JSON array
  - Catch parse errors, log + skip
  - MERGE into `raw.skill_tags`
- [ ] pytest with mocked Anthropic client
- [ ] Manual run + spot-check extracted skills

### Phase 7 — dbt: intermediate + analysis marts
- [ ] `int_postings_enriched`, `int_skill_exploded`
- [ ] `fct_postings` (incremental), `agg_skill_demand`, `agg_salary_stats`,
      `agg_stack_cooccurrence`
- [ ] All dbt tests green

### Phase 8 — Enrichment: personal scoring (Sonnet)
- [ ] `enrichment/personal_scorer.py`:
  - Query data-category remote postings without scores
  - Call Sonnet, parse `{personal_score, summary}`
  - MERGE into `raw.personal_scores`
- [ ] pytest with mocked client
- [ ] Manual run, review score quality on real postings

### Phase 9 — Personal mart + Telegram
- [ ] `fct_personal_matches` — score ≥ threshold, unnotified, remote
- [ ] `notify.py` — query mart, format, send, batch-UPDATE notified
- [ ] Test with manually inserted row

### Phase 10 — GitHub Actions
- [ ] `pipeline.yml` — full 6-step daily workflow
- [ ] `dbt_docs.yml` — weekly docs → GitHub Pages
- [ ] Add all secrets
- [ ] `workflow_dispatch` end-to-end test
- [ ] Enable cron

### Phase 11 — Analysis layer (deferred — start after 4–8 weeks of data)

Do not build this phase speculatively. After collecting 4–8 weeks of postings,
explore `fct_postings` and `int_skill_exploded` in Snowflake / Metabase first,
then design the aggregation models based on what's actually interesting in the data.

Possible directions (decide after exploration, not before):
- Skill demand trends over time by role category and country
- Salary percentile distributions by role and geography
- Skill co-occurrence / stack clustering (what tools appear together)
- Company hiring velocity (which companies are growing their tech teams)
- Remote vs on-site ratio trends by country
- CZ market vs EU vs US demand patterns for data roles

Build process for each chosen analysis:
- [ ] Explore raw data in Snowflake — write exploratory SQL, find the interesting signal
- [ ] Design dbt model in `marts/analysis/` that captures it cleanly
- [ ] Build Metabase chart or dashboard on top
- [ ] Write 2–3 sentence finding for README
- [ ] Commit with `analysis:` prefix

Full `README.md` with findings, architecture diagram, and dashboard screenshots
should be written as findings accumulate — not upfront.

Tag `v1.0.0` once the pipeline is stable and at least 3 analysis findings are documented.

---

## Coding conventions

- Python: type hints on all public functions, `black`, `ruff`
- Each source subclasses `BaseSource` — no ad-hoc scripts
- Credentials via env vars only, `python-dotenv` for local
- dbt naming: `stg_` / `int_` / `agg_` / `fct_` — no exceptions
- No hardcoded thresholds or weights — seeds and env vars only
- Enrichment scripts must be idempotent: safe to re-run at any time
- Claude API: validate JSON structure, log + skip errors, never crash pipeline
- Commits: conventional (`feat:` `fix:` `chore:` `docs:` `analysis:`)

---

## Known constraints and decisions

- **Scope is company-type, not role-type**: include all roles at tech companies.
  Exclude non-tech industries via `industry_blocklist.csv`. When in doubt, keep
  the posting — over-collection is better than under-collection for a dataset project.
- **`company_taxonomy.csv`**: starts with known tech companies from target list.
  Will grow organically as new companies appear in the dataset.
- **Analysis deferred**: no `agg_*` dbt models are built until Phase 11, after
  4–8 weeks of real data. The architecture supports any aggregation direction;
  don't pre-commit to specific analyses before seeing what the data contains.
- **`fct_postings` is the collection-phase anchor**: everything in Phases 1–10
  is oriented toward getting clean, enriched rows into this mart reliably.
- **Adzuna 250 req/day**: CZ(40) + DE(50) + NL(30) + GB(50) + US(80) = 250.
  Use broad search terms (`engineer`, `developer`, `designer`, `manager`) per country.
- **Haiku for skills**: structured extraction is low-ambiguity. Performs
  equivalently to Sonnet at 10× lower cost. ~$0.03–0.05/day at current volume.
- **Sonnet for personal scoring**: reasoning quality matters for nuanced fit
  assessment. Volume (~30–50/day) keeps cost negligible (~$0.04–0.09/day).
- **LinkedIn**: RSS only, `f_WT=2` for remote. No Playwright — account ban risk.
- **Greenhouse/Lever**: `target_companies.csv` only, no domain-wide crawls.
- **Salary coverage**: expect ~30–40% non-NULL. No rows dropped for missing salary.
- **`uncategorised` rows**: kept in dataset. Useful for auditing role taxonomy.
- **Metabase**: local Docker only. Not publicly hosted. Portfolio evidence via
  screenshots and dbt docs on GitHub Pages.
- **Personal alerts share the collection pipeline**: personal mart is a filtered
  view, not a separate project. No code duplication.
- **dbt Core only**: no dbt Cloud. All orchestration via GitHub Actions.
