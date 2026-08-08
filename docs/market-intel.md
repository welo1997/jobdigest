# Market intelligence (DECOMMISSIONED)

The Snowflake / dbt portfolio side. **Decommissioned 2026-07-22** — the `JOB_MARKET`
database, the `JOB_MARKET_CI` user and the `JOB_MARKET_ETL` role were dropped, and the two
cloud routines are paused. `COMPUTE_WH` and `JOB_MARKET_MONITOR` were kept; the warehouse is
shared with other projects. `dbt/`, `enrichment/`, `ingestion/` and the workflow code are
kept as a portfolio snapshot and no longer run.

**Do not spend effort maintaining this side.** Reviving it means re-creating the Snowflake
objects (`setup_snowflake.py`) and re-enabling the routines — at which point the rules below
apply again.

## Design

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


## Snowflake least privilege

These were security rules 6-8. They bind again only if the Snowflake side is revived.

**A scoped role granted to a personal admin login does not restrict anything.**
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

## Snowflake operational notes

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
