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
web/              Next.js 15 static export; app/(site)/[locale]/ is the translated site,
                  app/(plain)/ the rest — two root layouts, no app/layout.tsx
  web/i18n/       the eight message catalogues + LOCALES; one definition per language
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
  what makes suppression safe: nothing vanishes, it just isn't emailed twice. The one thing
  that leaves the page is a job the subscriber hid — and it moves to `/hidden` rather than
  going anywhere (see below).

### Hiding a job is a move, never a delete

A subscriber ticks jobs on `/matches` — already applied, not interested — confirms, and they
leave both the page **and the digest**. They are listed on `/hidden`, where the same tick-box
flow puts them back. `matches.status = 'dismissed'` is the whole storage: the column and its
index predate the feature, so there is no migration, and `upsert_match` never writes `status`,
which is what stops the nightly re-score resurrecting a hidden job in tomorrow's email.
Four things hold it together:

- **`matched_jobs` and `match_count` carry the filter separately.** They are two queries
  behind one screen, so a filter added to one and not the other renders "12 matches" above a
  list that can only ever reach 10. `test_hidden_sql.py` executes both against a real Postgres.
- **The digest reads `matched_jobs` too**, so hiding is the subscriber's own answer to "stop
  sending me this" — narrower than a pause and finer than a preference change.
- **`prune_matches` no longer deletes `dismissed` rows.** It used to, back when nothing wrote
  the value; pruning one now would delete a row off a page the subscriber can see, and a
  reactivated posting would come back unhidden.
- **The write is scoped by `profile_id` in the WHERE clause** — that is the authorisation, not
  a convenience. Ids arrive from a browser; one that isn't theirs changes nothing rather than
  erroring, because a stale page re-submitting an id is ordinary traffic. `MAX_HIDE_IDS` (200)
  bounds one call, and it is refused whole rather than truncated.

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

**`COUNTRY_ALIASES` is wider than the EU-27 on purpose, and that is not a contradiction.**
Only EU-27 is ever *offered* as a preference. But "unknown country is kept" means a country we
cannot name is a country we cannot exclude — so naming one is the only way the gate can act on
it. That is why `switzerland`, `norway`, `ukraine` and `serbia` were already there, and since
2026-08-01 why the countries the enterprise ATS sources actually post from are too. Workday and
SmartRecruiters reach employers who hire globally, and their non-European requisitions arrive
saying "India, Bengaluru" or "Suzhou, China" and nothing more; every one of them was resolving
to *no country* and spending a slot in the ~120-posting shortlist of subscribers who can only
work in the EU, to be rejected by a model that had to read it first. **Adding an alias here
tightens the gate; it never widens what a subscriber may choose.** One name is deliberately
absent — `georgia`, because the country lookup runs before the city lookup and "Atlanta,
Georgia" would resolve to Tbilisi's country and vanish from every US subscriber's digest.
Before adding a name, check it is not also a city, a US state, or an ordinary word.

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

**Education is a fourth axis, and it is mostly unprovable — that is stated, not hidden**
(migration 014). `service/education.py` is the one definition, mirrored in
`web/lib/education.ts` and drift-tested. `postings.education_min` is
`secondary | vocational | bachelor | master | doctorate | null`; `profiles.education_levels` is
which of those a subscriber will accept, shaped exactly like `work_modes` — set membership, not
a ceiling, empty widens to all five, all five applies no filter. `profiles.education_field` is
free text that reaches only the AI matcher.

Measured before it was built, against 20 763 active postings on 2026-08-02:

| | |
|---|---|
| state any degree requirement | 952 (4.6%) |
| …softened ("or equivalent", "preferred") | 286 |
| **binding and readable** | **~666 (3.2%)** |

The ceiling is not employer silence: **70% of the corpus has no description to read.** Median
description length is 35 characters — `jobscz` (9 639 postings, the largest source) averages
32 and stores scraps like "70 000 – 80 000 Kč", `profesia` (4 365) stores the empty string,
`cocuma` (319) averages 9. **The entire Czech and Slovak inventory will hold `null` forever**,
so this filter is real for English-language ATS postings and inert for someone searching only
Czech boards. Three consequences:

- **Null always passes the gate**, as with `work_mode` and an unresolved city. It is ~97% of
  rows; a gate that dropped nulls would not narrow a digest, it would empty it. `test_education_sql.py`
  executes that against a real Postgres, because `x = any(...)` on a null column is `NULL`, and
  `WHERE` discards `NULL` exactly as it discards `false` — the string cannot show you that.
- **The classifier's only safe error is a miss.** A false positive deletes a job from an inbox
  for a qualification the ad never demanded, so a softened mention ("or equivalent" — 30% of
  them) and a self-describing employer both classify as null. Every false-positive case in
  `test_education.py` is real production text that classified *wrongly* first: an `<img
  src=".../ausbildung-1.svg">` benefits icon, English "maturity" caught by the Czech
  "maturita" pattern, a university advertising its own "doctoral degree programs", "220
  veterinarians, PhD nutritionists", "post secondary" (which means tertiary). **Where several
  levels are named, the lowest wins** — an ad is satisfied by the lowest, and it caps the
  damage of any blurb the rules miss.
- **A CV never narrows this server-side.** `cvparse` detects a level and the browser pre-ticks
  the boxes with it, visibly; `merge_into_profile` deliberately does not write
  `education_levels`, because a CV that failed to mention a master's would otherwise silently
  delete every master-requiring role from that person's digest.

Changing the patterns means re-running `python -m service.backfill_education`, or stored rows
keep the old answer while new ingests use the new one and the column means two things at once.

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

### Running it locally

`dev/` — three processes, no image rebuilds in the loop, nothing that can reach production.
Full instructions in `dev/README.md`; the whole of it is:

```powershell
.\dev\db.ps1 reset          # postgres :5433 — see below for why `reset`
. .\dev\env.ps1
python dev\seed.py          # 40 synthetic postings, a confirmed subscriber, matches
.\dev\api.ps1               # service.webapp on localhost:8811
.\dev\web.ps1               # next dev on localhost:3000
```

Four things that are decisions rather than convenience:

- **`reset` is the only migration path.** `schema.sql` is mounted into
  `/docker-entrypoint-initdb.d/` and Postgres runs it **only on an empty data directory**, so a
  volume older than migration 012/013/014 keeps the old schema for ever — and the symptom is a
  500 from a missing column, which reads as an application bug. `db.ps1 status` prints whether
  those columns exist, and `seed.py` refuses to run without them.
- **Both servers are on `localhost`, not one on `127.0.0.1`.** The session cookie is
  `SameSite=Lax`; the two spellings are different hosts and therefore cross-site, so the
  browser would refuse to send it. Same name, different port, is same-site. Never loosen
  `samesite` in `webapp.py` to make dev work — that is a production property.
- **`seed.py` lives outside `service/`** because the Dockerfile does `COPY service/`, and a
  script that creates a confirmed subscriber with a live `manage_token` must not be able to
  ship in the image. It also refuses any `DATABASE_URL` that does not name localhost. **Never
  copy the production database down** instead (security rules 1, 2, 5).
- **`next dev` is not what ships.** Production is `output: "export"` behind Caddy, same-origin
  `/api`, with the redirects and the CSP header. `npm run build` and
  `deploy/docker-compose.smoke.yml` are the two checks that see what `next dev` cannot.

### Deployment reality

`/opt/jobdigest` on the VPS is **not a git checkout**. Local `master` and the box can drift,
and have — a merged commit is not a deployed commit.

**Use `deploy/deploy.sh`. Do not hand-assemble an `scp` list.**

```bash
deploy/deploy.sh              # ship HEAD (refuses if dirty or unpushed)
deploy/deploy.sh --dry-run    # list what would ship
deploy/deploy.sh --sha <sha>  # roll back to a known-good commit
```

It ships the *committed tree* via `git archive | tar -x`, so "deployed" is always a commit you
can `git show`; it syncs the systemd units, which live in `/etc/systemd/system` and are the one
thing a tree sync cannot reach; and it health-checks with automatic rollback. Files git does not
track — `deploy/.env`, `exchange/`, `pgdata` — are untouched, because tar only writes archive
members. The host is not in the script: it comes from the gitignored `deploy/.deploy-target`.

**The failure a hand-rolled `scp` list produces is not a missing file, it is a stale one.**
On 2026-08-02 the three new ATS adapters were copied to the box and the deploy was verified by
md5 — per directory. `search_jobs.py` sits at the repo *root*, was outside every directory
checked, and is where `gather()` lists the sources. The adapters were present, importable, and
wired into nothing; the box ran 10 sources while `master` ran 13, and no check said otherwise.
If you must copy by hand, diff the **whole tree** in both directions — files on the box that
differ, *and* files in the commit that never arrived — and hash the result **inside the image**,
since the image is what runs.

**`.deployed-sha` is `deploy.sh`'s rollback target, not a comment.** If it is stale, a failed
health check restores whatever it names. It was found reading a 61-commit-old sha, which would
have reverted the box past an entire release. Deploying by hand and not updating it is how it
gets that way.

**The build context is a place code can go missing.** `.dockerignore` excludes `dbt/` from the
backend image, but `ingestion/sources/greenhouse.py` reads its curated board tokens from
`dbt/seeds/target_companies.csv` at runtime — so for the life of the image the largest source
loaded zero boards, caught its own `FileNotFoundError`, logged a warning and returned `[]`.
Nothing errored; the symptom was an absence in `select source, count(*) from postings`.
`test_curated_boards.py` now fails if the seed stops shipping. When a source's number looks
wrong, check whether its data even reached the image.

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
4. **The privacy policy is a specification.** `web/components/legal/PrivacyEn.tsx` makes
   concrete promises — no IP stored, CV discarded, unsubscribed profiles erased within 30
   days, data sent to the matcher carries no email. Changing retention, adding a processor,
   or storing a new field means changing that page in the same commit, or the policy becomes
   false. Since 2026-08-02 there are **two** copies: `PrivacyCs.tsx` says the same things in
   Czech, and `TermsEn`/`TermsCs` alongside them. Both must move together — a translation
   that lags is a policy that is false in one language, which is the failure the English
   original was never allowed to have. English is authoritative and the Czech copy says so,
   but that clause is a tie-breaker, not permission to skip the edit.
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
- `test_education.py` — a requirement nobody wrote down is never invented; every
  false-positive case is real production text that classified wrongly first.
- `test_education_sql.py` — a null `education_min` passes the real gate, on both the narrow
  and the widened retrieval path. SQL-backed, so CI fails if it goes back to skipping.
- `test_hidden_sql.py` — hiding is a move, not a delete: the two queries behind the page
  agree, a re-score cannot resurrect a hidden job, and one subscriber cannot hide another's.
  SQL-backed, so it skips without `TEST_DATABASE_URL` — and CI fails if it does.

When adding a guard, mutation-check it: break the thing deliberately and confirm the test
goes red. A test that cannot fail documents nothing.

---

## Known constraints and decisions

- **Adzuna** 250 req/day, one index per country (AT/BE/CA/DE/ES/FR/GB/IT/NL/PL/US — it has
  no CZ or SK index, which is what the Czech scrapers are for). A run costs
  `sum(pages) × len(SEARCH_TERMS)`, so the two lists multiply and one small-looking edit to
  each is a 30-call increase; `test_adzuna.py` fails if a change busts `DAILY_REQUEST_BUDGET`.
  **The AUTH_FAIL on 2026-08-01 was never a bad credential — it was an unresolved
  `op://` reference** being sent to Adzuna verbatim. The developer `.env` stores both values as
  1Password references, which resolve only under `op run --env-file=.env`; run any other way,
  the literal string travels as the app id. It left no trace either: a 401 was caught as an
  ordinary `RequestException`, logged per page, and `fetch` returned `[]`, which downstream is
  indistinguishable from a quiet day. 401/403 now raises `AdzunaAuthError`, and the constructor
  refuses an empty or `op://` value rather than spending the budget discovering it one 401 at
  a time.
  **Live on the VPS since 2026-08-03.** The box gets the *literal* values in `deploy/.env` —
  nothing there runs `op`, so a reference would reproduce the original failure exactly. They
  are runtime variables, not build args, so `docker compose run` picks them up with no
  rebuild. One run is 165 of the 250 daily calls, which is why a manual re-run on the same day
  can push past the quota; a 429 is swallowed into an empty list, so it would read as "Adzuna
  got quieter", not as an error. Copy them with `op read`, never `op run` — **`op run` masks
  secrets in the child process's stdout**, so piping a value through it writes
  `<concealed by 1Password>` into the target file.
- **If a source does not already permit us, we skip it. We do not write to publishers.**
  Decided 2026-08-04, and it settles a whole class of question rather than one source. A
  refusal (Alma Career, the Bundesagentur, worki.sk, EURES) is final, not an opening position;
  a source gated behind an application, a signed agreement, a verification step or a
  negotiated feed (Poland's CBOP, Finland's Työmarkkinatori) is **skipped on the same footing
  as a refusal**, because the thing standing between us and the data is correspondence and we
  do not do correspondence. Do not draft a letter, do not open a "pending a conversation"
  state, and do not record a source as *waiting* on anyone — waiting is what turns a closed
  question back into an open one every time a session reads the file. The permitted set is
  discoverable by reading terms, and it is the whole buildable set. What remains open without
  asking anyone is **curated employers on Greenhouse/Lever/Ashby/Recruitee/Workable/
  SmartRecruiters/Workday/Oracle** via `scripts/discover_ats.py` — that is how Slovak coverage
  was rebuilt after Alma Career, and it is the route for any country whose register is gated.
- **Source terms of use — read on 2026-08-03, and one of them is a real problem.**
  `ingestion/politeness.py` is the one place for the crawler's identity: `USER_AGENT`
  (`JobDigest/1.0` + contact URL + address — every adapter used to send a browser string, and
  the three scrapers a bare `Mozilla/5.0`), `robots_allows()`, and `throttle()` at 1 s/host.
  `test_politeness.py` fails if any adapter reintroduces a browser agent. **robots.txt permits
  every path we fetch** on jobs.cz (`/prace/`), profesia.cz/.sk (`/prace/`, `/praca/`) and
  cocuma (`/jobs/page/N/`); none declares a `Crawl-delay`. All three still return 200 to the
  honest agent — verified, because an identifier no board accepts is a dead source.
  **But robots.txt is not the binding document.** Alma Career's *Podmínky používání* — which
  govern **jobs.cz + prace.cz** (§4.11 CZ) and **profesia.sk/.cz** (§4.11 SK, identical text)
  — say a user may not *"zpracovávat automatizovaně data … ani jiným obdobným způsobem
  vytěžovat či zužitkovávat databáze Alma Career"* or *"načítat prezentovaná data … pro další
  strojové či automatizované zpracování"*, and §4.7(e) makes *"pokusy o automatické či hromadné
  čtení anebo kopírování obsahu"* grounds for termination. That is our two Czech/Slovak
  scrapers described exactly, and *vytěžovat / zužitkovávat* is the statutory language of the
  EU database right (96/9/EC), not just contract. **12 861 active postings, 33% of inventory.**
  **Decided 2026-08-03: both are excluded from `gather()`.** The adapters are kept as working,
  tested code — like `linkedin` and
  `eurojobs` — so reversing this is a decision, not a rewrite. `test_source_exclusions.py`
  fails if either returns to `gather()`, because re-adding one is a two-word edit that nothing
  else would notice. **The price is deliberate and severe: Czech inventory falls 10 402 → 874
  (−92%) and Slovak 3 362 → 29 (−99%).** Do not re-add either because the digest looks thin;
  re-add them when there is permission. Rebuilding CZ coverage means more curated Czech
  employers on Greenhouse/Lever/Ashby, or a board that permits it — not this.
  **The CZ gap is now partly filled, and not by another scraper** (2026-08-03). `mpsv` is the
  Úřad práce's own vacancy register, published as open data by the Ministry of Labour. Its
  DCAT metadata declares `databáze_chráněná_zvláštními_právy:
  není-chráněna-zvláštním-právem-pořizovatele-databáze` — the sui generis database right, the
  very right that puts Alma Career out of reach, **expressly disclaimed by the publisher** —
  plus `neobsahuje-autorská-díla`, and `robots.txt` is `Disallow:` (allow-all). 39 063
  vacancies nationwide; `ISCO_MAJOR_KEEP` trims to the 7 339 in ISCO major groups 1–3
  (managers/professionals/technicians), because the register is the whole labour market and
  loading the other 32 000 would put kitchen and warehouse work into the widened retrieval
  path, which drops the recall predicate entirely. **1 576 Prague, 308 Brno, 100% with a
  salary, 94% with a description** — against jobs.cz's median description of 35 characters.
  Two things that must not be undone: it declares `obsahuje-osobní-údaje` and every record
  names a contact person with a direct email and phone, so the adapter **never reads
  `prvniKontaktSeZamestnavatelem`** and scrubs contacts out of description text — storing
  either would add a personal-data category the privacy policy does not cover (rule 4); and
  `country_code="CZ"` is a source-level constant, which is normally wrong, but is the sound
  exception here because the Úřad práce registers vacancies in Czechia by statute. The
  per-vacancy portal link (`up.gov.cz/volna-mista-v-cr?id=`) is stable and unique but its
  deep-linking is **unverified** — the server returns an identical shell for a bogus id.
  `recruitee` is the other addition: the mid-size Czech employers (STRV, Trask, Twisto,
  Livesport) that no existing ATS adapter reached. Both found via `scripts/discover_ats.py`,
  which reads a company's ATS slug off its own careers page. Cocuma's terms are B2B and carry
  no such clause. **Remote OK's API terms require a
  *followed* link back and naming Remote OK as a source** — hence the source list in Terms §3
  and `rel="noopener"` (not `noreferrer`) on `MatchCard`, so the referral they ask for actually
  arrives. Terms §3a tells any board how to have us stop; that mailbox is `hello@jobdigest.eu`.
- **Reading a company's own careers page does not work, and the measurement is the useful
  part.** The idea recurs — "employer X has jobs on its own site, and it is one of many" — and
  it is worth answering with numbers rather than intuition. The scalable form would be
  **schema.org/JobPosting JSON-LD**, which publishers emit deliberately so machines can read
  them (that is how Google for Jobs works), and which needs *one* parser rather than one per
  employer. Measured 2026-08-04: **1 hit in ~400 companies** across the CZ and PL discovery
  runs, and **0 of 8** hand-checked Czech consumer brands. What is left is a bespoke parser per
  site, which breaks silently at every redesign; the pages are client-rendered anyway (Vilgain
  is 1.5 MB of HTML with no markers), so reading them needs a browser, which is the constraint
  that already closed SuccessFactors. And 3 of those 8 — Alza, Dr. Max, Mall — return **403 to
  the honest `JobDigest/1.0` agent**, which by this repo's own rule is a dead source. The
  `jsonld` column in `discover_ats.py` is effectively dead weight for the same reason: it can
  only see HTML that was fetched. Reach these employers through an ATS or not at all.
- **BambooHR is refused on terms, and it is the one that looked safest.** 19 companies and 121
  jobs verified live across the CZ+PL discovery runs (Apify, Codasip, Hangar 13, České dráhy,
  Dr. Max, Adastra, CD Projekt, Miquido) on a clean public JSON endpoint,
  `https://{tenant}.bamboohr.com/careers/list`, which answers 200 to the honest agent — and
  the per-tenant `robots.txt` **allows** it, disallowing only `/jobs/embed.php` and
  `/jobs/embed2.php`. The terms say otherwise. ToS **§4.2** forbids using *"any robot, spider,
  other automated device, or manual process to monitor or copy any content from the Service"*,
  and the **Developer ToS** forbids *"scrape or crawl BambooHR interfaces or content without
  BambooHR's prior written consent"* — consent this project does not ask for. **Third time
  robots.txt has permitted what the terms refuse** (Alma Career, EURES, now BambooHR): settle a
  source on the narrowest applicable clause, and never on robots.txt alone. Checked 2026-08-04.
- **Teamio is Alma Career, so it was already decided.** 31 Czech household names sit on it
  (Agrofert, Albert, ČEZ, Česká spořitelna) and it is tempting for exactly that reason.
  `cz.teamio.com` is an Alma Career product, and the binding document is the same one that
  excluded jobs.cz and profesia — *"Podmínky používání **Elektronických systémů Alma Career**
  pro uživatele"*, whose §4.11 forbids automated processing and whose object is *"databáze Alma
  Career"*, the company's databases generally rather than one portal's. `discover_ats.py`
  already marks it `RESTRICTED`; leave it that way and do not treat a Teamio hit as a free win.
- **Personio is unresolved, not refused — and still not built.** 8 companies, 27 jobs. Its
  marketing site returned **429 to every path tried** (`/legal-notice/`, `/imprint/`,
  `/terms/`, and the `.de` equivalents), so its terms could not be read at all. Permission
  unestablished is not permission, so it is skipped; but record it as *unresolved* so a future
  session re-reads the document rather than re-deriving the whole question. At 27 jobs it is
  not worth much effort either way.
- **Never use LinkedIn beyond its public RSS** — account ban risk, and never Playwright. As
  of 2026-08-01 that RSS returns 0 entries, as does EuroJobs (Cloudflare interstitial), so
  both adapters are **not wired into `gather()`**; the README used to list them as coverage.
  A source that fetches nothing is a gap counted as filled — measure before believing a list.
- **A source that returns a round, plausible number is the hardest kind of broken.** Four
  adapters were silently returning a fraction of what they hold, and none of them errored:
  - **Himalayas** capped at 20 for its whole life. It requests `limit=100`, Himalayas caps a
    response at 20 regardless, and the loop stopped on `len(jobs) < PAGE_SIZE` — so the first
    page always satisfied the stop condition, and the `offset += PAGE_SIZE` stride would have
    skipped 80 jobs a page had it continued. **Page by the count received, never by the count
    requested.** 20 → 300.
  - **RemoteOK** was reading 3 tags (`data`, `analytics`, `machine-learning`). A tag does not
    filter one pool, it *selects* which ~100 jobs you get, so the tag list is how many pools
    you read, not how narrow the result is. 177 → 540 across 12 tags.
  - **Jobicy** hard-caps at 100 with no offset; `industry` and `geo` are the only way past it,
    and an unrecognised value returns an empty list rather than an error (`design` is not a
    value it knows). 100 → 413 across 8 slices.
  - **Remotive**'s `?category=data` was being **ignored by the API** — filtered and unfiltered
    both return the same 34 rows across 13 categories. It narrowed nothing and misdescribed
    the source, which is its own kind of wrong.

  Three of the four were narrowed to data roles, left from when this repo served one person
  hunting data jobs. JobDigest matches nine categories and the shortlist recall predicate is
  `category OR keyword`, so a subscriber asking for design or sales could not be shown those
  postings — not because none existed, but because none were fetched. **When a source looks
  small, measure what it holds before believing it.**
- **Greenhouse/Lever/Ashby/SmartRecruiters/Workday** curated company lists, no domain-wide crawls.
  A board that goes dark is a **silent zero**: `fetch` skips a non-200 without an error-level
  log. Re-probe the lists rather than assuming (`dbtlabsinc` and `nubank` were both dead when
  the seed was last checked) — **`scripts/probe_boards.py` is what does that**, reading the
  lists out of the adapters themselves rather than a copy, so a list that grows is covered
  without editing it. It separates *dead* (answered, zero jobs — remove the row) from
  *unreachable* (no answer — re-run before editing), because a transient failure and a retired
  board look identical and only one is worth a commit. **Measured 2026-08-04: 239 of 239 boards
  live, 49 834 jobs — greenhouse 97/11 721, workday 25/19 892, ashby 77/5 598,
  smartrecruiters 8/7 501, oraclecloud 5/4 522, lever 18/541, recruitee 7/30, workable 2/29.**
  That is the baseline to compare against; "a source looks small" is only meaningful with one.
  **One blind spot, found by mutation-checking the prober rather than trusting it:**
  `oraclecloud` verifies the tenant and *not* the site — Vertiv's real tenant with the invented
  site `CX_999` returns 2 240 jobs, because Oracle ignores an unrecognised `siteNumber`. Same
  shape as Remotive's ignored `?category=` and Platsbanken's ORed filters. Every other ATS
  rejects a bad token outright (verified: bogus greenhouse/lever tokens and a bogus Workday site
  all read unreachable, and `nubank` still reads *dead* rather than live). To check an Oracle
  site, compare its count against the number in the `SITES` comment.
  **A live board is not evidence that it belongs to the company whose name it spells.**
  `discover_ats.py` guesses slugs from company names, and 12 of the 37 boards it found in the
  Polish pass on 2026-08-04 were somebody else: `greenhouse:ideo` is the American design firm,
  `greenhouse:sas` a security integrator in Canoga Park, `lever:blue` a LatAm staffing agency,
  `recruitee:dpd` the Dutch DPD. Worst, `recruitee:accenture`, `:ccc`, `:samsung` and `:wp`
  each answered with one or two Amsterdam/Berlin postings titled **"Senior Marketer
  (Sample)"** — Recruitee's demo content on an unclaimed vanity slug — and Recruitee *refuses*
  an invented slug outright, so those 200s are indistinguishable from real finds. `ey` shipped
  in the list for a day on exactly that basis. **Check the board's own postings for company
  and city before adding it**, never a live 200; `KNOWN_IMPOSTORS` + `test_recruitee.py` fail
  if any of the five returns, because the discovery CSV keeps proposing them with a job count.
  Separately, demo rows live *inside* genuine boards — `trask` is a real Prague consultancy
  and 3 of its 5 offers were "(Sample)"/template seed content — so `_DEMO_TITLE` drops those
  at normalize. It is deliberately narrow: open-application and talent-pool ads are posted on
  purpose and are left alone.
  Do not add a company that another adapter already carries —
  `clickhouse` and `qonto` are live on Ashby *and* on Greenhouse/Lever respectively, and the
  duplicate would occupy two rows and two shortlist slots even though `digest.dedupe_key`
  collapses it in the email.
- **SmartRecruiters and Workday are the N+1 adapters, and the sources of large-EU-employer
  inventory.** Neither list endpoint carries a description, so each posting needs its own
  detail call, and both are bounded the same way: a keyword parameter (`q` / `searchText`) to
  keep the pull tech-relevant, and a `MAX_DETAILS` ceiling sized *above* a normal run so it
  only bites on a bulk import. Both run last in `gather()` because they are by far the slowest
  and a failure there should not cost everything before it.
  **Run the list stage concurrently, not just the details.** Workday's list is 13 sites × 9
  terms = 117 independent queries; sequentially they cost ~500 s, more than the 1 200 detail
  calls after them, and the whole source took 730 s. Pooling the queries — paging still
  sequential *within* one, since each page decides whether there is another — brought it to
  312 s for identical output. A full international `gather()` is ~15 min and ~22 500 postings;
  the 05:00 export has until the 07:00 import, and `Type=oneshot` means systemd sets no
  start timeout, so the window is the only real constraint.
- **A national employment service publishing open data is the best source shape available,
  and there are two: `mpsv` (CZ) and `platsbanken` (SE).** Sweden's is Arbetsförmedlingen's
  JobSearch API — no key, robots 404, open data the agency calls "free for anyone to use".
  15 201 ads, 100% with company/date/description, **median description 3 775 characters**
  (Oracle 401, jobs.cz 35). Four things that bite: **`limit` caps at 100 and `offset` at
  2 000**, so one query reaches 2 100 rows and five of the seven occupation fields hold more
  — the way past it is **keyset paging** (`sort=pubdate-desc` + `published-before` set to the
  oldest ad seen), not a second filter; **two filters of the same family are ORed, not
  ANDed**, so `occupation-field` + `occupation-group` returns the *whole field* (2 710 vs
  1 183 for the group alone) — a filter silently doing nothing, exactly Remotive's
  `?category=data`, and it cost a run of 15 440 instead of 15 736 before the ceiling warning
  exposed it; `stats.limit` hard-caps at 30 while four fields have more than 30 groups;
  and **scope is SSYK occupation field**, the MPSV ISCO 1–3 call, keeping 7 of 21 fields.
  Personal data is handled as in MPSV: `application_contacts` (29% of ads), `employer.email`
  and `employer.phone_number` are **never read**, and free text is scrubbed — verified at
  0 leaks across 15 201 live descriptions.
- **Germany's Bundesagentur für Arbeit is the largest source in Europe (820 599 vacancies)
  and is excluded on terms — do not add it.** Its Jobbörse answers an undocumented public API
  at `rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs` with the well-known
  `X-API-Key: jobboerse-jobsuche`, and it works. But the BA's terms of use §2a(3) forbid
  *"use robots, web spiders or similar technologies, or to use existing communication or
  programming interfaces contrary to the BA's intended purpose, and thus to read out content
  from the portal or apps for the purpose of data collection and evaluation"*, and the BA has
  publicly called the community-documented interface "technisch wie rechtlich kritisch". Same
  bar as Alma Career, 55× the scale. Checked 2026-08-04.
- **EURES is refused on a carve-out inside a permission — the first source here to fail that
  way, so read the reason rather than pattern-matching it to the others.** ELA's portal answers
  an undocumented public API at `europa.eu/eures/api/jv-searchengine/public/jv-search/search`:
  no key, 2 835 594 vacancies, **846 936 in the EU-27 within ISCO 1–3**, descriptions in the
  list row (no N+1), per-posting NUTS 3 codes, and `occupationUris` accepting ISCO URIs that
  expand down the hierarchy. Nothing prohibits automated reading and `europa.eu/robots.txt`
  permits the paths. **The licence is what refuses it.** *"Re-use is authorised, provided that
  ELA is acknowledged"* runs on Commission Decision 2011/833/EU, which licenses **content owned
  by the EU** and then carves out the rest: *"To use or reproduce content that is not owned by
  the EU, you may need to seek permission directly from the rightholders"* and *"…if a specific
  content depicts identifiable private individuals."* The vacancies are the employers', routed
  via national PES — ELA owns the portal, not the corpus — and 27 of 50 sampled descriptions
  carried a named contact's email, so both clauses bite. MPSV's metadata disclaims the database
  right and Arbetsförmedlingen states the data is free to use; **nobody has licensed this
  dataset**, and it is not on `data.europa.eu`. Two consequences worth keeping: Germany is
  settled rather than debatable — ELA's notice points at the rightholder, the rightholder is the
  BA, and **an aggregator does not launder a refusal**; and had it been permitted it was still
  barely buildable, because `Crawl-delay: 10` on the `*` group puts ~17 000 requests at ~47 h
  against a 05:00→07:00 window, `resultsPerPage` caps at 50 and `page` at 200 (a hard 10 000-row
  ceiling per query, so Germany could not be pulled in one query anyway), and `publicationPeriod`
  takes enum strings (`LAST_WEEK`), not integers. **Closed, not pending** — per the skip rule
  above, nobody is being asked whether CC BY 4.0 reaches PES-supplied vacancy content. Checked
  2026-08-04, full workings in `notes/2026-08-04-eures.md`.
- **Poland's CBOP is the best-licensed source this repo has ever found and is still skipped —
  the licence and the access are two different gates.** The Ministry of Family and Social
  Policy publishes the Centralna Baza Ofert Pracy on `dane.gov.pl` under **CC BY 4.0**, updated
  daily, describing a WebService *"umożliwiającą podmiotom zewnętrznym pobieranie drogą
  elektroniczną, w zautomatyzowany sposób"* — a publisher inviting exactly this, more explicit
  than MPSV's disclaimer — and §1.2 of its *Warunki udostępniania* makes access free. Nothing
  here refuses us. But §1.5 requires emailing the completed terms to `apicbop@praca.gov.pl`
  **signed with a profil zaufany or qualified electronic signature**, and §5 demands **NIP,
  REGON, KRS and PESEL** — four Polish identifiers a Czech applicant does not have, with no
  field for a foreign equivalent — plus the IP of the accessing machine. That is an
  application, so it is skipped. Recorded because the temptation to reopen it is stronger than
  for any refused source: the data genuinely is free, and it is 8 003 EU-27 ISCO 1–3 rows.
  Three design facts kept in case the gate ever disappears: §3 answers **only 17:00–07:00
  local** (15:00–05:00 UTC in summer — the 05:00 export starts as it closes, so it would need
  its own earlier timer, not a slot in `gather()`), serves a 16:00 snapshot, and caps **20
  queries per cycle** against slices of country/voivodeship/labour office; §2 contractually
  forbids **publishing out-of-date offers**, making the Muse zombie problem an obligation
  rather than a preference; and transport is **SOAP**, one operation `Dane` at
  `oferty.praca.gov.pl/integration/services/oferta` (hand-rollable as an XML POST — no `zeep`).
  `robots.txt` is 404 at the host root. Checked 2026-08-04.
- **Check robots.txt at the host root, and quote the clause that would refuse you.** Both
  errors happened in one session: `europa.eu/eures/robots.txt` 404'd and was reported as a
  finding, when robots.txt is only ever authoritative at the root — where it exists, is 4 930
  bytes, and carries the `Crawl-delay` above; and ELA's one-line reuse permission was quoted as
  the licence answer while the sentence that guts it sat one page away in the policy it cites.
  A source's terms are settled by the narrowest applicable clause, never the friendliest one.
- **Oracle Recruiting Cloud is the one enterprise ATS here that is not N+1, and that is why
  it has no keyword ceiling.** Its list rows carry `ShortDescriptionStr` (86% non-empty,
  median 401 chars, Oracle-capped at 1 000), a real per-posting `PrimaryLocationCountry`, a
  `PostedDate` and a structured `WorkplaceTypeCode` — so a full pull of five tenants is ~20
  requests and 43 s for 4 225 postings. SmartRecruiters and Workday need `q`/`searchText` to
  bound their *detail* calls; with no detail calls there is nothing to bound, and it was
  measured rather than assumed: Workday's nine `SEARCH_TERMS` cost 9× the requests, removed
  only 22% of the corpus, and **lowered** the EU-27 share from 16% to 12%, because English
  tech phrases cut hardest into exactly the European industrial inventory subscribers can
  take. Four things that bite: a site is a **`(tenant, region, site)` triple** and the region
  is not `us2` by default — Honeywell's is `ocs`, Vertiv's site is `CX` and Cummins' `CX_1001`,
  none of it derivable; **the company name must come from the curated list**, because
  `LegalEmployer`, `Organization` and `BusinessUnit` are null on every tenant measured, so
  that column is data and not a comment; **Oracle caps a response at 200 rows whatever `limit`
  says**, so page by the count received or repeat the Himalayas bug; and `MAX_AGE_DAYS` is
  **180, deliberately not The Muse's 45** — ORC medians are 26–43 days and most tenants do
  expire, so 45 would delete live inventory, while 180 drops the genuine tail (Vertiv reaches
  791 days, Cummins 1 293). Only `ORA_REMOTE` may set `remote_signal`; `ORA_HYBRID` is a
  commute, and 64% of rows carry no workplace type at all (the label field is `''`, not null).
- **SAP SuccessFactors cannot be read without a browser — do not re-probe it.** It is the ATS
  behind Lidl CZ, Allegro, ZF, Brose and Deloitte, and the per-company career site *is*
  reachable once you have the `company=` parameter off the employer's own page
  (`lidlstiftuP2`, `allegrospz`, `brosefahrz`). But every route — `/careers`, `/career`,
  `/services/xhr/jobsearch` — returns a JS shell behind `loginFlowRequired`: 188 KB, 195
  `<script>` tags, **424 characters of visible text and zero job ids**. Playwright is out
  (LinkedIn rule), so this is closed. Checked 2026-08-04.
- **A Workday career site is a `(tenant, host shard, site slug)` triple, and the slug is
  unguessable.** Adobe's is `external_experienced`, NVIDIA's is `NVIDIAExternalCareerSite`.
  A brute-force sweep of 2 568 plausible combinations across 100 companies found **two**
  new sites; hand-verified triples found eleven. Do not try to derive them — verify and
  record. Also: `locationsText` reads "7 Locations" for a multi-office requisition (use the
  detail's `location`), and `remoteType` has a **"Flexible"** value that means the employer
  decides per hire, not that the job is remote. Only an explicit remote value may set
  `remote_signal`.
- **The Muse is the widest US source, and the only one that needs a freshness filter.** It
  never expires listings — a 2026-08-01 sample had a median age of 17 days and a tail back to
  March 2025 — and `query_shortlist_meta` orders by `first_seen_at`, so a 17-month-old row
  ingested today sorts to the top of the freshest bucket and is emailed as new. `posted_at`
  cannot catch it either, being null for 14 291 of 19 439 rows. `MAX_AGE_DAYS` at the source
  is the only place that still knows, which is also why an *undated* Muse listing is dropped
  rather than kept — the opposite of the call every other adapter makes for an unknown.
- **A source-level country constant is almost always wrong.** Arbeitnow was hardcoding
  `country_code="DE"` because it is a German board; measured 2026-08-01, only 207 of 375
  postings were actually German, and the constant *overrode* "London, England, United
  Kingdom" — because an explicit code beats the text in `resolve_location` — hiding 20 UK
  roles from UK subscribers and showing them to Germans. Resolve from the posting's own text
  and let unknown be unknown. The one sound exception is evidence that is unambiguous *in the
  posting*: a trailing US state code in a Muse location ("Austin, TX"), which names no country
  the resolver can read, and only when every listed place agrees.
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
