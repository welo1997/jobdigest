# Job Market Intelligence Pipeline

A data collection and market intelligence pipeline that ingests job postings from 5+ sources daily, stores the full history in Snowflake, enriches postings with AI-extracted skills, and sends personal job alerts via Telegram.

## Architecture

```
Sources                    Warehouse              Transform           Output
─────────                  ─────────              ─────────           ──────
Remotive (API)     ─┐
WeWorkRemotely (RSS)│
StartupJobs (API)   ├──→  Snowflake RAW  ──→  dbt Core  ──→  fct_postings (analysis)
Adzuna (API)        │     + Claude Haiku       staging/      fct_personal_matches
Greenhouse (ATS)   ─┘       skill tags         intermediate    → Telegram alerts
                          + Claude Haiku
                            personal scores
```

## Data Sources

| Source | Type | Coverage | Daily Volume |
|---|---|---|---|
| Remotive | JSON API | Remote tech jobs, global | ~20 |
| WeWorkRemotely | RSS | Remote, all categories | ~100 |
| StartupJobs.cz | JSON API | Czech startups/tech | ~500 |
| Adzuna | REST API | PL, AT, DE, NL, GB, US | ~1,800 |
| Greenhouse | ATS API | 11 target tech companies | ~2,400 |

**Total: ~4,800 postings/day**

## Tech Stack

| Layer | Tool |
|---|---|
| Ingestion | Python 3.12 — `requests`, `feedparser` |
| Warehouse | Snowflake |
| Transform | dbt Core |
| AI Enrichment | Claude Haiku — skill extraction + personal scoring |
| Orchestration | GitHub Actions — daily cron |
| Notifications | Telegram Bot API |

## Snowflake Schema

```
JOB_MARKET
├── RAW
│   ├── JOB_POSTINGS        ← immutable raw store
│   ├── SKILL_TAGS           ← AI-extracted skills per posting
│   └── PERSONAL_SCORES      ← AI personal fit scores
├── STAGING (dbt views)
├── INTERMEDIATE (dbt tables)
└── MARTS
    ├── ANALYSIS             ← fct_postings
    └── PERSONAL             ← fct_personal_matches → Telegram
```

## dbt Models

- **`stg_job_postings`** — clean/cast, `role_category`, `is_remote`, `salary_min`/`salary_max`
- **`int_postings_enriched`** — joins skills + personal scores
- **`int_skill_exploded`** — one row per (posting, skill), normalised via taxonomy
- **`fct_postings`** — full enriched fact table (incremental)
- **`fct_personal_matches`** — high-score unnotified matches → Telegram

## Setup

1. Clone the repo
2. Copy `.env.example` to `.env` and fill in credentials
3. `pip install -r requirements.txt`
4. Run `python setup_snowflake.py` to create database/schemas/tables
5. Run `python -m ingestion.run` for first ingestion
6. Run `python -m enrichment.skill_extractor` for skill extraction
7. `cd dbt && dbt seed && dbt run && dbt test`

## GitHub Actions

- **Daily Pipeline** (`pipeline.yml`): ingestion → skill extraction → personal scoring → dbt → Telegram
- **Weekly dbt Docs** (`dbt_docs.yml`): generate + publish to GitHub Pages

## Tests

```bash
# Python tests
python -m pytest ingestion/tests/ enrichment/tests/ -v

# dbt tests
cd dbt && dbt test
```

## License

Private project.
