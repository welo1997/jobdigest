# Market intelligence — the skill-extraction routine (no-API)

Enrichment runs on **Claude subscription compute via a claude.ai routine**, not on metered
API credits. This is the same shape as `deploy/matcher-routine.md`, and it exists for the
same reason: on 2026-07-20 an exhausted Anthropic credit balance took the Snowflake
pipeline down while JobDigest kept working, because the matcher already had a no-key path
and enrichment did not.

```
                postings.json                         enrichment-NNN.json
 credentialed  ───────────────▶   claude.ai routine   ───────────────▶  credentialed
 job (Snowflake) (export step)     (reads → writes)       (import step)   job (Snowflake)
```

- **Export** (needs Snowflake): `python -m enrichment.exchange --export postings.json [--limit N]`
- **Routine** (no DB, no key): reads `postings.json`, writes `enrichment-001.json`, `-002`, …
- **Import** (needs Snowflake): `python -m enrichment.exchange --import <file|glob|dir>`

One pipeline run does both ends: it imports whatever the routine produced since last time
(Step 4a, before the dbt run, so today's models are built on it), then exports a fresh
batch after ingestion (Step 9). The routine runs in between.

`ANTHROPIC_API_KEY` is **deliberately absent** from `pipeline.yml`. With no key, Step 3
(`skill_extractor`) logs a skip and exits 0, and this exchange does the work instead. Do not
add the key back — its absence is what enforces the billing decision.

## postings.json (input to the routine)

```json
{
  "generated_at": "2026-07-21T…Z",
  "instructions": "…embedded enrichment instructions…",
  "postings": [
    {"posting_id": "md5…", "title": "Data Engineer", "company": "Acme",
     "location": "Prague", "is_remote": true, "description": "…≤1500 chars…"}
  ]
}
```

Batch is `BATCH_SIZE` (300) postings, roughly 170 kB — sized to fit one routine's context.
The export selects postings with **no row in `raw.skill_tags`**. Reading a file this size is
fine; it is only the routine's *write* that is capped (see below).

## enrichment-NNN.json (output the routine writes — the import step's ONLY input)

The routine writes **several numbered parts**, not one file:

```
gdrive:JobMarket/enrichment-001.json
gdrive:JobMarket/enrichment-002.json
...
```

each of the same shape, at most ~120 postings and under 12 000 bytes:

```json
{"enrichment": [{"posting_id": "md5…", "skills": ["dbt", "snowflake", "python"]}]}
```

**Why parts.** The routine's Drive write truncates at **15 000 bytes**, silently and
mid-string. Observed 2026-07-21: a 300-posting reply was severed after exactly 50 records
and arrived as invalid JSON. Drive itself is fine — the export step pushes a 168 kB
`postings.json` through rclone without trouble — the limit is on the routine's write path.
Do not "simplify" this back to a single file.

**Skills only.** Personal scoring was retired 2026-07-21 — its only consumer was a Telegram
alert feed, replaced by using JobDigest itself as a subscriber — and the whole path was
deleted from the repo. Dropping `personal_score` and `summary` also cut the record from
~300 bytes to ~100, which is what makes the part sizes workable. Do not add a score field
back: there is nothing downstream to read it.

## What the import step enforces

These parts cross a trust boundary — written by a routine, carried through cloud
storage — so nothing in them is taken on faith (`enrichment/exchange.py`, tested in
`enrichment/tests/test_exchange.py`):

- **`posting_id` is validated against `raw.job_postings`.** An invented or stale id is
  dropped, so the routine cannot conjure rows for postings that were never ingested.
- **`skills` is coerced**, not stored as given: lowercased, trimmed, deduped, non-strings
  dropped, each capped at 60 chars, max 30. `int_skill_exploded` flattens this array and
  the marts group by its values, so one dict or one 40 kB string in here becomes bad rows
  in a fact table rather than an obvious crash.
- **A malformed record is logged and skipped**, never aborting the batch.
- **A truncated part is skipped, not fatal.** This is the expected transport failure, not a
  hypothetical. One severed part costs only its own postings; the rest of the batch — and
  the rest of the pipeline run, which still has ingestion, dbt and the marts to do — carries
  on. If *every* part is unreadable nothing was stored, and that raises.
- **Files that yield nothing raise.** Parts produced, transported and parsed yet storing zero
  rows are an outage wearing a green checkmark — the same failure mode as the
  all-postings-failed guard in `skill_extractor.py`. *Missing* files are different and are a
  clean skip: the routine simply hasn't run yet.

The MERGE is INSERT-only, matching the API path: enrichment is idempotent, so re-running
must not overwrite an answer already stored.

## Routine prompt (paste into the claude.ai routine)

> You are the job-market dataset's skill-extraction pass. Read `postings.json` from the
> Drive folder `JobMarket`. For EACH posting, extract every technical skill, tool and
> technology mentioned, as lowercase strings, normalising variants ("PostgreSQL" →
> "postgresql", "React.js" → "react"); at most 30 per posting, `[]` if none. Postings may be
> Czech, Slovak or English; treat them equally. Only use `posting_id`s present in the file;
> never invent one.
>
> Write your answer as SEVERAL numbered files in the same folder — `enrichment-001.json`,
> `enrichment-002.json`, … — each holding at most 120 postings and staying under 12 000
> bytes. **A single larger file is silently truncated in transit and arrives as invalid
> JSON.** Each file has the shape:
> `{"enrichment":[{"posting_id":"…","skills":["…"]}]}`
> Output only the files — no commentary.

The same instructions are embedded in `postings.json` itself (`ROUTINE_INSTRUCTIONS` in
`enrichment/exchange.py`), so the file is self-describing if the two ever drift.

## Setup (one-time)

1. **A Drive folder for the exchange.** `gdrive:JobMarket` — separate from JobDigest's
   `gdrive:JobDigest` folder. Leave it unshared; the routine reaches it through your own
   account's Drive connector.

2. **An `RCLONE_CONFIG` repository secret** holding an rclone config with a `gdrive` remote
   that can read and write that folder. Generate it locally with `rclone config` and paste
   the contents of `~/.config/rclone/rclone.conf`. Until this secret exists, Steps 4a and 9
   skip themselves and the pipeline stays green.

   Do not paste the config into a chat, a commit, or an issue — it contains an OAuth
   refresh token for your Google account (CLAUDE.md security rule 2).

3. **Schedule the claude.ai routine** to run between two pipeline runs, pointed at the
   folder with the prompt above.

## Privacy note — deliberately shorter than the matcher's

`postings.json` contains public job advertisements and nothing else: no subscriber, no
email, no CV summary, no credential. That is why this has no PII section comparable to
`deploy/matcher-routine.md`'s, and it is asserted by a test rather than left as a claim
(`test_export_carries_no_credentials_and_only_public_fields`).

The folder is still readable by **any** claude.ai session with the Drive connector, so the
rule that governs JobDigest's folder governs this one too: never put a database dump or a
credential in it. Keep the sharing list empty.

## The backlog is a separate job

The daily path above handles steady state — a few hundred new postings. There is also a
**14,578-posting backlog**, a one-time artefact of the 2026-07-20 database rebuild. Do not
wire it into the daily pipeline: at `BATCH_SIZE` 300 it is ~49 routine runs. Chew it with
`--export postings.json --limit N` by hand, or on its own schedule, until
`fetch_unenriched_postings` returns nothing.
