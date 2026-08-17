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

> **The market-intelligence / Snowflake side is DECOMMISSIONED (2026-07-22).** `dbt/`,
> `enrichment/`, `ingestion/` and the workflow code are kept as a portfolio snapshot and no
> longer run. **JobDigest is the sole live system.** Do not spend effort maintaining that
> side — see `docs/market-intel.md`.

**JobDigest handles real personal data.** Treat changes there with the care that implies.

```
ingestion/        BaseSource adapters, Snowflake load, migrations
enrichment/       Claude skill extraction + the routine file exchange (Snowflake side)
dbt/              staging → intermediate → marts
service/          JobDigest: webapp, matcher, digest, mailer, store, taxonomy, cvparse
  service/tests/  the tests that matter most — see "Testing"
web/              Next.js 15 static export; app/(site)/[locale]/ is the translated site,
                  app/(plain)/ the rest — two root layouts, no app/layout.tsx
  web/i18n/       the eight message catalogues + LOCALES; one definition per language
deploy/           Compose, Caddy, systemd units, backup + matcher runbooks
docs/             the reference material this file points at
notes/            session logs, security review; notes/INFRA.local.md is gitignored
```

## Where the detail lives — read these when the trigger fires

This file is the rules. The reasoning, the measurements and the failure histories behind them
live in `docs/`, because they are consulted for particular tasks rather than every session.
**The rules below are stated without their evidence; do not re-derive or overturn one from
intuition — the doc has the numbers.**

| Read | Before |
|---|---|
| `docs/jobdigest.md` | changing anything under `service/`, `web/` or `deploy/` |
| `docs/sources.md` | adding, removing, re-probing or re-arguing **any** job source, or touching `ingestion/` |
| `docs/market-intel.md` | any Snowflake, dbt or `enrichment/` work (decommissioned — usually the answer is "don't") |

`notes/` holds dated session logs; the ones a rule below cites by name are worth reading when
you touch that rule.

---

## JobDigest

### Flow

```
05:00 UTC  digest   ingest sources → Postgres → AI match (metered API, due subs only) → send
08:00 UTC  sources  per-source freshness + churn → alert if a source silently died
09:00 UTC  watchdog digest_runs → alert if any subscriber has had nothing for 3 days
01:30 UTC  backup   pg_dump → encrypt → off-box

Sun 09:30  categorize export  uncategorised titles → titles.json → Drive
Mon 09:30  categorize import  validated answers → title_categories  (weekly, off the digest
                              path on purpose — see deploy/categorize-routine.md)
```

Retrieve-then-rerank: a cheap full-text prefilter builds a ~120-posting shortlist per
subscriber, then one Claude call reads the whole shortlist in context and picks what fits.
Cost scales with subscribers, not inventory. Candidates go to the model by integer index —
posting IDs never round-trip — and invented indices are dropped.

**Matching runs on the box with a metered `ANTHROPIC_API_KEY` (since 2026-08-17), not the old
claude.ai routine.** `service.matcher.run()` reranks only the subscribers `pipeline._is_due`
will actually email today — a weekly subscriber is not billed on the five off days — and
`match_one` powers an on-demand `POST /digest/run` (6h cooldown). Using a personal Claude/Codex
subscription as a headless backend was ruled out on terms (Anthropic Consumer Terms + OpenAI
ToS both forbid commercial + automated subscription use); the licensed path is the API key. The
`export_shortlists`/`import_picks`/Drive/`shortlist_shadow`/embed code is retained but **no
longer scheduled** — do not treat the routine as live. See `deploy/matcher-routine.md`.

### The invariants

Each of these has been broken in production at least once. Reasoning and measurements in
`docs/jobdigest.md`.

- **The shortlist stays keyword-retrieved at ~120.** The vector-narrowing plan was gated on
  `scripts/measure_shadow_recall.py` and the gate came back negative. `shortlist_shadow`
  records the vector top-K daily and reads nothing on the delivery path. **Do not re-open
  this from intuition** — re-open it on accumulated shadow days, on seniority being gated
  outside cosine, or on subscriber count making token cost binding.
- **The embed step runs between ingest and shortlist export, in its own container, and is
  non-fatal.** Own container bounds peak RAM; before the export or the shadow is blind to the
  freshest rows; non-fatal because a measurement must never cost a day of digests.
- **One job, one email — even under several `posting_id`s.** `digest_sends` keys on
  `posting_id`; `digest.dedupe_key` also collapses a normalised (company, title, city). It
  runs before the limit, its seen-set is pre-seeded from `digest_sends`, an unknown city is a
  wildcard, and an empty key is always unique. It folds only a *trailing* legal form, so
  **carrying one employer on both a national register and an ATS board risks a second email**
  — a standing check whenever a country has both.
- **`/matches` is deliberately NOT deduplicated** and stays the complete record.
- **The candidate window must exclude what was already emailed, in SQL** (`exclude_sent`,
  set only by `build_digest`) — otherwise the window tightens with tenure and long-running
  subscribers silently go quiet. The flag is opt-in and **must never reach the web path**;
  `already_sent_ids` stays in `build_digest` as the actual guarantee.
- **Hiding a job is a move, never a delete.** `matches.status = 'dismissed'`; `upsert_match`
  never writes `status`; `prune_matches` never deletes dismissed rows; `matched_jobs` and
  `match_count` carry the filter separately; the write is scoped by `profile_id` in the
  WHERE clause — that is the authorisation, not a convenience.
- **Every lookup table in front of the AI matcher must be able to say "I don't know" and hand
  off, never drop.** A role chip that maps to no category becomes a `stack` keyword. Under
  `SHORTLIST_FLOOR` (20) the recall predicate is dropped and `widened` is recorded.
  `digest_runs` gets a row per profile per day, **written before any `continue`**.
- **`store.unmet_demand_terms()` is a report for a human**, never an input to anything
  automatic.
- **Geography has one definition: `service/geo.py`** (mirrored in `web/lib/geo.ts`,
  drift-tested). `COUNTRIES` (selectable) and `EEA_COUNTRIES` (what the `eu` remote scope
  means) are **separate sets** — merging them silently widens that scope. Unknown country or
  city is **kept** and left to the AI matcher. Hybrid is not remote and is gated by city
  exactly like on-site. Adding a country or city means the tables, the TS mirror, tests,
  **and `python -m service.backfill_geo`** — without it, stored rows keep the old answer.
- **Before adding a name to `COUNTRY_ALIASES` or `FOREIGN_CITIES`, check it is not also a
  city, a US state, or an ordinary word.** That is the `georgia` rule; `island`, `waterloo`,
  `cambridge` and the `CA`/`DE`/`MT`/`NL`/`SK` subdivision codes are the same rule.
  `check_no_shadowed_cities` raises if a name sits in both tables.
- **Seniority has six levels and a NULL, and the NULL is the majority.** One definition:
  `search_jobs.seniority()` (mirrored as `SENIORITY_IDS` in `web/lib/options.ts`, labels in
  all eight catalogues plus `service/i18n.py` for email). `intern | entry_level` split on
  **contract shape** (a placement vs a first permanent job), `senior | lead` on **kind** (IC
  vs people leadership) — neither pair is a ladder step. **NULL means the title named no
  level: 70.7% of the live corpus**, and it is never a selectable value. The two polarities
  are deliberate: display filters (`/jobs`, `/matches`) are equality, so NULL is excluded;
  **the digest path does not gate on seniority in SQL at all** — `_hard_gate` must never grow
  that predicate, or two thirds of every candidate pool vanishes with no error. Changing the
  patterns means `python -m service.backfill_seniority`, and the live claude.ai routine
  prompt is a *separate* copy that must move too.
- **Education is a fourth axis and is mostly unprovable.** `service/education.py` is the one
  definition (mirrored in `web/lib/education.ts`). Null is 90.6% of rows and **always passes
  the gate**; the classifier's only safe error is a miss; a CV never narrows this
  server-side. Changing the patterns means `python -m service.backfill_education`.
- **A source's `remote_signal` is a claim, not a fact** — `is_fully_remote` checks the
  posting's own words. Only a *named* schedule or policy disqualifies. Changing detection
  means re-running `python -m service.backfill_geo`.
- **The digest gate reads `remote_reach`, and until 2026-08-17 it did not.** A fully-remote
  posting was exempt from the location test outright, so the owner's own subscription was
  emailed five "100% remote" data-engineering roles bound to Poland, India and the UK, scored
  7-8, none holdable from Prague. Nothing was broken: the gate exempted them, the prompt taught
  the exemption, the model obeyed, `digest_runs` looked healthy and the watchdog stayed silent.
  Two halves now close it and **they must move together** — `geo.reach_predicate` (ANDed into
  `location_predicate`'s remote arm at *every* scope, because `eu` leaked identically: Poland is
  in the EEA) and the `reach` token `matcher._reach_for_model` renders into both prompts. **It
  refuses only what a posting positively states**: a null reach, an unenumerable macro-region,
  or a `country` reach whose country is unknown are all KEPT and handed to the model as
  `reach=?`, and a subscriber with no `countries` at all gets `?` on every row rather than a
  refusal on every row — the failure that would empty a legacy profile's shortlist entirely.
  Sized before it shipped: 966 of 1 101 active remote rows in the owner's two categories name
  another country, against 7 `anywhere`. Do not "simplify" it into a check on `remote_signal`
  alone, and do not gate it in the prompt only — a prompt-only fix makes the model reject 90% of
  its own shortlist and the digest goes quiet as `exclude_sent` retires the local rows.
- **"Remote" is two questions, and `remote_signal` only answers one.** Whether there is an
  office is `work_mode`; **where you may live while holding the job is `postings.remote_reach`**
  — `anywhere | region | country | NULL`, one definition in `geo.remote_reach`, backfilled by
  `python -m service.backfill_remote_reach`. The two are independent and conflating them
  promises something no posting said. **The measured answer is that "remote" almost always
  means work from home in one country: 82.1% `country`, 5.4% `region`, 0.5% `anywhere` — 83
  postings — and 12% NULL** (all 16 296 active remote rows, 2026-08-14) — so *do not build a
  feature on the assumption that fully-remote-abroad inventory is large*, and do not re-argue
  the size of it from intuition.
  **That denominator is known to be inflated and is being corrected (2026-08-17).** `ashby` was the
  corpus's largest remote claimant — 5 320 of its 9 598 active rows — and it read Ashby's derived
  `isRemote` boolean while ignoring the employer's own single-valued `workplaceType`. Sampled over
  14 live boards, **64% of its remote claims (882 of 1 378) are actually `Hybrid`** — on the order of
  **~3 400 rows, about a fifth of the 16 296, were never fully remote.** Fixed at the source;
  `remote_signal` comes from the adapter, so no backfill can recompute it and the rows correct
  themselves over the ingest window. **The percentages are ratios and are not invalidated — the
  reach classifier never read `isRemote` — but every absolute count resting on "active remote rows"
  is, including the EU-International / North America-International figures below. Re-measure once
  the window has turned over; do not re-derive them by argument.** The lesson is the invariant
  itself arriving through a new door: a `remote_signal` is a *claim*, and a publisher's own
  structured field beats a boolean the publisher derived. `is_fully_remote` cannot catch this class —
  it re-reads the posting's prose, and `workplaceType` is a field, so the word "hybrid" appears
  nowhere for it to find. **The signal is a board's structured field first, location text second, prose
  last.** Reading description prose alone found 5.8% and looked unprovable; that was the wrong
  layer. `JobPosting.scope_raw` carries the publisher's own scope field verbatim — it is a
  *claim*, like `remote_signal`, and the reason a classifier change can be backfilled without
  re-ingesting. It must **never** be folded into `location`: `resolve_location` takes the first
  country n-gram it finds, so Ashby's secondary countries in front of "Paris offices" silently
  re-home the job to Germany. **Every rule is a positive detection and a named country outranks
  every wider signal** — all three false positives found on live data were a wide signal
  beating a stated country ("Anywhere in the United States" read as `anywhere`; a Himalayas
  timezone band read as `region` on all 300 sampled rows). Prose is read through a keyhole:
  a bare "based in" is an employer's head office, not an eligibility rule.
- **On `/jobs`, Country means *where* and Work setup means *how*.** They were conflated until
  2026-08-14 by a synthetic "Remote" row at the top of the Country menu that ORed in every
  fully-remote posting — a location control answering a work-arrangement question, which showed
  a Prague visitor US-only roles. The Country menu now leads with two **international rows**
  backed by `postings.reach_areas` (`geo.REACH_AREAS`, mirrored and drift-tested in
  `web/lib/geo.ts`, sent as the `intl` parameter): **EU-International 568** postings,
  **North America-International 403**, **150 in both** — and the overlap is the only set in
  which someone in the EEA can hold a US-facing role, so **the two counts must never be
  summed**. A remote job bound to one country is in *neither* row: it sits under its own
  country and Work setup flags it remote. That is what keeps the rows meaningful — 13 377 of
  16 296 active remote postings are single-country. **`eea` means `EEA_COUNTRIES`, not
  `COUNTRIES`** (GB is selectable and outside the EEA), the same decoupling as the `eu` remote
  scope. `remote_reach` and `reach_areas` must come from one `geo.classify_reach` call, or a
  posting is `region` from its scope field and filed under the area named in its location
  field. **The timezone patterns read the raw scope text, not `normalise`d output** — the sign
  in "UTC+2" does not survive normalisation, so a signed pattern on normalised text is a regex
  that can never match. An unrecognised `intl` id is *dropped*, which widens; so mirror drift
  makes the filter silently return everything rather than fail.
- **A posting has several countries; `country_code` holds one of them.** `resolve_location` must
  pick one because `city` has to agree with it, so before 2026-08-15 every *other* country a
  posting named was reachable by nothing — one Printful row listing twelve countries answered
  eleven country filters with silence, and each of those was individually a correct "no rows".
  `postings.reach_countries` (`geo.reach_countries`, migration 023) holds them when a posting
  names two or more, and is **ORed** into the country test in both `store._search_where` and
  `geo.location_predicate` — a union, never a replacement. Three rules: it is **deliberately not
  gated on being remote** (where you may *live* is a category error for an on-site job; whether
  there is a job for you in Poland is not, and two offices in two countries answers yes to both);
  the **country facet must count `country_code ∪ reach_countries`, deduped**, or the menu
  promises fewer rows than ticking it returns; and `eu` remote scope tests the array against
  **`EEA_COUNTRIES`**, on the same rule and the same GB counterexample as everything else here.
  It reads **fields only, never the description** — prose is thin enough evidence for a reach
  verdict and far too thin to mint a country-filter entry. Adding it to the digest gate widens
  the candidate pool on purpose.
- **A scope can be a *list* of locations, and `countries_named` cannot read one.** Its rule that
  an explicitly named country discards every city-implied one is correct within a single phrase
  ("London, London, Ontario, Canada" is Canada) and wrong across a list: `"Warsaw, PL; Kyiv, UA;
  Bucharest, RO; Tallinn, EE; Barcelona, ES"` classified as `country` — Ukraine, the only token
  read as a country outright, with the other four dropped as merely implied. `countries_in_scope`
  splits on **`;`** and resolves each entry independently; the adapters that join a `locations[]`
  array must use that separator, because the comma is already spoken for inside one entry.
- **The macro-region words are `_EUROPE` / `_EUROPE_SUB`, one constant feeding both
  `_MACRO_REGION` and `_EEA_AREA`.** They read English only until 2026-08-15, so `Europaweit`,
  `Europees` and `Evropa` classified as no scope at all — silently, on exactly the boards an
  international row exists to surface. Country names were never the gap. Two copies of the word
  list drift (a refactor dropped `emea` from one of them and only the regression list noticed),
  the stems are enumerated rather than `europ\w*` (which matches *Europcar*), and **`norden` is
  excluded under the `georgia` rule** — the Nordics in three languages, a town in Lower Saxony,
  and the ordinary German word for "the north"; `nordisk` says it with no collision.
- **`postings.eligibility` is a subscriber-specific judgement in a posting-level column**, so
  a constant allowlist over it is always wrong for somebody. `store.eligibility_allowlist`
  derives from `profile["countries"]`, shared by both call sites.
- **There are two file exchanges and both are trust boundaries.** The second one
  (`titles.json` → `categories.json`, weekly) classifies the titles no pattern or occupation
  code could read; `import_categories` drops an answer for a title it never asked about and
  any category not in `taxonomy.CATEGORIES`, stores `uncategorised` as a *recorded decline*
  so the same unreadable titles are not re-exported forever, and is keyed on
  `categorize_exchange.normalise_title` — the one definition, deliberately never re-expressed
  in SQL. The cache is consulted **only where patterns and codes both decline**, so it can
  add a category but never change one. Its export carries **titles and integer indices only**.
- **The file exchange is a trust boundary.** Nothing in `picks.json` is trusted:
  `import_picks` validates both sides of every match against the DB, clamps scores, and skips
  a malformed record rather than aborting everyone's digest. **`shortlists.json` must never
  contain email addresses.** A partial `picks.json` is invisible except to
  `matcher.coverage_gap`, which reads the *file*, not `digest_runs`.
- **`sendable_profiles`' `order by last_digest_at nulls first` is a priority policy**, so a
  truncated run starves long-tenured subscribers, never someone waiting on their first.
- **The site speaks eight languages; the stored subscription speaks none of them.** What is
  stored must never depend on the display language — chips are keyed by stable ids in
  `web/lib/options.ts`. Locale lives in the URL. Two root layouts, no `app/layout.tsx`;
  cross-group links must be plain `<a>`. Plurals go through `Intl.PluralRules`.
- **Emails follow the subscriber, not the server** (`profiles.language`, `service/i18n.py`).
  `plural_category()` implements CLDR; a role word in a Slavic subject line must be a
  case-neutral noun; never `strftime("%b")`; `clean_locale` degrades, never raises.
- **The privacy policy and terms exist in two languages** (`en`, `cs`) and **both must move
  in the same commit** — see security rule 4.

### Running it locally

`dev/` — three processes, no image rebuilds in the loop, nothing that can reach production.
Full instructions in `dev/README.md`.

```powershell
.\dev\db.ps1 reset          # postgres :5433 — reset is the only migration path
. .\dev\env.ps1
python dev\seed.py          # 40 synthetic postings, a confirmed subscriber, matches
.\dev\api.ps1               # service.webapp on localhost:8811
.\dev\web.ps1               # next dev on localhost:3000
```

Four decisions, not conveniences: `reset` is the only migration path (`schema.sql` runs only
on an empty data directory, so an old volume keeps the old schema and the symptom is a 500);
both servers are on `localhost`, because the `SameSite=Lax` session cookie treats
`127.0.0.1` as cross-site — **never loosen `samesite` to make dev work**; `seed.py` lives
outside `service/` so a script minting a live `manage_token` cannot ship in the image, and it
refuses any non-localhost `DATABASE_URL`; **never copy the production database down**.
`next dev` is not what ships — `npm run build` and `deploy/docker-compose.smoke.yml` are.

### Deployment reality

`/opt/jobdigest` on the VPS is **not a git checkout**. Local `master` and the box can drift,
and have — a merged commit is not a deployed commit.

**Use `deploy/deploy.sh`. Do not hand-assemble an `scp` list.**

```bash
deploy/deploy.sh              # ship HEAD (refuses if dirty or unpushed)
deploy/deploy.sh --dry-run    # list what would ship
deploy/deploy.sh --sha <sha>  # roll back to a known-good commit
```

It ships the *committed tree* via `git archive | tar -x`, syncs the systemd units (the one
thing a tree sync cannot reach), and health-checks with automatic rollback. The host comes
from the gitignored `deploy/.deploy-target`.

- **The failure a hand-rolled `scp` list produces is not a missing file, it is a stale one.**
  `search_jobs.py` sits at the repo root and was outside every directory checked; the box ran
  10 sources while `master` ran 13, and no check said otherwise. If you must copy by hand,
  diff the **whole tree in both directions** and hash the result **inside the image**.
- **`.deployed-sha` is `deploy.sh`'s rollback target, not a comment.** Deploying by hand and
  not updating it is how a failed health check reverts the box past an entire release.
- **The build context is a place code can go missing.** `.dockerignore` excluded `dbt/` while
  `greenhouse.py` read its board tokens from `dbt/seeds/target_companies.csv` at runtime — the
  largest source loaded zero boards and only logged a warning. When a source's number looks
  wrong, check whether its data even reached the image.
- **The `db` service is a BUILT image** (`deploy/db.Dockerfile`, pgvector on
  `postgres:16-alpine`). **Never swap it for the glibc `pgvector/pgvector:pg16` without a dump
  and restore** — this cluster was initdb'd on musl and the two libcs order the same locale
  differently, which corrupts all 19 indexed text columns silently. `db` must stay in
  `deploy.sh`'s build list.
- **Rebuild `api` as well as `pipeline`** whenever the change touches anything the webapp
  imports; `build` alone changes nothing until the containers are recreated. Several files
  share the basename `page.tsx`, so a multi-file `scp` silently keeps only the last. The `web`
  image bakes `NEXT_PUBLIC_*` **build args**, so rebuilding with an incomplete `deploy/.env`
  ships a site with Turnstile and the Google button quietly missing.

Host `deploy` and the container's `app` user are **both uid 1000**, which is why `exchange/`
can be 0700 and the container still writes to it.

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
4. **The privacy policy is a specification.** `web/components/legal/PrivacyEn.tsx` makes
   concrete promises — no IP stored, CV discarded, unsubscribed profiles erased within 30
   days, data sent to the matcher carries no email. Changing retention, adding a processor,
   or storing a new field means changing that page in the same commit, or the policy becomes
   false. There are **two** copies (`PrivacyCs.tsx`, plus `TermsEn`/`TermsCs`) and both must
   move together. English is authoritative, but that clause is a tie-breaker, not permission
   to skip the edit. A new *value* in an existing column is not a new field.
5. **Backups contain bearer tokens.** `manage_token` grants full control of a subscription.
   Dumps are encrypted before leaving the box and must never be written into the matcher's
   Drive folder — `jobdigest-backup.sh` enforces this.
6. **Least privilege.** No SYSADMIN in CI; no root SSH; the API container runs non-root.
   **Test the credential, not the grant statement** — connect as the identity that will be
   used and try the operation that should be refused. The Snowflake-specific rules (secondary
   roles, object ownership) are in `docs/market-intel.md` and bind only if that side revives.

---

## Conventions

- Python: type hints on public functions, `black`, `ruff`. Each source subclasses
  `BaseSource` — no ad-hoc scripts.
- Credentials via env vars only. `python-dotenv` locally.
- dbt naming: `stg_` / `int_` / `agg_` / `fct_`, no exceptions.
- No hardcoded thresholds or weights — seeds and env vars.
- Geography has **one** definition: `service/geo.py` (mirrored in `web/lib/geo.ts`,
  drift-tested). Adding a city means: slug + display name in `CITIES`, any local spelling in
  `CITY_ALIASES`, regenerate/extend the TS mirror, run tests, then
  `python -m service.backfill_geo` so existing postings resolve to it. Work setup lives in
  the same file for the same reason — `WORK_MODES`, `work_mode()`, `clean_work_modes()`,
  `work_mode_predicate()` — and its TS mirror is drift-tested too. Changing what counts as
  hybrid means re-running the backfill.
- `role_category` has **one** definition: `service/taxonomy.py`. The dbt YAML and
  `web/lib/options.ts` cannot import it, so tests assert they do not drift. Adding a category
  means: pattern, subject word, shortlist keywords, dbt `accepted_values`, a chip in
  `ROLE_OPTIONS` + a label in all eight catalogues, run tests.
  **A source's `source_category` hint must already be a value in `CATEGORIES`, or `None`** —
  `classify` discards anything else, and until 2026-08-08 it did not, leaving 14% of active
  postings in categories no query could name. There is deliberately **no backfill** (the
  column drains over the staleness window). `service.ingest._report_discarded_hints` logs what
  was discarded per source, in the shape of `unmet_demand_terms()`.
  **`taxonomy.py` used to read only English, and that is now largely fixed** — Swedish and
  Czech were taught first (they are the two *graded* slices), then fr/de/nl/es/it/pl/pt in
  waves 1–2, then **Norwegian on 2026-08-11**. What survives is the honest limit: only SE
  (SSYK), CZ (ISCO) and now **NO (STYRK-08)** have a publisher-assigned occupation code, so
  the other six languages are **coverage-only and their correctness is unproven**. Never
  quote a coverage number as accuracy.
  **Norwegian is the model to copy for any further language work**: the answer key
  (`scripts/categorization_score.py --fetch-no`) was built *before* the vocabulary, so the
  boundary calls were settled by the publisher's own coding rather than by argument — which
  is how `miljøarbeider` came to be **declined** (NAV splits it healthcare 5 / social_care 4 /
  education 3 even on titles naming no other profession, so any single answer is a coin
  flip). Norwegian went 68.5% → 42.7% uncategorised on a held-out half of a live NAV corpus.
  **The remaining languages are sourcing-limited, not vocabulary-limited** — Italian's
  residual is ~50% English titles on Italian boards, Polish's permitted corpus is the
  English-writing SSC/BPO layer. More permitted inventory is now worth more than more words.
- User-facing copy has **one** definition per language: `web/i18n/messages/*.ts`, shaped by
  `web/i18n/schema.ts`. Never inline a user-visible string in a component under
  `app/(site)/`. Adding one means: a key in the schema, a value in all eight catalogues.
  Adding a *language* means: `LOCALES`, `LOCALE_NAME`, a catalogue, the `CATALOGUES` map.
- Shell scripts and systemd units are LF-only (`.gitattributes`) — CRLF breaks them on the
  VPS in ways that look like unrelated failures.
- Commits: conventional (`feat:` `fix:` `chore:` `docs:` `refactor:` `analysis:`).
  Explain *why*, and name the failure the change prevents.

---

## Testing

```bash
python -m pytest                # ingestion, enrichment, service
cd dbt && dbt test
```

**CI runs the tests on every push and PR** (`.github/workflows/tests.yml`), in **two jobs
split along the same boundary as the code** — `jobdigest` (service + ingestion, minus the
Snowflake importers) and `market-intel` (enrichment + `test_load.py`). They cannot share one
environment: the root stack and `service/` pin feedparser, beautifulsoup4 and anthropic to
different versions, so one combined `pip install` is a `ResolutionImpossible`. That is why
`service/requirements-dev.txt` exists alongside the root `requirements-dev.txt`.

The `jobdigest` job stands up a throwaway Postgres and loads `service/db/schema.sql`, which
is what makes the SQL-backed tests execute — they skip themselves without
`TEST_DATABASE_URL`, so before 2026-07-29 they had never run on any machine. **The job fails
if they go back to skipping**: a suite that silently drops its most expensive tests still
shows green, which is the failure this whole workflow exists to prevent. Locally they skip
unless you export `TEST_DATABASE_URL` (recipe in each file's docstring).

`service/tests/` covers the places where being wrong is expensive, and the tests are written
to fail when the guarantee breaks — not merely to pass:

- `test_matcher_exchange.py` — untrusted `picks.json` cannot reach the DB; the export
  carries no email.
- `test_webapp_lifecycle.py` — a GET never unsubscribes; one-click still works; `/event`
  is bounded.
- `test_taxonomy.py` — classification, ordering, and drift against dbt + the frontend.
- `test_education.py` — a requirement nobody wrote down is never invented; every
  false-positive case is real production text that classified wrongly first.
- `test_education_sql.py` — a null `education_min` passes the real gate, on both the narrow
  and the widened retrieval path.
- `test_hidden_sql.py` — hiding is a move, not a delete: the two queries behind the page
  agree, a re-score cannot resurrect a hidden job, and one subscriber cannot hide another's.
- `test_export_coverage_sql.py` — who a truncated run sacrifices, and who we notice it
  missed.
- `test_geo_sql.py`, `test_eligibility_sql.py`, `test_digest_window_sql.py` — the gates,
  executed against a real Postgres, because a string cannot show you what `WHERE` does with
  `NULL`.

**The CI skip-check globs `service/tests/test_*_sql.py`; it must never go back to a list.** A
list is a copy — adding a SQL-backed file and forgetting the copy leaves a test that can
silently stop running, which is the exact failure the step exists to prevent, arriving
through the step itself. Same lesson as `source_watchdog.py` reading
`search_jobs.source_classes` and `pending_boards.py` reading the adapters' own list
structures. The step also fails if the glob matches *nothing*.

When adding a guard, mutation-check it: break the thing deliberately and confirm the test
goes red. A test that cannot fail documents nothing.

**`scripts/check_links.py` is the one check that leaves the machine**, and it is deliberately
not in CI. A stored URL is never fetched again, so a dead link is invisible to every other
check — right row count, fresh ids, no churn, green tests, and a subscriber clicking nothing.
That failed twice in three days. Run it when an adapter's URL construction changes; the five
load-bearing rules and the current baseline are in `docs/sources.md`.

---

## Known constraints and decisions

The full source catalogue — every terms-of-use verdict, country sweep and adapter
measurement — is in **`docs/sources.md`**. What follows is only what applies without opening
it.

- **If a source does not already permit us, we skip it. We do not write to publishers.** A
  refusal is final, and a source gated behind an application, a signed agreement, a
  verification step or a negotiated feed is **skipped on the same footing as a refusal** —
  the thing standing between us and the data is correspondence, and we do not do
  correspondence. Do not draft a letter, do not open a "pending a conversation" state, and do
  not record a source as *waiting* on anyone. What remains open without asking is **curated
  employers on Greenhouse/Lever/Ashby/Recruitee/Workable/Workday/Oracle/Teamtailor** via
  `scripts/discover_ats.py`.
- **Settle a source on the narrowest applicable clause, never on robots.txt alone.** Fourteen
  sources so far permit in robots what they refuse in their terms. Check robots.txt at the
  **host root**, and quote the clause that would refuse you. **A Cloudflare Content-Signal
  block (`ai-train=no`, a named-AI-crawler disallow list including `ClaudeBot`) is neither a
  refusal of *us* nor boilerplate** — we match `User-agent: *`, so it decides nothing on path
  permission, but it is an express Art. 4 EU DSM reservation and a reason to go read the terms.
  **A client-rendered SPA returns 200 for paths that do not exist**, so a path probe cannot
  find terms; the site's own sitemap plus the Wayback Machine can, and neither is browser
  automation. **No terms at all is "permission unestablished", which is not the same as a
  refusal** — the "Personio shape" (`cyprusjobs`, NHS Jobs, NVA's `cvvp`, `euroremotejobs`).
  Historically that was an automatic skip. Two things now sit above it:
  - **An affirmative grant elsewhere makes silence permission, and `teamtailor` is that case**:
    no ToS, but robots carries `Content-Signal: ai-input=yes` and a keyless public JSON feed
    that exists to be read. Silence plus an invitation is permission; silence alone is not.
  - **The owner decided on 2026-08-15 to ingest a named, finite set of silent sources anyway**
    — currently `remoteineurope` only. That is a risk-appetite call on the owner's own product,
    not a general licence. **It never reaches a source that refuses**: an anti-automation
    clause, a personal-use-only licence, a refusing robots.txt or an application gate all still
    close a source, and `test_source_exclusions.py` is unchanged. Adding another silent source
    means editing `ingestion/tests/test_unestablished_permission.py`, which enumerates the set
    so it cannot spread by habit. Every such adapter still calls `robots_allows` and `throttle`.
- **But that rule cuts one way only, and SmartRecruiters is the case that showed it.** A
  *permissive* robots is evidence of nothing; a **refusing** robots is dispositive on its own
  and the terms never need to be reached. `api.smartrecruiters.com/robots.txt` is `Disallow: /`
  for `*` while granting the exact path we used to LinkedInBot alone —
  **removed from `gather()` 2026-08-11**, and pinned by `test_source_exclusions.py`. Do not
  re-add it because a country looks thin. **The cost was argued as ~7 700 postings and measured
  at ~1 690 reachable ones** (3 286 stored active, half in countries nobody can select, led by
  India at 445) — board-held, stored-active and subscriber-reachable are three different
  numbers and only the last is the price. The wider lesson is the guard, not the source: **an
  adapter that leaves the machine must call `robots_allows()` and `throttle()`**, and nothing
  currently checks that it does.
- **jobs.cz and profesia are excluded on Alma Career's terms** (2026-08-03), at a deliberate
  cost of 92% of Czech and 99% of Slovak inventory. `test_source_exclusions.py` fails if
  either returns to `gather()`. **Do not re-add them because the digest looks thin.**
  Alma Career also owns Teamio, CVonline.lt, CV.lv, CV.ee, MojPosao and Jobly.fi — check
  ownership before checking terms.
- **Never use LinkedIn beyond its public RSS** — account ban risk.
- **No browser automation on the sourcing/ingestion path.** The rule is scoped to *sourcing*,
  and it is about terms and legal risk, not the tool: a source that can only be read by
  driving a headless browser is one whose anti-bot wall or terms are telling us no, so **a
  source needing a browser to crawl it is closed, not a project** — and `test_politeness.py`
  fails if any adapter reintroduces a browser agent. Browser automation (Playwright,
  claude-in-chrome) is **fine elsewhere** — dev/UX testing already uses it, and a future
  *user-authorised* auto-apply workstream is a separate, permitted use. What is forbidden is
  scraping a job board with a browser, not the browser.
- `ingestion/politeness.py` is the one place for the crawler's identity: `USER_AGENT`
  (`JobDigest/1.0` + contact URL), `robots_allows()`, and `throttle()` at 1 s/host.
  `test_politeness.py` fails if any adapter reintroduces a browser agent. Its 1 s guarantee
  holds **within one process only** — parallelise discovery with `--workers`, never with more
  processes.
- **A source that returns a round, plausible number is the hardest kind of broken.** Four
  adapters silently returned a fraction of what they held and none errored. **Page by the
  count received, never by the count requested**, and when a source looks small, measure what
  it holds before believing it. Equally: **a live API is not a live board** — probe the public
  page, and check a board's own postings for company and city before adding it. A live 200 is
  not evidence the board belongs to the company whose name it spells.
- **A source-level country constant is almost always wrong.** Resolve from the posting's own
  text and let unknown be unknown. The sound exceptions are in `docs/sources.md`.
- **A source that changes its URL scheme re-creates its whole inventory**, because
  `posting_id = md5(url)`. `posting_id` must hash something the employer cannot edit, and a
  link must carry whatever the site needs to resolve it. `upsert_postings` refreshes `url`,
  so a link repaired in the adapter repairs stored rows without churning ids.
- **`service/source_watchdog.py` (08:00 UTC) is what makes both of those loud.** It reads the
  expected source list from `search_jobs.source_classes` — never a copy — and flags
  `SILENT ZERO` and `ID CHURN`. The churn ratio's denominator is `seen_today`, not `active`.
- **Adzuna** is 250 req/day across per-country indices; `test_adzuna.py` fails if a change
  busts `DAILY_REQUEST_BUDGET`. Its credentials are literal values in `deploy/.env` — an
  unresolved `op://` reference travels verbatim and 401s. Copy with `op read`, never
  `op run` (which masks the value).
- **Workday is the N+1 adapter** and runs last in `gather()`. Its ceilings (`MAX_DETAILS`,
  `MAX_PAGES_PER_QUERY`, paging) are what decide coverage, and the failure mode is silent —
  re-measure against the export window after changing them. (SmartRecruiters was the other
  one until it was removed on terms — see above. ~38 Workday sites are still
  found-and-not-added behind that same export-window measurement.)
- **Haiku for both enrichment passes** — well-calibrated at ~10× lower cost than Sonnet.
- Salary coverage is ~30–40%; no row is dropped for a missing salary.
- Metabase is local Docker only; portfolio evidence is screenshots + dbt docs.
- The claude.ai routine reaches Drive through the **owner's own connector**. The folder is
  owner-only and should stay unshared — sharing it is what would create third-party access.
- `service/api.py` is the pre-v1 internal API. It has no auth and is **not** deployed
  (`Dockerfile` runs `service.webapp`). Do not expose it without adding auth.

---

## Open items

Tracked from `notes/2026-07-18-security-review.md`. **Every item from that review is now
closed.** Do not reopen: confirm-token expiry *and* single-use; job-`url` scheme validation
in both the digest email and the `/matches` page; `pip-audit` / `npm audit` / Dependabot in
CI; off-box backup retention; **origin TLS** — replaced 2026-07-29 with a Cloudflare Origin
Certificate valid to **2041** (`deploy/cert-renewal.md`).

### Measured on 2026-07-29 and deliberately NOT fixed

Three long-standing items were quantified against production rather than re-argued. Each has
a stated trigger; **do not reopen without new numbers, and do not "fix" them on intuition.**
Full workings in `docs/jobdigest.md`.

- **Company name inside `search_tsv`.** Real cost: 4 of 360 shortlist slots. The fix removes
  employer search and needs a GIN reindex. **Trigger:** a subscriber whose role word is also
  a common company word — recheck per-subscriber, not corpus-wide.
- **On-site is unprovable, and the obvious fixes are actively harmful.** The refusal phrases
  do not exist in this corpus, and `onsite`/`in-office` occur *more* often in known-remote
  ads — a naive rule would strip genuine remote jobs from digests. The On-site chip still
  reaches the AI matcher as `work_setup`; only the SQL gate cannot act on it.
- **Category breadth** in `product` / `design` / `devops_platform`. ~2–3 off rows per 120.
  The structural cause is missing categories and the inventory is real (sales 659,
  cybersecurity 185, IT support 93), but the demand half is absent. **Trigger:** a role word
  appearing in `unmet_demand_terms()`.

Three things about the TLS setup are load-bearing and easy to undo by accident:

- **The private key was generated on the VPS and never left it.** Cloudflare's default flow
  displays a generated key in the browser once; that would put a credential in a transcript
  (security rule 2). Only the CSR was pasted into the dashboard. If the cert is ever
  re-issued, keep that shape.
- **The `tls` directive is committed, not a box-local edit.** `web/Caddyfile` is baked into
  the `web` image. It carries `import /etc/caddy/origin/*.caddy`; the box-specific line lives
  in `deploy/origin/tls.caddy` (gitignored, mounted `:ro`). A glob matching nothing is a
  no-op, which is what keeps local dev on automatic certs — do not replace it with a plain
  `import`.
- **The liveness healthcheck has to trust that cert, and didn't.** It now pins
  `--cacert .../origin/cert.pem` when present. **Pin, never `--insecure`** — under Full
  (strict) a wrong origin cert is a 526 for every user, and this check is what should catch
  it. It also re-alerts roughly hourly (`REALERT_EVERY`): **a monitor that can only fire once
  has a single point of failure at "you were looking".**
