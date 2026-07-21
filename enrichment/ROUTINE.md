# Market intelligence — the enrichment routine (no-API)

Enrichment runs on **Claude subscription compute via a claude.ai routine**, not on metered
API credits. This is the same shape as `deploy/matcher-routine.md`, and it exists for the
same reason: on 2026-07-20 an exhausted Anthropic credit balance took the Snowflake
pipeline down while JobDigest kept working, because the matcher already had a no-key path
and enrichment did not.

```
                postings.json                              enrichment.json
 credentialed  ───────────────▶   claude.ai routine   ───────────────▶  credentialed
 job (Snowflake) (export step)     (reads → writes)       (import step)   job (Snowflake)
```

- **Export** (needs Snowflake): `python -m enrichment.exchange --export postings.json [--limit N]`
- **Routine** (no DB, no key): reads `postings.json`, writes `enrichment.json`
- **Import** (needs Snowflake): `python -m enrichment.exchange --import enrichment.json`

One pipeline run does both ends: it imports whatever the routine produced since last time
(Step 4a, before the dbt run, so today's models are built on it), then exports a fresh
batch after ingestion (Step 9). The routine runs in between.

`ANTHROPIC_API_KEY` is **deliberately absent** from `pipeline.yml`. With no key, Steps 3
and 4 (`skill_extractor`, `personal_scorer`) log a skip and exit 0, and this exchange does
the work instead. Do not add the key back — its absence is what enforces the billing
decision.

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

Batch is `BATCH_SIZE` (300) postings, roughly 0.5 MB — sized to fit one routine's context.
The export selects postings missing skills, a personal score, **or both**, in one query:
a posting missing only its score still needs its description sent, and sending it twice
would double the routine's reading for nothing.

## enrichment.json (output the routine writes — the import step's ONLY input)

```json
{
  "enrichment": [
    {"posting_id": "md5…", "skills": ["dbt", "snowflake", "python"],
     "personal_score": 8, "summary": "Two sentences on the fit."}
  ]
}
```

Both passes come back from **one** read of each posting. The API path makes two Haiku calls
per posting; a routine makes a handful of large-context calls instead, so combining them is
both cheaper and stops the two answers disagreeing about the same text.

## What the import step enforces

`enrichment.json` crosses a trust boundary — written by a routine, carried through cloud
storage — so nothing in it is taken on faith (`enrichment/exchange.py`, tested in
`enrichment/tests/test_exchange.py`):

- **`posting_id` is validated against `raw.job_postings`.** An invented or stale id is
  dropped, so the routine cannot conjure rows for postings that were never ingested.
- **`skills` is coerced**, not stored as given: lowercased, trimmed, deduped, non-strings
  dropped, each capped at 60 chars, max 30. `int_skill_exploded` flattens this array and
  the marts group by its values, so one dict or one 40 kB string in here becomes bad rows
  in a fact table rather than an obvious crash.
- **`personal_score` is clamped to 0–10**, and `None` is kept distinct from `0`: 0 is a real
  verdict ("not eligible"), while `None` means nothing usable came back and the row keeps
  its NULL so a later batch retries it.
- **A malformed record is logged and skipped**, never aborting the batch.
- **A file that yields nothing raises.** A batch that was produced, transported and parsed
  yet stored zero rows is an outage wearing a green checkmark — the same failure mode as
  the all-postings-failed guard in `skill_extractor.py`. A *missing* file is different and
  is a clean skip: the routine simply hasn't run yet.

Both MERGEs are INSERT-only, matching the API path: enrichment is idempotent, so re-running
must not overwrite an answer already stored.

## Routine prompt (paste into the claude.ai routine)

> You are the job-market dataset's enrichment pass. Read `postings.json` from the Drive
> folder. For EACH posting, do two things in one read. (1) SKILLS: extract every technical
> skill, tool and technology as lowercase strings, normalising variants ("PostgreSQL" →
> "postgresql", "React.js" → "react"); max 30, `[]` if none. (2) PERSONAL_SCORE: rate 0–10
> how well the role fits an early-career data professional (dbt, Snowflake, Python, SQL;
> ~1–2 years; based in Czechia/EU; wants fully-remote EU/worldwide roles, or anything in
> CZ). Score 0 ONLY for a genuine eligibility blocker — needs non-EU work authorisation,
> on-site outside CZ/EU with no remote option, security clearance, or fluency in a language
> other than English/Czech. A stack mismatch or a too-senior title is a deduction, not a
> zero. Add a 2-sentence summary of the fit. Postings may be Czech, Slovak or English;
> judge them equally. Only use `posting_id`s present in the file; never invent one. Write
> the result to `enrichment.json` in the same folder, in exactly this shape:
> `{"enrichment":[{"posting_id":"…","skills":["…"],"personal_score":<int>,"summary":"…"}]}`
> Output only the file — no commentary.

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
