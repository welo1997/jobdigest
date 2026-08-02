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
| Output | `fct_postings` (analysis dataset) | Daily digest email, jobdigest.eu |
| Blast radius if broken | A stale dataset | Real people's inboxes and personal data |

Shared: `ingestion/` (source adapters), `search_jobs.py` (classifiers),
`service/taxonomy.py` (role taxonomy). Nothing else is shared — Snowflake code must never
reach into `service/`, and JobDigest must never require Snowflake.

> **Status (2026-07-22): the market-intelligence / Snowflake side is DECOMMISSIONED.** The
> `JOB_MARKET` database, the `JOB_MARKET_CI` user and `JOB_MARKET_ETL` role were dropped, and
> the two cloud routines (pipeline trigger, skill extraction) are paused. `COMPUTE_WH` and
> `JOB_MARKET_MONITOR` were kept — the warehouse is shared with other projects. The `dbt/`,
> `enrichment/`, `ingestion/` and workflow code is **kept as a portfolio snapshot** and no
> longer runs; `dbt_docs.yml`'s schedule is disabled. **JobDigest is the sole live system.**
> To revive market-intel you'd re-create the Snowflake objects (`setup_snowflake.py`) and
> re-enable the routines. Do not spend effort maintaining this side — see the memory note.

**JobDigest handles real personal data.** Treat changes there with the care that implies.

```
ingestion/        BaseSource adapters, Snowflake load, migrations
enrichment/       Claude skill extraction + the routine file exchange (Snowflake side)
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
09:00 UTC  watchdog digest_runs → alert if any subscriber has had nothing for 3 days
03:30 UTC  backup   pg_dump → encrypt → off-box
```

Retrieve-then-rerank: a cheap full-text prefilter builds a ~120-posting shortlist per
subscriber, then one Claude call reads the whole shortlist in context and picks what fits.
Cost scales with subscribers, not inventory. Candidates go to the model by integer index —
posting IDs never round-trip — and invented indices are dropped.

Picks ≥ `MATCH_FLOOR` (4) are stored; only ≥ `EMAIL_MIN_SCORE` (6) are emailed. The rest
show on the subscriber's `/matches` page. `digest_sends` guarantees a job is never emailed
twice and is never pruned.

**One job, one email — even under several `posting_id`s.** `digest_sends` keys on
`posting_id`, so it cannot see a cross-source duplicate or a jobs.cz relist (the same ad
re-posted under a fresh id). `build_digest` therefore also collapses on a normalised
**(company, title, city)** — `digest.dedupe_key`, folding diacritics, trailing legal forms
and bracketed trailers. Four properties are load-bearing:

- **It runs before the limit**, so a duplicate costs no slot.
- **The seen-set is pre-seeded from `digest_sends`** joined back to `postings`, bounded by
  `DIGEST_REPEAT_WINDOW_DAYS` (90). Without this the fix does nothing for the real cases:
  both repeats found in production were *cross-day*, and by the time the relisted id
  appears the original is already filtered by `already_sent_ids`.
- **City is part of the identity, but an unknown city is a wildcard.** 1 438 active
  postings sit in 641 same-company-same-title groups, nearly all one role advertised
  across many towns (ČSOB's in 10) — keying without city would delete those and suppress
  them for the whole window. But two *known* cities that differ mean two jobs; if either
  is unknown, fall back to (company, title). Of the two real repeats, one was
  prague/prague and the other null/prague, so neither half is optional.
- **An empty key — missing company or title — is always unique, never a match.**
- **`/matches` is deliberately NOT deduplicated** and stays the complete record. That is
  what makes suppression safe: nothing vanishes, it just isn't emailed twice.

### Nothing a subscriber states may vanish, and no subscriber may starve silently

The taxonomy models nine role categories. Subscribers are not obliged to be one of them —
sales, cybersecurity and IT support have no category today — so **every lookup table in front
of the AI matcher must be able to say "I don't know" and hand off, never drop.** The matcher
reads free text and needs no vocabulary at all; a closed vocabulary is fine for ranking and
fatal for admission. Three rules follow, and all three have been broken in production:

- **A typed role chip that maps to no category becomes a `stack` keyword** — the shortlist
  full-text query searches those, so the word still steers retrieval. Both signup forms and
  `/preferences` do this; `test_taxonomy.py` fails if one stops. The two known-wrong
  alternatives: slugifying it into `role_categories` (produced `social_media_specialist`, a
  value no posting carries — a filter matching nothing, no error anywhere), and dropping it
  with `.filter(Boolean)` (same silence, one step earlier, chip still highlighted).
- **The retrieval floor.** Under `SHORTLIST_FLOOR` (20) candidates, `query_shortlist_meta`
  re-runs with the recall predicate dropped — location and eligibility only. Dropping it
  *widens*, because recall is `category OR keyword`, not a conjunction. The subscriber may
  still get nothing, but then it is the model's judgement on a fair shortlist rather than a
  filter that could never have matched. `widened` is recorded, because it is the fingerprint
  of someone the taxonomy does not serve.
- **`digest_runs` + `service/watchdog.py`.** One row per profile per day: `shortlist_n`,
  `widened`, `picks_n`, `sendable_n`, `sent`, written by whichever stage learns each number
  and **written before any `continue`** — the subscriber who got nothing is the one worth
  alerting on, so that path must not be the one leaving no trace. The watchdog exits non-zero
  and systemd's existing `OnFailure` alerter mails the report; addresses are masked because
  it leaves the box. It names *which* failure: `RETRIEVAL` (ours), `MATCHER` (not a bug),
  `DELIVERY` (by design). This exists because all five bugs found on 2026-07-26 were invisible
  — no exception, no failed timer, every check green while a real person received nothing.

`store.unmet_demand_terms()` lists role words subscribers asked for that nothing models. It is
a **report for a human**, never an input to anything automatic: pattern order in `PATTERNS` is
load-bearing, and each new category needs a live backfill plus six files kept in sync. Grow the
taxonomy from that list *and* real inventory — not from one person's free text.

### Tables (Postgres)

**Location preferences live in `service/geo.py`** — countries (EU-27), the curated cities per
country, the free-text→(country, city) resolver, and the SQL gate. A subscriber picks
countries, optionally specific cities per country (naming none means "any city there"), and a
separate `remote_scope` (`country` | `eu` | `worldwide`) for *fully* remote roles. Three rules
that are easy to break: **hybrid is not remote** (that is the point — a hybrid Brno role must
stay excluded for a Prague subscriber), **unknown country/city is kept** and left for the AI
matcher, since `postings.country_code` is null for whole sources and a typed city can never
equal a resolved slug, and **`profiles.regions` is derived** from the new fields on every write
— it is a coarse backstop for old clients, never the filter. `web/lib/geo.ts` mirrors the two
data tables for the browser; `service/tests/test_geo.py` fails on drift, and
`test_geo_sql.py` pins the gate's behaviour against a real Postgres (needs
`TEST_DATABASE_URL`, skipped otherwise).

**Work setup is a third axis, not a finer grade of remote** (migration 012).
`postings.work_mode` is `remote | hybrid | onsite | null`, `profiles.work_modes` is which of
those a subscriber will accept, and `geo.work_mode` is the one classifier both come from —
`remote_signal` is now *defined* as `work_mode = 'remote'` so the two can never disagree.
Three things to hold on to:

- **Hybrid is gated by city exactly like on-site**, because two days a week in Brno is still a
  commute to Brno. What `work_modes` decides is whether those roles are wanted at all; where
  they may be is still `countries`/`cities`. Only `remote` is judged by `remote_scope`.
- **`null` means the ad never said, it is the common case, and the gate keeps it.** Folding
  unknown into `onsite` would let one unticked checkbox delete most of the inventory on a
  guess. The preference goes to the AI matcher instead (`work_setup` in the export, and the
  routine prompt), which reads the description — the same division of labour as an unresolved
  city. So `remote`-only is an exact filter; the other selections are partial by construction,
  and that is stated rather than hidden.
- **An empty selection widens to all three**, in `geo.clean_work_modes`, `store`, the API and
  the browser. "No preference" is the only reading that cannot silently empty a digest.

**A source's `remote_signal` is a claim, not a fact — `is_fully_remote` checks the posting's
own words before trusting it.** `remote_signal` exempts a posting from the location gate
entirely, so a wrong one is not a cosmetic error: it is an on-site job in the wrong country
landing in an inbox. `if source_signal: return True` was true of Lever's `workplaceType` and
false of the scrapers, which derived it from substrings of graded phrases — *"Možnost
**občasné** práce z domova"*, *"Pozícia umožňuje **občasnú prácu** z domu"*, *"a hybrid work
model of 3 days in the office"*. On 2026-07-28 the flag was wrong on 3 744 of 3 744 jobs.cz
postings and 4 830 rows needed repair; 40 of 50 evaluation personas were affected, including
subscribers who had picked only Germany or the Netherlands. Two rules follow: an explicit
"fully remote" claim wins over a passing hybrid mention, and only a **named** schedule or
policy disqualifies — the bare word appears in "hybrid cloud" and German `Hybrid-DRG`, and
"hybrid/remote" is offering the choice. Changing detection means re-running
`python -m service.backfill_geo`, or stored rows keep the old answer — and since migration 012
that backfill is what fills `work_mode` at all: SQL can set `'remote'` from the old flag but
cannot tell hybrid from on-site from unknown.

`postings` · `profiles` (a subscription: preferences, tokens, CV summary) · `matches` ·
`digest_sends` · `suppression` (never-contact list, outlives the profile) · `events`
(cookieless analytics) · `events_daily` (rollups) · `digest_runs` (per-subscriber outcomes —
operational, deliberately not in `events`; cascades on profile delete so the 30-day erasure
promise needs no extra step).

### The file exchange is a trust boundary

`picks.json` is written by a claude.ai routine and travels via cloud storage. Nothing in it
is trusted. `import_picks` validates **both** sides of every match against the DB —
`posting_id` must be a real active posting, `profile_id` a real subscriber — clamps scores,
and skips a malformed record rather than aborting everyone's digest. Do not relax this.

`shortlists.json` must **never** contain email addresses. A profile is an opaque UUID. This
is what makes the transfer non-identifying and is load-bearing for the privacy policy.

### The site speaks eight languages, and the stored subscription speaks none of them

`en cs de sk pl es fr it`, declared once in `web/i18n/config.ts`. Locale lives in the URL
(`/cs/preferences/`) — a language nobody can link to is a language no crawler indexes and no
subscriber can share. `localStorage` only remembers the *choice*, so the bare `/` negotiator
can honour it; it is never what decides what renders.

- **What is stored must never depend on the display language.** A Czech and an English
  visitor tapping the same chip must produce byte-identical subscriptions. Chips are keyed by
  stable ids in `web/lib/options.ts` (`data_analyst`, `fulltime`, `junior`) and the label is
  only a rendering of the id. This is not tidiness: the maps used to be keyed by the English
  label, so translating one would have missed `ROLE_CAT`, dropped the chip through the
  category path into `stack` as a keyword, and given the subscriber a filter they never
  chose — with nothing failing anywhere. A category-less chip contributes `roleKeyword(id)`,
  the **English** word, because the shortlist full-text query runs against posting text, which
  is not in the visitor's UI language either.
- **`web/lib/geo.ts` is not translated — it is drift-tested against `service/geo.py`.**
  Country names are localised through the `geo.countries` overlay in each catalogue, which
  falls back to `geo.ts`. `en.ts`'s overlay is deliberately **empty**, so English has exactly
  one spelling and `test_geo.py` can still see drift in what the site renders. City names stay
  in their curated form in every language.
- **Two root layouts, no `app/layout.tsx`.** `app/(site)/[locale]/layout.tsx` renders
  `<html lang>` for the translated pages; `app/(plain)/layout.tsx` covers `/`, `/privacy`,
  `/terms` and the unlinked `/v2`. That split is what puts a real `lang` attribute in the
  exported HTML instead of patching it after hydration. Cross-group links must be plain
  `<a>` — a client-side transition cannot cross root layouts.
- **The privacy policy and terms exist in two languages, not eight** (`LEGAL_LOCALES`, `en`
  and `cs`), and the English is authoritative — the Czech copy says so in its own last
  section. Rule 4 below treats the policy as a specification, and every translation is a
  second specification that can drift out of step without anything failing. Two is the most
  that is worth carrying: `cs` because a Czech subscriber's own data rights should be
  readable to them. The cost is real and permanent — a change to what the product does with
  data now means editing **both** copies in that same commit, not one.
- Plurals go through `Intl.PluralRules`, never `n === 1 ? a : b` — Czech and Slovak split 2–4
  from 5+, Polish splits again at 5. `service/tests/test_web_i18n.py` fails if a catalogue
  drops a form, a role id, a country, or is declared in `LOCALES` but never wired up.
  TypeScript catches a missing *section*; only that test catches a missing *entry*.
- Error messages returned by the API (`service/webapp.py`) are still English wherever the UI
  surfaces `e.message`. Translating those means translating the backend, not the site.

**Emails follow the subscriber, not the server.** `profiles.language` (migration 013) stores
the locale someone signed up under; `service/i18n.py` is the email-side catalogue, and
`links.site_page(path, lang)` points every emailed link back at that language. It cannot be
derived at send time — the digest runs from a systemd timer with no browser and no
`Accept-Language`. Saving from `/cs/preferences/` rewrites it, deliberately with no separate
language control: a second setting that can disagree with the one they just used is how the
site and the mail drift apart again. Four things that bite:

- **`plural_category()` implements CLDR, not `n == 1`.** Czech and Slovak split 2–4 from 5+,
  Polish splits again at 5 and needs `many`. Getting it wrong renders fine and reads as broken
  Czech; `test_i18n.py` pins the categories against the CLDR chart.
- **A role word in a subject line cannot be an adjective in Slavic languages.** It has to
  agree with a noun whose case changes with the plural category — `3 nové datové nabídky` but
  `5 nových datových nabídek` — so one string cannot serve all three. `SUBJECT_WORDS` for
  cs/sk/pl are case-neutral **nouns**, rendered parenthetically (`3 nové nabídky (data)`).
  Shipped wrong once; the test asserts the word is not an inflected adjective.
- **Never `strftime("%b")`.** It renders in the C locale, so every language got an English
  month. `i18n.format_date` writes the date the way each language does.
- **`clean_locale` degrades, never raises.** It runs inside the send loop, so an unknown
  column value must produce an English digest rather than a failed send.

The legal pages are the exception to all of this: `LEGAL_LOCALES` is `en`+`cs` only, and
`language` being a **stored field** is why the privacy policy's section 2 lists it — in both
copies, per rule 4.

### Deployment reality

`/opt/jobdigest` on the VPS is **scp'd, not a git checkout**. Local `master` and the box can
drift, and have. After changing anything under `service/`, `deploy/` or `web/`, copy it up
and rebuild the affected image — a merged commit is not a deployed commit.

```bash
scp <files> deploy@<VPS>:/opt/jobdigest/<path>/
ssh deploy@<VPS> 'cd /opt/jobdigest/deploy && sudo docker compose build api pipeline web \
  && sudo docker compose up -d api web'
```

Rebuild **`api` as well as `pipeline`** whenever the change touches anything the webapp
imports (`webapp.py`, `store.py`, `geo.py`, `taxonomy.py`, …) — `api` runs `service.webapp` from
the same image, so building only `pipeline` leaves the live API on old code, and `build` alone
changes nothing until the containers are recreated. Two more things that bite here: several
files share the basename `page.tsx`, so `scp a/page.tsx b/page.tsx <host>:dir/` silently keeps
only the last — copy each to its own directory and **md5 the results against local**; and the
`web` image bakes `NEXT_PUBLIC_*` **build args**, so rebuilding with an incomplete `deploy/.env`
ships a site with the Turnstile check and Google button quietly missing.

Host `deploy` and the container's `app` user are **both uid 1000**, which is why
`exchange/` can be 0700 and the container still writes to it.

---

## Market intelligence

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

---

## Conventions

- Python: type hints on public functions, `black`, `ruff`. Each source subclasses
  `BaseSource` — no ad-hoc scripts.
- Credentials via env vars only. `python-dotenv` locally.
- dbt naming: `stg_` / `int_` / `agg_` / `fct_`, no exceptions.
- No hardcoded thresholds or weights — seeds and env vars.
- Geography has **one** definition: `service/geo.py` (mirrored in `web/lib/geo.ts`, drift-tested).
  Adding a city means: slug + display name in `CITIES`, any local spelling in `CITY_ALIASES`,
  regenerate/extend the TS mirror, run tests, then `python -m service.backfill_geo` so existing
  postings resolve to it. Work setup lives in the same file for the same reason —
  `WORK_MODES`, `work_mode()`, `clean_work_modes()`, `work_mode_predicate()` — and its TS
  mirror is drift-tested too. Changing what counts as hybrid means re-running the backfill.
- `role_category` has **one** definition: `service/taxonomy.py`. The dbt YAML and
  `web/lib/options.ts` cannot import it, so tests assert they do not drift. Adding a category
  means: pattern, subject word, shortlist keywords, dbt `accepted_values`, a chip in
  `ROLE_OPTIONS` + a label in all eight catalogues, run tests.
- User-facing copy has **one** definition per language: `web/i18n/messages/*.ts`, shaped by
  `web/i18n/schema.ts`. Never inline a user-visible string in a component under
  `app/(site)/`. Adding one means: a key in the schema, a value in all eight catalogues.
  Adding a *language* means: `LOCALES`, `LOCALE_NAME`, a catalogue, the `CATALOGUES` map,
  run tests — `test_web_i18n.py` fails on any of those left undone.
- Shell scripts and systemd units are LF-only (`.gitattributes`) — CRLF breaks them on the
  VPS in ways that look like unrelated failures.
- Commits: conventional (`feat:` `fix:` `chore:` `docs:` `refactor:` `analysis:`).
  Explain *why*, and name the failure the change prevents.

## Testing

```bash
python -m pytest                # ingestion, enrichment, service
cd dbt && dbt test
```

**CI runs the tests on every push and PR** (`.github/workflows/tests.yml`), in **two jobs split
along the same boundary as the code** — `jobdigest` (service + ingestion, minus the Snowflake
importers) and `market-intel` (enrichment + `test_load.py`). They cannot share one environment:
the root stack and `service/` pin feedparser, beautifulsoup4 and anthropic to different
versions, so one combined `pip install` is a `ResolutionImpossible`. That is why
`service/requirements-dev.txt` exists alongside the root `requirements-dev.txt`. The split is
worth more than the annoyance — each half now tests against the versions it actually deploys.

The `jobdigest` job stands up a throwaway Postgres and loads `service/db/schema.sql`, which is
what makes `test_geo_sql.py`'s 23 cases execute: they skip themselves without
`TEST_DATABASE_URL`, so before 2026-07-29 they had never run on any machine. The job fails if
they go back to skipping — a suite that silently drops its most expensive tests still shows
green, which is the failure this whole workflow exists to prevent. Locally they still skip
unless you export `TEST_DATABASE_URL` (recipe in the file's docstring); everything else runs
either way.

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
- Metabase is local Docker only; portfolio evidence is screenshots + dbt docs.
- The claude.ai routine reaches Drive through the **owner's own connector**. The folder is
  owner-only and should stay unshared — sharing it is what would create third-party access.
- `service/api.py` is the pre-v1 internal API. It has no auth and is **not** deployed
  (`Dockerfile` runs `service.webapp`). Do not expose it without adding auth.

## Open items

Tracked from `notes/2026-07-18-security-review.md`. **Resolved since that review** (do not
reopen): confirm-token expiry *and* single-use — the token is consumed on confirm, so a
replayed confirm link can no longer be exchanged for the manage token
(`store.confirm_subscription`); job-`url` scheme validation in both the digest email
(`safe_url`) and the `/matches` page (`web/lib/url.ts` `safeHref`); `pip-audit` / `npm audit`
/ Dependabot in CI (`.github/workflows/audit.yml`, `.github/dependabot.yml`); off-box backup
retention — `jobdigest-backup.sh` prunes both local and the encrypted Drive copies past
`KEEP_DAYS` (30), verified running on the VPS 2026-07-22; **origin TLS** — the LE cert that
could not renew behind the Cloudflare proxy was replaced 2026-07-29 with a Cloudflare Origin
Certificate valid to **2041**, and Caddy now logs *"skipping automatic certificate
management"* rather than attempting ACME (`deploy/cert-renewal.md`).

**Every item from the security review is now closed.**

### Measured on 2026-07-29 and deliberately NOT fixed

Three long-standing items were quantified against production rather than re-argued. Each
has a stated trigger; do not reopen without new numbers, and do not "fix" them on intuition.

- **Company name inside `search_tsv`.** It is *in* the tsvector, and that is the complaint:
  a keyword can match the employer's name and pull in unrelated ads (an agency called
  *"LEPŠÍ PRÁCE support s.r.o."*, and ČSOB, whose legal name contains *obchodní*). Real
  cost to live subscribers: 0, 1 and 5 company-only rows per 120-row shortlist, of which
  only **4 of 360 total slots** would not have been admitted by the subscriber's category
  anyway. The fix is a drop-and-recreate of a generated column plus a GIN reindex on 23k
  rows, and it removes employer search. Not worth it at ~1%. **Trigger:** a subscriber whose
  role word is also a common company word (the IT-support persona lost 8 of 120) — recheck
  with the per-subscriber shortlist measurement, not the corpus-wide one, which overstates it.
- **On-site is unprovable, and the obvious fixes are actively harmful.** `work_mode` can
  only prove `onsite` from the *location* field, while remote and hybrid also scan the
  description — that asymmetry is why the count is 0. Surveying real descriptions: every
  Czech/Slovak refusal phrase ("bez home office", "pouze na pracovišti", …) returns **zero**
  hits, so the text simply does not exist in this corpus. Worse, **`onsite` and `in-office`
  occur more often in known-*remote* ads than in unknown ones** (178 vs 114, 202 vs 109),
  because remote ads say "no onsite requirement" — a naive rule would strip genuine remote
  jobs from digests, the 2026-07-28 bug in reverse. German "vor Ort" is mostly a job *duty*
  ("Leitung der Baustellen vor Ort") and where it is an arrangement it usually means hybrid
  ("mindestens 2 Tagen vor Ort"). Only *named* forms survive — `Präsenzstelle` is genuine
  and yields **4 rows out of 11 613**. Not worth a pattern, a backfill, or the risk. Note
  the On-site chip is *not* inert end to end: it still reaches the AI matcher as
  `work_setup`, it is only the SQL gate that cannot act on it.
- **Category breadth** in `product` / `design` / `devops_platform`. Live impact is ~2–3
  obviously-off rows per 120 (a travel-industry *produktový manažer* for the product
  subscriber). The structural cause is missing categories, and the inventory is now real:
  **sales 659** active (656 sitting in `other_tech_function`), **cybersecurity 185**
  (scattered across `software_engineering` 117, `data_analysis` 23, `devops_platform` 20),
  **IT support 93**. But CLAUDE.md's rule needs inventory **and** demand, and the demand half
  is absent: `unmet_demand_terms()` currently returns only tool names (figma 3, canva 1,
  capcut 1, roadmapping 1) — no role words at all. **Trigger:** a role word appearing in
  that report. The inventory numbers above are recorded so the decision is half-made when
  it does.

Three things about the TLS setup are load-bearing and easy to undo by accident:

- **The private key was generated on the VPS and never left it.** Cloudflare's default flow
  displays a generated key in the browser once; that would put a credential in a transcript
  (security rule 2). Only the CSR was pasted into the dashboard. If the cert is ever
  re-issued, keep that shape.
- **The `tls` directive is committed, not a box-local edit.** `web/Caddyfile` is baked into
  the `web` image, so an uncommitted edit on the box would be reverted by the next
  `scp web/Caddyfile` and would take the site down at the following rebuild. The Caddyfile
  carries `import /etc/caddy/origin/*.caddy`; the box-specific line lives in
  `deploy/origin/tls.caddy` (gitignored, mounted `:ro`). A glob matching nothing is a no-op,
  which is what keeps local dev on automatic certs — do not replace it with a plain `import`.
- **The liveness healthcheck has to trust that cert, and didn't.** The Origin CA is not a
  public root, so `deploy/jobdigest-healthcheck.sh`'s loopback `curl` returned `HTTP 000` on a
  healthy box from the moment the cert went live — red for 3.5 h on 2026-07-29 while the site
  was fine. It now pins `--cacert .../origin/cert.pem` when present and falls back to the
  system store when absent. **Pin, never `--insecure`**: under Full (strict) a wrong origin
  cert is a 526 for every user, and this check is what should catch it. The same incident
  exposed a second bug — the script alerted only when the failure counter *equalled* the
  threshold, so one mail went out and the next 36 failures were silent; it now re-alerts
  roughly hourly (`REALERT_EVERY`). **A monitor that can only fire once has a single point of
  failure at "you were looking".**
