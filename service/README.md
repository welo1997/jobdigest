# Job & Freelance Matcher — service (Phase 1)

Turns the on-demand `search_jobs.py` script into a multi-user, API-driven service.
Reuses the existing `ingestion/` sources and `search_jobs` classifiers as the single
source of truth for how a posting is normalised and labelled.

```
ingestion/ sources ──► service/ingest.py ──► Postgres (postings)
                                                  │
                          service/api.py (FastAPI) ── /matches, /search, /profiles
                                                  │
                          service/scoring.py  (heuristic now, Haiku in Phase 2)
```

## Data model (`db/schema.sql`, Supabase-compatible)
- **postings** — one enriched row per job, deduped on `posting_id`; classification
  columns (`role_category, region, eligibility, seniority, work_type, is_part_time`)
  computed at ingest so `/matches` is a cheap SQL query. `last_seen_at` + `is_active`
  handle freshness / expired-listing drop at scale.
- **profiles** — a user's saved search + fit criteria (stack, seniorities, regions,
  work_types, sectors, part_time_only, eligible_only, min_score).
- **matches** — cached per-(profile, posting) fit score + tracking status
  (`new/saved/dismissed/applied`), so a pair is never re-scored.

## Run locally

```bash
# 1. Postgres (schema auto-applied on first boot)
docker compose -f service/db/docker-compose.yml up -d
export DATABASE_URL="postgresql://jobmatch:jobmatch@localhost:5433/jobmatch"

# 2. deps
pip install -r service/requirements.txt

# 3. ingest (add --cz for Czech sources)
python -m service.ingest

# 4. API
uvicorn service.api:app --reload --port 8099
```

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status + active posting count |
| GET | `/search?region=eu&part_time_only=true&…` | ad-hoc filtered postings (anonymous) |
| POST | `/profiles` | create a search profile |
| GET | `/profiles/{id}` | fetch a profile |
| GET | `/matches?profile_id=…` | prefilter → score → ranked matches |
| POST | `/matches/{profile_id}/{posting_id}/status` | save / dismiss / applied |

## Cost strategy (why it scales multi-tenant)
1. Classify + (Phase 2) extract skills **once per posting** at ingest.
2. `/matches` runs a **cheap SQL prefilter**, ranks the whole set with the free
   `heuristic_score`, and only the top-N per profile ever hit the LLM.
3. LLM scores are **cached in `matches`** keyed by (profile, posting).

## Roadmap
- **Phase 2** — implement `scoring.haiku_score` (single Haiku call, cached), skill
  extraction at ingest, optional pgvector embeddings for similarity prefilter.
- **Phase 3** — Next.js web: onboarding + shortlist + Supabase auth (swap `DEV_USER_ID`
  for `auth.uid()` + row-level security).
- **Phase 4** — daily email digest (Resend) + scheduled ingest (GitHub Action / Supabase cron).
- **Phase 5** — Stripe billing + freemium gating.

## Deploy → Supabase
`db/schema.sql` applies unchanged. Point `DATABASE_URL` at the Supabase connection
string; in production `profiles.user_id` references `auth.users(id)` and RLS restricts
rows to the owner. The API runs on Fly.io / Render; ingest runs as a scheduled job.
```
