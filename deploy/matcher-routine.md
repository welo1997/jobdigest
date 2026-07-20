# JobDigest — the daily matching routine (no-API, free tier)

Free-tier matching runs inside a **claude.ai cloud routine** so it uses Claude subscription
compute, not metered API credits. The routine sandbox has only a git checkout + Google
connectors — **no DB access, no API key** — so it works purely on two JSON files:

```
                shortlists.json                              picks.json
 credentialed  ───────────────▶   claude.ai routine   ───────────────▶  credentialed
 job (has DB)   (export step)      (reads → picks)       (import step)     job (has DB)
```

- **Export** (needs DB): `python -m service.matcher --export shortlists.json`
- **Routine** (no DB, no key): reads `shortlists.json`, writes `picks.json`
- **Import** (needs DB): `python -m service.matcher --import picks.json`
- **Send** (needs DB): `python -m service.pipeline`   (reads picks from `matches`, emails)

The credentialed steps run wherever the DB is reachable (VPS cron, or GitHub Actions if the
DB is Supabase). The routine only ever touches the two files.

## shortlists.json (input to the routine)

```json
{
  "generated_at": "2026-07-19T…Z",
  "instructions": "…embedded matcher instructions…",
  "profiles": [
    {
      "profile_id": "uuid",
      "email": "…",
      "profile": {"label": "...", "role_categories": ["product"], "stack": ["figma","sql"],
                  "seniorities": ["mid"], "regions": ["cz"], "work_types": ["permanent"],
                  "sectors": ["ecommerce"], "years_experience": 3, "cv_summary": "..."},
      "candidates": [
        {"posting_id": "md5…", "title": "...", "company": "...", "location": "...",
         "region": "cz", "seniority": "mid", "work_type": "permanent",
         "salary": "45 000 – 90 000 Kč", "description": "…≤320 chars…"}
      ]
    }
  ]
}
```

## picks.json (output the routine writes — the import step's ONLY input)

```json
{
  "picks": [
    {"profile_id": "uuid",
     "jobs": [{"posting_id": "md5…", "score": 9, "reason": "one sentence why it fits"}]}
  ]
}
```

Rules the import step enforces (so the routine can't break the DB): unknown or inactive
`posting_id`s are dropped, `score` is clamped 0–10, anything below the floor (`MATCH_FLOOR`,
default **4**) is dropped. `reason` is truncated to 280 chars. The email shows only the strong
picks (`digest.EMAIL_MIN_SCORE`, default 6); the 4–5s appear on the subscriber's `/matches`
web page.

## Routine prompt (paste into the claude.ai routine)

> You are JobDigest's daily job matcher. Read `shortlists.json`. For EACH subscriber, read
> their `profile` and their `candidates`, and pick the postings that genuinely fit that
> specific person — weigh the whole context (role type, seniority, skills/stack, work setup,
> location/region, sector interest), not just keyword overlap. Postings may be in Czech,
> Slovak, or English; judge them equally (a "Vývojář" is a developer, "Obchodní zástupce" a
> sales rep, "Účetní" an accountant). Score each posting 0–10 on overall fit (9–10 excellent,
> 6–7 solid, 4–5 plausible-but-weaker) — be honest, use the full range. Include every posting
> scoring 4+ (skip only clear non-fits); a later step decides which get emailed vs shown on the
> web page. Order best-first; at most 20 per subscriber. Only use `posting_id`s present in the
> file; never invent one. Write the result to `picks.json` in exactly this shape:
> `{"picks":[{"profile_id":"…","jobs":[{"posting_id":"…","score":<int>,"reason":"…"}]}]}`
> Output only the file — no commentary.

## PII note

`shortlists.json` contains subscriber emails + CV summaries. **Do not commit it to the
public repo** (`exchange/` is gitignored). It travels via a private Google Drive folder.

## VPS setup (one-time)

The DB-credentialed phases run on the VPS (the Postgres is compose-internal, not reachable
from GitHub Actions). Two systemd timers bracket the routine; `rclone` moves the JSON.

1. **Install rclone + authorise Google Drive** (interactive, on the VPS as `deploy`):
   ```bash
   sudo apt-get install -y rclone
   rclone config          # n) new remote, name: gdrive, type: drive, follow the OAuth flow
   rclone mkdir gdrive:JobDigest
   ```
   Share that `JobDigest` Drive folder with the Google account the claude.ai routine uses,
   so the routine can read `shortlists.json` and write `picks.json` in the same folder.

2. **Install the units + script:**
   ```bash
   cd /opt/jobdigest
   chmod +x deploy/jobdigest-match.sh
   sudo cp deploy/jobdigest-match-*.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now jobdigest-match-export.timer jobdigest-match-import.timer
   # retire the old single send timer (it sent without AI matching):
   sudo systemctl disable --now jobdigest-pipeline.timer 2>/dev/null || true
   ```
   (Requires the redeploy that ships `service/matcher.py`, the `exchange` volume, and
   migration 004 — rebuild the pipeline image: `sudo docker compose build pipeline`.)

3. **Schedule the claude.ai routine** for ~06:00 UTC (between the 05:00 export and 07:00
   import; claude.ai cron minimum is 1h). Point it at the shared Drive folder with the
   prompt above.

### Daily timeline (UTC)

| Time  | Where            | What |
|-------|------------------|------|
| 05:00 | VPS (export)     | ingest + write `shortlists.json` → Drive |
| ~06:00| claude.ai routine| read shortlists → write `picks.json` → Drive |
| 07:00 | VPS (import)     | pull picks → load `matches` → send digests |

Run a phase by hand any time: `deploy/jobdigest-match.sh export` / `import`.

## Premium / paid tier (metered API, optional)

Paid users can skip the routine and match inline with the API:
`docker compose run --rm pipeline python -m service.pipeline --ingest --cz --match`
(needs `ANTHROPIC_API_KEY` in `deploy/.env`; `MATCHER_MODEL` defaults to `claude-haiku-4-5`).
