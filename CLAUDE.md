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
03:00 UTC  export   ingest sources → Postgres → write shortlists.json → Google Drive
~06:00     routine  claude.ai reads shortlists.json → writes picks.json      (no DB, no key)
07:00 UTC  import   pull picks.json → validate → matches → build + send digests
08:00 UTC  sources  per-source freshness + churn → alert if a source silently died
09:00 UTC  watchdog digest_runs → alert if any subscriber has had nothing for 3 days
01:30 UTC  backup   pg_dump → encrypt → off-box
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
- **It folds a *trailing* legal form and nothing more, so two sources that spell one employer
  differently defeat it** (measured 2026-08-07). `dedupe_key("Tobii AB")` ≡ `dedupe_key("Tobii")`,
  but **`"NEXER GROUP AB"` does not collapse to `"Nexer"`**, nor `"IVER ACCELERATE AB"` to
  `"Iver"`, nor `"SECURITAS SVERIGE AB"` to `"Securitas"`. National registers write the full
  legal name and ATS feeds write the brand, so **carrying the same employer on both a register
  and an ATS board risks a second email for a job already sent** — the failure this key exists to
  prevent, arriving by a route it cannot see: two sources naming one employer differently, rather
  than one source relisting. This is why seven Swedish boards found on 2026-08-07 were dropped
  again after measuring their platsbanken overlap, and it is a **standing check whenever a
  country has both** — the exposure in Czechia (mpsv + ATS) was measured the same day at 19
  overlapping employers, small but real. Widening the fold to match on a company *prefix* is not
  the fix: it would collapse genuinely different employers sharing a first word.
- **`/matches` is deliberately NOT deduplicated** and stays the complete record. That is
  what makes suppression safe: nothing vanishes, it just isn't emailed twice. The one thing
  that leaves the page is a job the subscriber hid — and it moves to `/hidden` rather than
  going anywhere (see below).

**The candidate window must exclude what was already emailed, in SQL** (`matched_jobs`'s
`exclude_sent`, set only by `build_digest`). `build_digest` reads `limit * 6` rows ordered by
score; suppression used to happen in Python *after* that cut, and since an emailed posting
stays `is_active` for ever, every job a subscriber had received permanently occupied a
candidate slot. **The window therefore tightened with tenure** — measured 2026-08-07 on a
subscriber 15 days in: **21 of 30 slots** held jobs already in their inbox, the 9 survivors
were all cross-id duplicates that `dedupe_key` then collapsed, and the digest went out with
**1 job instead of 5 while 25 unsent matches scoring ≥ 6 sat outside the window.** Nothing
raised, `sendable_n` recorded 1, and a throttled digest is indistinguishable from a quiet
day — so it read as the product going quiet on its longest-running subscribers. Two rules:
the flag is **opt-in and must never reach the web path**, because `/matches` is the complete
record and `match_count` carries the filters separately (that is the `hidden` bug in a new
place); and `already_sent_ids` stays in `build_digest` as the actual guarantee, so "never
emailed twice" does not depend on an optimisation staying switched on.
`test_digest_window_sql.py` executes both against a real Postgres.

**A source that changes its URL scheme re-creates its whole inventory**, because
`posting_id = md5(url)`. StartupJobs' 2026-08-06 rewrite moved the canonical link from
`startupjobs.cz/nabidka/{id}/{slug}` to `startupjobs.com/job/{id}`, so the next export
inserted **450 "new" postings that were the same 450 jobs** — 442 same-job-two-ids pairs,
both copies active until the old ones aged out ~7 days later. `digest_sends` keys on
`posting_id` and could not see it; `digest.dedupe_key` is what stopped a full digest of
already-seen jobs. Expect this whenever an adapter's URL changes: duplicate rows competing
for shortlist and candidate slots for one staleness window, and a spike in `first_seen_at`
that is not new inventory.

**And the new link was a 404 — for a whole day, on every startupjobs posting.**
`/job/{id}` without the slug is a clean 404 on both domains and to a browser agent as well
as ours; so are the id-only URLs in the site's *own* `sitemap/offers.xml`. Nothing catches
this: the API answered, the count was right, the country split was right, and **a stored URL
is never fetched again**, so a dead link is invisible until a subscriber clicks one. One was
emailed. Two rules came out of it. **A link must carry whatever the site needs to resolve
it** — `JOB_URL` includes `slug`, and the adapter is smoke-tested against live URLs, not
just live JSON. And **`posting_id` must hash something the employer cannot edit**: the slug
changes on a retitle (offer 106499 went `social-media-content-creator` →
`social-media-specialist` in two days, with a 302), so hashing it would mint a new posting
on every rename. startupjobs hashes `ID_URL` (the immutable `displayId`), which is also why
repairing the links churned **no** ids — and why `upsert_postings` now refreshes `url`,
without which a link stored wrong stays wrong for the life of the row.

**`service/source_watchdog.py` (08:00 UTC) is what makes both of these loud.** It reads the
expected source list from `search_jobs.source_classes` — never a copy, so a retired source
(jobscz, profesia) is silent rather than alerting daily until the mailbox is ignored — and
flags two shapes: `SILENT ZERO` (freshest row older than `--stale-days`, the 2026-08-06
case, which was previously caught only by luck) and `ID CHURN` (nearly everything a run
returned was first seen today, the 2026-08-07 case). **The churn ratio's denominator is
`seen_today`, not `active`**, and that is the whole design: the superseded rows stay active
for a staleness window, so against `active` the real incident reads 48% and no threshold
fires, while against rows-returned it reads 100%. Genuine sources sit at 1–16%.

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

**Location preferences live in `service/geo.py`** — countries (**EU-27 + EEA-EFTA + CH**), the
curated cities per country, the free-text→(country, city) resolver, and the SQL gate.
**The selectable set widened on 2026-08-04 and is no longer the EU-27.** The question a
subscriber is answering is where they may work without a permit, and for an EU citizen that
is the EEA plus Switzerland — so `CH`, `IS`, `LI` and `NO` are now offered. They were already
in `COUNTRY_ALIASES` so the gate could *exclude* them, which is exactly what made the gap
invisible: production held **28 active Swiss, 23 Norwegian and 2 Icelandic postings that no
subscriber could ask for**. Two consequences. The `eu` remote scope resolves against the
**EEA set** (`EEA_COUNTRIES`, see the 2026-08-05 note below — it was `COUNTRIES` until GB/US
were added), and its label reads "Anywhere in the EU or EEA" in all eight catalogues — a copy
change is part of this, not an afterthought. And
**`island` must never be added to `COUNTRY_ALIASES`**, though it is Icelandic and German for
Iceland: matching is per token n-gram, so it would resolve "Long Island, NY" and "Rhode
Island" to IS and delete them from every US subscriber's digest. That is the `georgia` rule,
and `test_geo.py` now pins it. A subscriber picks
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

**The United Kingdom and the United States became selectable on 2026-08-05, and the product
now serves UK and US citizens as well as EEA ones.** GB and US were "stored but not offered"
for the project's whole EEA-only life (a London- or Austin-onsite role needed a visa and was
correctly hidden); they are now first-class, because a US citizen wants on-site US roles, not
only remote ones. Two things make this safe rather than a scope leak. **(1) `COUNTRIES` and
`EEA_COUNTRIES` are now separate sets.** `COUNTRIES` (33) is what a subscriber may pick;
`EEA_COUNTRIES` (31) is the EEA + CH and is what the **`eu` remote scope** means — so picking
the US for on-site work never turns "Anywhere in the EU or EEA" into a channel for US
*fully-remote* roles. Conflating them would silently widen that scope; `test_geo.py` and
`test_geo_sql.py` both pin the split. **(2) US/GB curated cities were MOVED, not copied.** The
selectable US metros and GB cities live in `CITIES` now; the same names had to be *removed*
from `FOREIGN_CITIES`, or `check_no_shadowed_cities` raises at import — a name may not sit in
both. Names that are also a US *and* a UK city (`cambridge`, `birmingham`) and the Bay Area
suburbs stay in `FOREIGN_CITIES` as country-only (no slug), because a wrong guess between two
now-selectable countries is newly visible. Adding these countries needs
**`python -m service.backfill_geo`** on the box: existing US/GB postings already carry
`country_code` (so country-level filtering works at once), but their `city` is null until the
backfill re-resolves "San Francisco" → `us:san-francisco`. No privacy-policy change is
triggered — no new data category, processor, or retention.

**Canada became selectable on 2026-08-07, and it was a geo change rather than a source one.**
CA had sat in `COUNTRY_ALIASES` only, so the gate could resolve and *exclude* it — which meant
**1 946 active Canadian postings already in the corpus reached nobody**. Adding it surfaced them
with no new adapter, no new request and no new data category, so no privacy-policy change was
triggered (security rule 4: a new *value* in `profiles.countries`, not a new field, processor or
retention). It follows the GB/US precedent exactly, including the part that is easy to get wrong:
**`COUNTRIES` gains CA and `EEA_COUNTRIES` does not.** That set is what the `eu` remote scope
means and `eu` is the *default* scope, so merging them would have started sending Canadian
fully-remote roles to every existing EEA subscriber, silently. Curated cities were chosen from
what production actually carries — Toronto ~461 rows, Vancouver ~100, Montréal ~31 — plus
Calgary, Ottawa and Winnipeg because a subscriber expects to pick them, and all of them were
**moved** out of `FOREIGN_CITIES` rather than copied, or `check_no_shadowed_cities` raises at
import. Two flagged collisions were settled by measurement rather than argument: `vancouver` is
also Vancouver, Washington and `edmonton` is also a north-London district, and **all 128
Vancouver rows and all 20 Edmonton rows in production are Canadian**. Left country-only on
purpose: `mississauga` (a Toronto suburb — the Bay Area precedent), `quebec` (a province as well
as a city, so a slug would repeat the `ontario` bug), `waterloo` (which `test_geo.py` names as
the canonical shadowing hazard — Belgium, and a London station), and the two provinces.
`ontario` stays absent from both tables. The `"ca"` subdivision code had to go in the same
change; see the `SUBDIVISION_CODES` note above. **`python -m service.backfill_geo` is not
optional**: every CA row already carries `country_code`, so country filtering works on deploy,
but `city` is null for all 1 946 by construction — `FOREIGN_CITIES` only ever returned a
country — so until it runs, "Canada → Toronto only" silently means "Canada, anywhere".

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

**`FOREIGN_CITIES` and `SUBDIVISION_CODES` are the same rule for postings that name no
country at all** (2026-08-04). `COUNTRY_ALIASES` only works when the ad writes the country
out, and the largest group in production did not: 7 147 of 30 043 active postings had a null
`country_code`, **4 681 of them Greenhouse and 1 134 Ashby**, and the top strings were "San
Francisco" (275), "London" (218), "New York" (167), "Chicago" (114), "Toronto" (89). None of
them European, all of them kept by the gate and handed to the AI matcher, each one spending a
slot in the ~120-posting shortlist of a subscriber who can only work in the EEA. `uk` and `us`
were not aliases either, which alone accounted for "London, UK" (272) and "Remote - US" (558).
Four rules, and every one is the `georgia` rule in a new place:

- **They resolve a country and never a city.** We offer no cities outside the EEA, so there is
  no slug to return and nothing here can reach the picker. Deliberately **not** mirrored in
  `web/lib/geo.ts` — it is resolver-only, and `test_geo.py` says so.
- **They run last**, after the country and selectable-city lookups, so they cannot overrule an
  EEA answer. Berlin is Germany before either table is consulted.
- **A name in both tables is refused at import.** `geo.check_no_shadowed_cities` raises rather
  than choosing, because a European city resolving to another continent would delete it from
  the digest of everyone who chose that country with nothing failing.
- **A trailing subdivision code fires only on the final token**, and **`DE`, `MT`, `NL`, `SK`
  and — since 2026-08-07 — `CA` are excluded from it.** Delaware, Montana, Newfoundland and
  Saskatchewan are also Germany, Malta, the Netherlands and Slovakia. **`CA` (California) joined
  them the moment Canada became selectable, and it is the sharpest of the five**: with
  `SUBDIVISION_CODES["ca"] = "US"`, `"Kelowna, BC, CA"` resolved to the **United States** — a
  British Columbia posting filed as American, deleted from the Canadian who wants it and
  delivered to the American who cannot take it. That is the Delaware failure exactly.
  `test_no_subdivision_code_can_shadow_a_country_anyone_may_select` fires on it, and **the test
  was not weakened — the code was removed.** The cost was measured, not estimated: dropping it
  left **22 locations / 77 active rows** with no country. Most California strings never needed
  it ("San Francisco, CA", "Los Angeles, CA", "Palo Alto, CA" all resolve from the city), and
  the four recurring towns that did — `el segundo` (44), `poway` (10), `lompoc` (3), `fresno`
  (2) — are now country-only in `FOREIGN_CITIES`. **`irvine` is deliberately not among them**:
  Irvine is also a town in Scotland, and with GB and US both selectable that is the
  `cambridge`/`birmingham` rule. Unknown is *kept*, so the residue is a precision cost on ~30
  rows, never a deletion. Every remaining collision (`IL` Israel, `IN` India, `MA` Morocco,
  `CO` Colombia) is with a country nobody can select, so being wrong there is invisible.

Note what this does *not* do: it adds no country to the selectable set, and it removes nothing
from anyone's digest that they asked for. It only lets the gate act where it previously could
not. `python -m service.backfill_geo` is what applies it to stored rows.

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
- **Personio is unresolved, not refused — and still not built** (re-checked 2026-08-06). Its
  marketing site returned **429/403 to every path tried** (`/legal-notice/`, `/imprint/`,
  `/terms/`, and the `.de` equivalents), *again*, so its governing terms still could not be
  read. Two findings from the re-check that make it worse than merely unresolved: the public
  `{tenant}.jobs.personio.de/xml` feed **answers with a job count for bogus slugs**
  (`amazon`, `johnson`, `kbc` — none are Personio customers), so a live 200 is not evidence
  the board is that company's, a recruitee-style impostor trap with no `hiringOrganization`
  to check against; and the feed's *documented* consumers are the employer's own website and
  Personio's multiposting **partners** (GoHiring/JOIN), not arbitrary third parties. Permission
  unestablished is not permission → skipped. Do not build it on the strength of the feed
  merely working. The genuine thin-country firms found on it (Intigriti BE, Kevin LT, IQM FI —
  though IQM is also on Teamtailor and taken there) are the reason the temptation recurs.
- **Teamtailor IS built and permitted** (`ingestion/sources/teamtailor.py`, added 2026-08-06).
  Settled on its terms, not robots alone: Teamtailor publishes **no Terms of Service** governing
  career-site visitors (footer is Privacy/Cookie/Security/Conduct/Modern-Slavery only), its
  career-site `robots.txt` **allows** `/jobs/` and carries `Content-Signal: search=yes,
  ai-input=yes` (only `ai-train=no` — and we do not train), and the keyless public JSON Feed at
  `{tenant}.teamtailor.com/jobs.json` exists to be read. It is the same shape as
  recruitee/workable — a curated multi-country ATS in the `include_cz` block — and reaches the
  thinnest selectable countries (EE/LV/FI/DK) whose product companies run Teamtailor rather than
  a supported ATS: 11 identity-checked tenants, ~103 postings (LV 28, FI 10, EE 6, DK 2). The
  feed carries the real `hiringOrganization.name` (so identity is off the feed, never the slug),
  a per-posting ISO country, `baseSalary` and `datePosted`. No source `remote_signal` (the feed
  has no structured remote flag — left to the downstream text classifier), and talent-pool /
  "submit your CV" rows are dropped by title. `discover_ats.py`/`inspect_hits.py`/
  `pending_boards.py` now verify it, and `test_teamtailor.py` pins the guarantees.
  **But "identity is off the feed, never the slug" is not sufficient, and 2026-08-07 is when that
  became clear.** It catches a slug *collision* — probing Norwegian names, `norr` self-identified
  as "Svensk Markservice AB" (grounds maintenance around Umeå) and `remarkable` as "REMARKABLE
  RETAIL" (Swedish mystery-shopper gigs), and both were rejected on that basis. It does **not**
  catch an **abandoned trial tenant registered under the real company's own name**: `akerbp`,
  `jotun` and `salmar` each returned 11 postings **sharing 10 identical titles**, 5–6 of each
  board's descriptions were *Teamtailor's own product pitch* ("Teamtailor is an Employer Branding
  & ATS SaaS platform…", dated 2023) — and `akerbp.teamtailor.com/jobs.json` is titled
  **"Aker BP"**, so the identity check *passes*. A board can be honestly named and hold nothing
  but demo content. `_DEMO_CONTENT` therefore keys on the **body, never the title**: a title rule
  was available and would have been wrong, because `volue` genuinely advertises "Software
  Engineer". Measured when written: **0 of 12 known-good tenants** match a single item. This is
  Recruitee's `(Sample)` problem in a form the existing guard cannot see, and the general rule is
  **a marker-free demo posting is caught by what the description sells, not by what the job is
  called.**
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
  board look identical and only one is worth a commit. **Measured 2026-08-05 (end of day): 436
  of 436 boards live, 76 996 jobs — workday 61/36 816, greenhouse 149/15 445,
  smartrecruiters 9/7 692, ashby 132/8 071, oraclecloud 10/6 851, lever 36/1 488,
  workable 20/500, recruitee 19/133.** (239/49 834 on 2026-08-04 → 367 mid-day → 436 after the
  US-HQ, GB on-site and DE passes below once GB/US became selectable.)
  That is the baseline to compare against; "a source looks small" is only meaningful with one.
  **NO pass, 2026-08-07 — the first deliberate Norwegian sweep, and the yield concentrates in one
  ATS.** 112 employers probed → 57 live boards → 52 supported → **27 wired** (20 teamtailor,
  2 lever, 2 ashby, 1 workable, 1 greenhouse, 1 oraclecloud), of which **20 are
  Teamtailor** (against 5 Lever and 4 Workable found): Teamtailor is where the Nordic mid-market sits,
  so probe it first for any Nordic country. Measured live, the 20 tenants return **157 rows, 66 of
  them Norwegian** — against **23 active NO postings in the entire corpus** beforehand — plus
  FI 3, DK 3, PT 2, LT 2, RO 1 in the thinnest countries. `lever:bekk` is the single best board of
  the run and the only 100%-Norwegian one (Oslo/Trondheim consultancy, ISCO 1–2 by construction).
  **Eleven live boards were rejected and the three-way split is the reusable part:** *wrong
  company* — `lever:dnb` is **Dun & Bradstreet**, not Norway's largest bank (a three-letter slug is
  an initialism before it is a company), `recruitee:tgs` is a gaming/BPO outsourcer and at **614
  jobs** was the run's biggest trap, `workable:boost` is in Wellington NZ, `workable:crayon` is
  Boston, `greenhouse:bw` is a São Paulo asset manager; *wrong country* — `recruitee:sweco` is
  genuinely Sweco, entirely Dutch; *wrong inventory* — **the Oracle Posten Bring board is
  genuinely Posten Bring and 17 of 18 rows are Norwegian, and is still refused**, because the roles
  are yrkessjåfør, terminalarbeider and postbud. That is the most tempting rejection in the file:
  perfect country match, wrong work. Same call as `workday:jlp`.
  **Four Workday sites (Jotun, AutoStore, Storebrand, Equinor) were found and deliberately not
  added** — the 2026-08-07 note says to time the export before adding anything else to the
  slowest N+1 source, and that measurement still has not been taken.
  **SE + DK/CH/FI/IE pass, 2026-08-07 — 264 employers in two runs, 31 boards wired, 770 rows
  measured through `build_row`.** The countries that needed it most moved most: **CH 79 rows
  against 160 active in the whole corpus, DK 45 against 146, FI 33 against 210**; SE 286, IE 19.
  Best finds: `oraclecloud:iaaras` (ELCA Lausanne, 69, Zurich/Bern/Basel — the best Swiss board
  found), `greenhouse:solita` (47, FI 19 + SE 15 + DE/BE/DK/NO, entirely EEA),
  `greenhouse:proton` (74), `teamtailor:lunar` (the Danish bank), `oraclecloud` Milestone Systems
  (44, DK 15).
  **Sweden had to justify itself before it could be swept, and the measurement cuts both ways.**
  Platsbanken is 16 296 of Sweden's 16 588 active postings, so the question was whether the
  register already holds what an ATS sweep would find. It does not reach the tech employers —
  **0 platsbanken rows for Spotify, Northvolt, Truecaller and Epidemic Sound**, 2 for Klarna, 7
  for Ericsson — because reporting is voluntary in practice. But it *does* hold the consultancies
  and large employers, and that killed seven of the boards it justified: see the `dedupe_key`
  note above. **Varbi, Sweden's academic/public-sector ATS and the analogue of Norway's Jobbnorge,
  is redundant for the same reason** — platsbanken already carries 2 257 rows from 323 Swedish
  universities, municipalities and regions. Jobbnorge stays worth trying precisely because no
  Norwegian register covers those employers.
  **Blocket.se refuses in `robots.txt` as prose, word-for-word like FINN.no — and both are
  Schibsted.** *"Crawling blocket.se is prohibited unless you have written permission. Användning
  av automatiserade tjänster såsom robotar, spindlar, indexering eller liknande … är inte tillåtet
  utan föregående skriftligt tillstånd."* The group rule: Schibsted's **classified marketplaces**
  refuse automated use in both countries, while their **own recruiting boards** are ordinary ATS
  boards (`teamtailor:schibsted` is their internal hiring and is carried) — do not conflate them.
  **Nineteen more Workday sites were found across these five countries** (SE 1 227 postings,
  DK/CH/FI/IE ~1 537) and all deliberately not added, for the same untaken measurement.
  **FI deep pass, 2026-08-07 — the thinnest country relative to its economy, and now closed on
  every door.** Finland held **210 active postings from 26 employers**, the most concentrated
  inventory of any selectable country. A second list of 81 fresh names (after the 38 in the
  four-country run) gave 30 live boards → 27 supported → **12 wired → 82 FI rows, +39%**, and
  professional throughout: `granlund` 33 (building-services engineering), `patria` 18 (defence),
  `insta` 14, `eficode` 8, `ensto` 8.
  **Its three rejections are the best slug lessons in this file.** `teamtailor:ssh`
  self-identifies as **"French Express"**, a Stockholm restaurant group whose rows are *Kock* and
  *Sommar Bartender Extra* — not SSH Communications Security of Helsinki. `teamtailor:career`,
  probed for Innofactor, self-identifies as **"Teamtailor"**: a maximally generic slug resolves
  to the ATS vendor's own careers page. And `teamtailor:wihuri` **is** genuinely Wihuri and was
  rejected anyway — on inventory, not identity (district service mechanics, an open application).
  **Finland's non-ATS routes, all closed.** Already on record: Työmarkkinatori is gated *and*
  robots-disallows `/api/`; Jobly.fi is Alma Career; Duunitori 403s. Added 2026-08-07:
  **Oikotie Työpaikat** forbids *"säännöllinen, järjestelmällinen tai jatkuva tietojen
  kerääminen, tallentaminen, indeksointi, jakelu tai muu tietojen kokoaminen … ilman Oikotien
  antamaa kirjallista lupaa"* while its `robots.txt` is allow-all — **the tenth** such case — and
  it is part of **Vend**, making it the *third* ex-Schibsted marketplace refusing in the same
  terms after FINN.no and Blocket.se; **Kuntarekry** renders 1 752 visible characters and is
  municipal healthcare anyway; **valtiolle.fi** renders 1 932 of 228 KB; **Aarresaari** moved its
  board to JobTeaser and its sitemap holds 94 informational pages and no vacancies. And
  **`avoindata.fi` — which the twelve-portal open-data sweep above missed** — holds statistics
  plus exactly one genuine vacancy dataset, the City of Vantaa's **CC BY 4.0** REST API, whose
  documented endpoint now serves an unrelated HTML page. Perfect licence, dead endpoint: the
  Sicily case in a third form.
  **LV pass, 2026-08-07.** Latvia held **68 active postings**. 55 employers probed → 16 live
  boards → 12 supported → **3 wired → 56 rows, 55 Latvian, 48 in Riga: +81%**. The Latvian
  telcos are where the professional hiring is — `teamtailor:tet` 33 (product owner, AI services
  owner, Red Team Lead) and `teamtailor:lmt` 10 (systems analyst, IT transformation architect,
  senior .NET). `teamtailor:bitelatvija` 13 is a deliberately marginal keep at a ~50/50
  office/shop-floor split.
  **The largest Latvian board found was rejected**: `teamtailor:rimilatvia` is 100 rows,
  genuinely Rimi, and entirely supermarket shop floor — dishwashers, cooks, checkout
  supervisors, central-kitchen production. It would have been the biggest single LV number in
  the corpus. `ashby:bite` is a London board while `teamtailor:bitelatvija` is the Latvian
  operator — **the third slug live on two ATSes with only one right**, after `sunday` and
  `lunar`. Also rejected: `ashby:maxima` (San Mateo), `recruitee:grid` (esports betting, not
  Grid Dynamics), `recruitee:accenture` (Recruitee seed content).
  **Twenty-five Workday sites are now found-and-not-added across these six countries** (SE 1 227,
  DK/CH/FI/IE ~1 537, FI a further ~400), all pending the same untaken export timing. That
  measurement is now the single biggest blocked lever in this file.
  **The 13-country pass, 2026-08-07** (`notes/2026-08-07-thirteen-country-pass.md`) — GR, HU,
  BG, HR, SI, LU, MT, CY, PT, EE, LT, IS, LI, the last selectable countries with no dedicated
  employer list. **297 employers probed → 106 live → 81 supported → 40 wired → 892 rows.**
  **Malta 23 → +108 and Cyprus 52 → +55**; GR +120, PT +54, LT +45, HU +25, EE +18. Best boards:
  `workable:payabl` (66, Limassol 30 — the best Cypriot board found), `ashby:leovegasgroup`
  (73, MT 28), `greenhouse:betsson` (126: MT 69 / HU 18 / GR 16), `workable:greenvolt` (64,
  Lisbon 24), `recruitee:intralot` (40, Athens 31), `ashby:surfshark` (22, Vilnius 21).
  **Two zero-yield countries, and both are findings rather than gaps.** Iceland returned one row
  because its ATS market is 50skills (unsupported), BambooHR (refused) and bespoke portals — what
  it has sits in three Workday boards. **Liechtenstein returned zero, predicted before the run**:
  its eight professional employers are the Hilti/Ivoclar/Presta/LGT tier, which runs
  SuccessFactors or Workday. Do not re-derive either.
  **The one-word-slug rule is now overwhelming.** Fourteen rejections in one session, every one
  caught by the board's own postings: `ashby:span`, `titan`, `genesis` (and `lever:genesis`, a
  *third* company), `catena`, `post`, `wizz`, `light`, `odyssey`; `greenhouse:link`, `ses`,
  `agr`, `tempo`; `teamtailor:origo`, `otp`. **And `ashby:bite` answered twice in one day** —
  probed for Bitė Latvija and for Bitė Lietuva, and it is a London founding-engineer board both
  times. New tell worth keeping: `ashby:gr8-tech` was dropped for its Greenhouse twin because its
  board carries literal **".NET Developer Test"** and **"test Job title"** rows — an ATS being
  trialled, not run. And `ashby:tempo-io` is **findable only via the careers-page read path**,
  because `slug_candidates("Tempo", "tempo.io")` returns just `tempo`, which is a different
  company.
  **Batch D was gated and deliberately not run**: GR and HU repaid their lists, but BG, HR and SI
  produced **one board between them from 54 names**. That is the Romania shape, and N is a
  ceiling rather than a quota.
- **Parallelising discovery means more workers, never more processes.**
  `ingestion.politeness._last_request` is a module-level dict behind a `threading.Lock`, so the
  1 s-per-host guarantee holds **within one process only**. Company domains are all distinct
  hosts, so `--workers` scales nearly free; but the ATS-probe half of `discover_ats.py` lands on
  a handful of *shared* hosts (`api.greenhouse.io`, `api.lever.co`, `api.ashbyhq.com`,
  `jobs.workable.com`, `api.smartrecruiters.com`), and N concurrent processes would hit each at
  N req/s. **Run several countries by concatenating their CSVs into one run**, which is what
  `scripts/dk_ch_fi_ie_companies.csv` is. The same rule blocks running `inspect_hits.py`
  alongside a discovery run.
- **`scripts/pending_boards.py` reads the adapters' list structures, and must never go back to
  grepping their text** (fixed 2026-08-07). The old version searched one lowercased blob of every
  file under `ingestion/sources/` for the token, which was wrong twice: **a slug named in a
  comment counted as carried** — and these adapters document the boards they *rejected*, so an
  impostor's slug read as already wired — and **the ATS was ignored**, so a token carried on
  Teamtailor counted as carried on Workday. Both directions produce a *false* "already carried",
  which silently drops a genuine board from the report; four sessions running hit it
  (`ashby:post`, `recruitee:max`, `workday:thales`, then `ashby:novo`/`ashby:julius`/
  `workday:zendesk`). `--self-test` pins 11 cases so a renamed list attribute fails loudly rather
  than reporting everything as new. It also reports **same-company-on-two-ATSes**, which the NEW
  list structurally cannot show because both rows are new — that caught Frontify, Lunar, ICON,
  ELCA and Too Good To Go in one run.
  **FR/GB/US pass, 2026-08-05.** Curated employers via `scripts/discover_ats.py`, the same
  route as every country before. FR is genuine EEA inventory (Airbus, Air Liquide, Pennylane,
  Veepee, Doctrine, Exotec…); **GB and US are gated out** — post-Brexit the UK is not in the
  selectable set (EU-27 + EEA-EFTA + CH; it is in `COUNTRY_ALIASES` only, so the gate excludes
  UK-located rows exactly like US ones), so those two were added only for their EEA-office and
  fully-remote roles (Octopus Energy DE/ES/IT, Toast/Okta Dublin, Snowflake Warsaw, Sentry
  Vienna…). Every N+1 Workday GB/US board (Barclays, Lloyds, Abbott, J&J, Amgen, Medtronic,
  Nike) was **skipped** — their US/UK-onsite bulk would consume the shared detail budget on
  rows the gate drops. The SK/PL/IT re-runs added 8 (DT IT Solutions Košice on SmartRecruiters
  is the one that matters — Slovakia holds ~29 postings). `scripts/pending_boards.py` splits a
  discovery CSV into new vs already-carried, and `scripts/inspect_hits.py` prints each board's
  postings' cities for the identity check that a job count cannot do.
  **One blind spot, found by mutation-checking the prober rather than trusting it:**
  `oraclecloud` verifies the tenant and *not* the site — Vertiv's real tenant with the invented
  site `CX_999` returns 2 240 jobs, because Oracle ignores an unrecognised `siteNumber`. Same
  shape as Remotive's ignored `?category=` and Platsbanken's ORed filters. Every other ATS
  rejects a bad token outright (verified: bogus greenhouse/lever tokens and a bogus Workday site
  all read unreachable, and `nubank` still reads *dead* rather than live). To check an Oracle
  site, compare its count against the number in the `SITES` comment.
  **Refined 2026-08-07: an unrecognised `siteNumber` falls back to the *whole tenant*, and a
  recognised one returns its subset.** ELCA's `CX_1` returns 69 while the invented `CX_99999`
  returns 85. Vertiv and DNV read as "the parameter is ignored" only because those tenants are
  single-site, where both answers coincide. So the mutation check is still the right move — it
  just tells you whether the tenant has more than one site, not whether the site is real.
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
  detail call, so both need a way to decide which postings are worth one. Both run last in
  `gather()` because they are by far the slowest and a failure there should not cost
  everything before it.
  **Both were bounded by an ATS keyword parameter, and on 2026-08-04 both bounds turned out
  to be wrong in ways nothing could see.** The shared lesson: *a search parameter belonging
  to somebody else's ATS is not a filter you control, and a ceiling you never watch is a
  ceiling you cannot tell from a total.*
  - **SmartRecruiters had the Himalayas bug.** `_list` issued **one** `limit=100` request per
    (tenant, term) with **no `offset` at all**. `totalFound` for "software engineer" at
    BoschGroup is 1 866 and we took 100 of it, on every run since the adapter was written.
    Worse, `q` was never a filter: it is a *ranked* full-text search — "software engineer"
    returns 1 866 of BoschGroup's 4 714 and "data engineer" returns 2 109, and both hand back
    the same first row — so the term matrix neither bounded the pull nor selected tech roles.
    It now **pages the whole tenant** (88 list requests against the old 96 — *cheaper*, for
    all 7 503 postings instead of a truncated 1 671) and decides relevance locally on the
    title via `TECH_TITLE`. That filter is load-bearing, not tidiness: these are industrial
    employers and the rows it drops are "Manifold Assembler I", "MSR-Techniker
    Gebäudeautomation" and "HVAC Specialist", which would otherwise land in the *widened*
    retrieval path that drops the recall predicate entirely — the reason `mpsv` keeps only
    ISCO 1–3. Measured end to end: **7 502 listed → 2 355 tech-titled → 1 943 inside
    `MAX_AGE_DAYS`, in ~6 min.** Note the honest size of the win: 1 671 → 1 943 is +16%, not
    the 7 501 the board holds. Most of an industrial board is not for this product.
  - **Workday's `MAX_DETAILS` was described as sized above a normal run and was three times
    below one.** `probe_boards.py` reports 19 892 open postings across these sites; production
    held 1 454 active Workday rows, 7%. Raised 1 200 → 6 000, and `MAX_PAGES_PER_QUERY` 3 →
    10. Measured after the change: the list stage reaches **15 634 unique postings across 38
    sites in 8 minutes** — 79% of everything those boards hold — so `MAX_DETAILS` is now the
    only thing deciding what we take, which is where that decision belongs.
  - **Workday never signals the end of a result set — it keeps serving full pages**, which is
    why raising the page ceiling was nearly free of *benefit* before this was fixed.
    `philips`, whose whole board is 1 017 postings, answered 4 000 hits for one search term
    and stopped only when the measuring harness did; its nine terms returned 9 304 rows of
    which **708 were distinct**, a 92% duplicate rate, and `nvidia` was 68%. So
    `len(hits) < PAGE_SIZE` almost never fires. `_query` now also ends on **a page that
    contributes no new posting** — the real end of the results, one page to discover — which
    makes `MAX_PAGES_PER_QUERY` a runaway guard again rather than the thing deciding coverage.
    `test_workday_paging.py` pins all three stop conditions.
  - **`_age_rank` is what makes the Workday ceiling safe to raise**, and it is coarse: the
    list gives relative prose ("Posted Today", "Posted 30+ Days Ago") and the real `startDate`
    only arrives with the detail call. It orders well enough to decide what survives the cut
    and cannot do more than that.
  **Run the list stage concurrently, not just the details.** Workday's list is 13 sites × 9
  terms = 117 independent queries; sequentially they cost ~500 s, more than the 1 200 detail
  calls after them, and the whole source took 730 s. Pooling the queries — paging still
  sequential *within* one, since each page decides whether there is another — brought it to
  312 s for identical output.
  **The export's deadline is the ~06:00 routine, not the 07:00 import** — corrected
  2026-08-04, because this line previously said the latter and it is the number these ceilings
  get sized against. The claude.ai routine reads whatever `shortlists.json` is sitting in
  Drive when it wakes at ~06:00. An export still running then does not delay it, it *misses*
  it, and every subscriber gets yesterday's file or none — with no error anywhere, which is
  this repo's recurring failure shape.
  `Type=oneshot` means systemd sets no start timeout, so nothing else will stop it either.
  **The window was 60 minutes until 2026-08-07, when the export was moved 05:00 → 03:00 and it
  became roughly 3 hours.** The move was made on a measurement rather than a feeling: the
  2026-08-07 run was `05:00:00 → 05:39:40` (exit 0) — **39m40s of a 60-minute window, ~20
  minutes of headroom**, read off the box with
  `systemctl show jobdigest-match-export.service -p ExecMainStartTimestamp -p ExecMainExitTimestamp`
  (the journal needs group membership the deploy user does not have). The backup moved
  03:30 → 01:30 in the same change so the two cannot overlap. **This buys slack, not licence:**
  the ceilings below (`workday.MAX_DETAILS`, `MAX_PAGES_PER_QUERY`, `smartrecruiters` paging)
  are still what decide coverage, and the failure mode is unchanged and still silent. Re-measure
  after any change that adds N+1 work, and keep an hour of slack rather than spending it all.
  Measured 2026-08-04 with the raised ceilings: SmartRecruiters 7 min, Workday 29 min at 8
  detail workers and ~19 min at 16, which is why `workday.DETAIL_WORKERS` exists.
- **A national employment service publishing open data is the best source shape available,
  and there are three: `mpsv` (CZ), `platsbanken` (SE) and — permitted but not yet built —
  **NAV (NO)**.** Sweden's is Arbetsförmedlingen's
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
- **Norway's NAV is permitted in writing, self-service, and the best unbuilt source left**
  (checked 2026-08-07, full workings in `notes/2026-08-07-norway.md`). `arbeidsplassen.nav.no`'s
  feed API is governed by `/vilkar-api`, which says *"Alle kan bruke tenesta. Tenesta er
  kostnadsfri"* and grants consumers *"rett til å **republisere og vise** mottekne jobbannonsar
  på sine tenester"* — a positive grant naming the act this product performs, stronger than
  MPSV's disclaimer. `GET /api/publicToken` hands out a signed JWT with no account and no email,
  so the skip rule does not apply; **do not mail NAV for the stable token** — that is
  correspondence, and the public one works (fetch it every run, it rotates, and the response is a
  text blurb with the JWT inside, not a bare token). Measured on a live 120-ad sample: **14 548
  active ads**, **median description 3 102 characters**, 100% `expires`, 100% `employer.orgnr`,
  93% city, **STYRK08 major groups 1–3 = 59%** (the MPSV ISCO 1–3 call — major group 5 alone is
  28% and would otherwise reach the widened retrieval path), 120 details in 2.8 s at 8 workers.
  Four things that decide the build. **Use the documented feed, never the sitemap**: the sitemap
  enumerates the exact active set and its ad pages are server-rendered, but only **71% of sitemap
  uuids resolve on `/api/v1/feedentry/{uuid}`** and all six 404s checked carry a `finn.no`
  attribution — the API withholds ads NAV may display but not redistribute, so **the feed is the
  permission boundary, not a transport**, and scraping the 29% gap would look like a coverage win
  while republishing what is not ours. **It is the first source needing persisted state** (the
  feed is append-only from 2023-06-14, 1 000 summaries per page, `next_url` forward-only, no
  snapshot call) — but no backfill is needed, because every ad carries `expires` and a cold start
  reaches a full corpus in about a month. **Two obligations change behaviour**: an entry arriving
  `status: "INACTIVE"` must deactivate the posting *and* `expires` must be enforced (*"skal straks
  fjernast frå resultatlista"*), and the apply link must deep-link to `applicationUrl` — but
  **`posting_id` must hash the immutable `uuid`**, never that third-party URL. And personal data
  is heavy — **81 of 86 ads name a contact, 70 with an email, 80 with a phone, and 35% of
  descriptions carry a phone-shaped string** — so `contactList` is never read and text is
  scrubbed, verified across the corpus not a sample, exactly as for MPSV. Finally,
  `arbeidsplassen.nav.no/stillinger/api/search` is a live keyless Elasticsearch endpoint that
  would make the cursor question vanish; it is the site's internal search, outside the document
  that grants us anything, and taking it is the Bundesagentur pattern. **Measurement only.**
- **Norway's other sources are all closed, and one of them corroborates the NAV gap** (checked
  2026-08-07). **FINN.no refuses in its own `robots.txt`**, which is the only source here to put
  the refusal in that file as prose: *"Crawling FINN.no is prohibited unless you have written
  permission … Bruk av automatiserte tjenester (roboter, spidere, indeksering m.m.) samt andre
  fremgangsmåter for systematisk eller regelmessig bruk er ikke tillatt uten eksplisitt samtykke
  fra FINN.no."* That is **independent confirmation of why 29% of NAV's ads are not in its API** —
  they are finn.no-sourced, NAV may display them and may not redistribute them, and finn.no
  forbids reading them directly. Two doors, same answer. (Note `lever:finn`, already carried, is
  **FINN GmbH of Munich** — the German car-subscription company — not FINN.no; genuine German
  inventory, but never count it as Norwegian coverage.) **Webcruiter**, Norway's largest ATS and
  the apply target on NAV ads, is `User-agent: * / Disallow: /` — its `Allow:` rules for
  `/home/alladverts/` are **Googlebot-only**, so it is closed on robots alone like AMS eJob-Room,
  and `www.webcruiter.no` is `Disallow: /` too. **Jobbnorge** (the ATS behind Norwegian
  universities, municipalities and health trusts, so ISCO 1–2 by construction) has an effectively
  allow-all robots but its listing page renders 2 534 characters and says it *needs JavaScript*;
  its bundle (`/search/site.min.js`) contains no XHR path, only client-side handlebars templating
  of `schema.org/JobPosting` — so the Actiris method found nothing to call. Unresolved, not
  refused; the ad pages themselves were not checked for server-rendered JSON-LD and that is the
  one thing left to try. **karrierestart.no** carries the same Content-Signal template as
  kariera.gr — `search=yes, ai-train=no`, `ai-input` unset, `Disallow: /` for ClaudeBot/GPTBot —
  so nothing is granted to a product whose matcher reads postings into a model.
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
- **France Travail is permitted, self-service, and declined on cost rather than terms — the
  first source to fail that way, so do not pattern-match it to the refusals.** Its API needs
  only a francetravail.io account (no application, no signature, so the skip rule does not
  apply), and its licence *assigns* **"l'intégralité des droits d'auteur et droits sui generis
  sur la Base de données"** — stronger than MPSV, which merely disclaims the database right.
  What stops it is two clauses that change the product rather than the adapter. **Art. 5.3**
  requires *"faire figurer sur chaque offre d'emploi la totalité du Contenu mis à disposition
  dans l'API"*, and JobDigest has nowhere to put that: a `MatchCard` shows a title, employer,
  salary and the matcher's own line, and the description is stored but never displayed. **Art.
  7** requires an expired offer to be stripped of company name, description, URL *and the
  commune* — which empties `digest.dedupe_key`, whose seen-set joins `digest_sends` back to
  `postings` over 90 days, and an empty key is deliberately "always unique, never a match". So
  compliance would reintroduce duplicate emails through the back door. Also Art. 4: attribution
  *plus* a published description of every modification made to the data. **Reopen only if a
  per-posting detail page exists for some other reason** — then Art. 5.3 is free and only the
  Art. 7 scrub remains. Not because France looks thin: `sanofi`, `valeo` and `salesforce` are
  already French inventory on Workday. Full workings in `notes/2026-08-04-france-travail.md`.
- **The rest of France was swept on 2026-08-07 and closes the question around France Travail:
  there is no second national-scale route.** Three boards, three refusals, and the *cadre* one
  is the one that stings:
  - **APEC** is the association that exists for exactly this product's segment — French
    *cadres*, ISCO 1–3 by construction — and its robots.txt says *"All robots will spider the
    domain"* with no `Disallow`. Its CGU forbid two things separately: *"**Capter tout ou
    partie du contenu du site Apec.fr et des bases de données qui lui sont liées (offres
    d'emploi, candidApec, …)** sous peine de s'exposer à des sanctions de nature civile et/ou
    pénale"*, and *"**Utiliser, pour naviguer sur le site Apec.fr, un logiciel robot ou tout
    autre procédé ou outil automatisé équivalent**"*. Its search webservice
    (`/cms/webservices/rechercheOffre`, POST, unauthenticated) works; that is not the question.
  - **HelloWork Group** (hellowork.com, ex-RegionsJob) carries the most explicit refusal in
    this repo: automated extraction *"à quelques fins que ce soit, **commerciales ou non**, est
    strictement interdite"*, followed by a full recitation of the **sui generis database
    right** — extraction of a substantial part, and repeated extraction of insubstantial parts.
  - **Welcome to the Jungle** forbids any *"extraction automatisée de données (« scraping »)"*,
    while its robots.txt permits `/jobs/`. **Cadremploi** returns a Cloudflare 403 to the honest
    agent, so it is a dead source before its terms matter.

  That is now **seven times robots.txt has permitted what the terms refuse** — Alma Career,
  EURES, BambooHR, karriere.at, hokify, APEC, WTTJ. Treat a permissive robots.txt as evidence
  of nothing.
  **The French open-data route is statistics or municipal scraps**, and it is worth knowing why
  it *looks* promising: `data.gouv.fr` returns a France-Travail-published *"Offres d'emploi
  diffusées à France Travail"* under Licence Ouverte, which is a **quarterly XLS of seasonally
  adjusted series**. Two collectivités redistribute genuine live records under LO 2.0 — and
  Seine-Saint-Denis's holds **150 rows**, unfiltered and last touched in December 2025 (the
  sampled one is a delivery driver 700 km away in Castelnaudary), while Île-de-France's 193 are
  **the Région's own civil-service posts**, not France Travail's corpus. **The French ATSes are
  closed too**: Taleez's robots is `Disallow: /api/`, and DigitalRecruiters/Flatchr/Beetween/
  Softy host each customer on the customer's own domain with no keyless multi-tenant board API
  — the eRecruiter problem, per-employer permission with no scalable answer.
- **The national-register route is now closed in every remaining EEA country, and the reasons
  differ** (checked 2026-08-04). Denmark's Jobnet webservice is free but arranged by email to
  `spoc@star.dk`; Belgium's VDAB Vacature API needs an approved partnership, an intake call and
  a signed cooperation agreement; Austria's AMS publishes *statistics* on data.gv.at — counts
  of open positions by district, not vacancy records — and its HR-API is inbound, for employers
  posting *to* the AMS; Switzerland's job-room.ch API is the same shape, a publishing channel
  for the Stellenmeldepflicht with credentials on request; Portugal's IEFP puts only monthly
  aggregate movements on dados.gov.pt. So `mpsv` (CZ) and `platsbanken` (SE) remain the only
  two, France Travail is the only other permitted one, and **curated employers on an ATS is the
  entire remaining route** for the countries that are empty. (**Corrected 2026-08-07: NAV (NO)
  is a third permitted register** — see above. "Every remaining country" was true of the ones
  then checked, and Norway had not been.)
- **The last sixteen countries were swept on 2026-08-07 and the map is now complete**
  (`notes/2026-08-07-remaining-eea-sweep.md`): NO, IS, LI, IE, ES, GR, HU, BG, HR, SI, LT, LV,
  EE, LU, MT, CY, plus re-checks of FI/DK/CH. Norway is the one build. Four things worth keeping:
  - **Check ownership before checking terms — Alma Career's own brand list closed five more
    countries' largest board.** `almacareer.com` names **CVonline.lt, CV.lv, CV.ee** (Baltics),
    **MojPosao** (HR) and **Jobly.fi** (FI) alongside jobs.cz/profesia/Teamio. The 2026-08-03
    exclusion therefore already governs EE, LV, LT, HR and FI; do not re-read those terms, and do
    not treat a Baltic or Adriatic board as open because it has a local name. The reverse error is
    just as costly: **MojeDelo.com (SI) and Profession.hu (HU) are *not* Alma Career** and had to
    be settled separately (MojeDelo is a 1 777-character SPA; Profession.hu's sitemap carries
    articles and salary pages but **no job listings**).
  - **Ireland's public employment service refuses, and it is the closure that costs most** —
    Adzuna has no IE index. JobsIreland.ie asserts *"copyright, **database right, sui generis
    right**"*, limits the site to *"use by jobseekers searching"*, and requires
    **"contact DSP for permission by emailing info@welfare.ie"** to re-publish or reproduce.
    Correspondence → skipped. Its `/en-US/browse-jobs` loads by XHR anyway (4 976 vacancies, no
    JSON-LD, no vacancy links in the HTML). Ireland is ATS-only.
  - **Greece's kariera.gr is the ninth source whose robots permits what its terms refuse.** It
    publishes three job sitemaps of 5 000 `<loc>` each and serves `Content-Signal: search=yes,
    ai-train=no` — the Teamtailor shape — but its user licence allows saving **"ένα μόνο
    αντίγραφο"** and forbids **"να κάνετε διαθέσιμο αυτό το υλικό σε τρίτους"**, which is what a
    digest does. It also `Disallow: /` for **ClaudeBot**/GPTBot/CCBot and never sets `ai-input`,
    so nothing is granted to a product whose matcher reads postings into a model.
  - **Latvia is the inverse of Sicily: a perfect licence over real but wrong inventory.** NVA
    publishes **"Vakances"** on `data.gov.lv` under **CC0-1.0**, refreshed daily (today's resource
    is literally `vakances-2026-08-07.csv`), 1 633 rows, **100% with min+max EUR salary**, 685
    Rīga; a second CC0 set (State Chancellery **TVP `vacancy.csv`**, 973 rows across 81
    institution sites) joins on an NVA id and supplies the description. What kills it is the
    corpus: education 614 + healthcare 380 + public administration 210 = **74%**, and
    **IT/telecoms is 21 rows**; the TVP descriptions are **truncated at 255 characters**, the NVA
    file has **no description at all** (only a link into a hash-route SPA) and names the employer
    only by registration number. So: permitted, buildable, low value, category-filtered — reopen
    if LV subscribers appear, not before. NVA's own `cvvp.nva.gov.lv` holds the private-sector
    register behind a 945-byte shell — and **on 2026-08-07 that API was found**, which changes
    the reasoning without changing the answer. Read the AngularJS bundle rather than guessing
    paths (the Actiris lesson): `/js/app.*.js` names the view `pub_vakance_list` and a
    `$resource("./data/" + entity + "/:id")` prefix. So **`GET /data/pub_vakance_list?limit=N`
    returns the live list and `GET /data/pub_vakance/{id}` returns the full record** — no key, no
    session. The list carries `uzn_uznemums_nosaukums` (the real company name, private employers
    included — "SIA Starfish"), `vieta` (street address), `alga_no_lidz` (salary),
    `publicesanas_laiks` and `aktuala_lidz`; the detail adds `darba_apraksts` as full HTML. The
    endpoint even distinguishes its own errors usefully: `404 "View definition for X not found"`
    means no such entity, while `400 "X.list is not a part of this API"` means the entity exists
    and that operation is not exposed — which is how `pub_vakance` was found at all.
    **It is still skipped, and the reason is the EURES reason rather than caution.** NVA
    publishes ten datasets on data.gov.lv, all CC0 — and the only vacancy-*record* one is
    "Vakances", scoped in its own description to `Latvijas valsts sektorā`; the other nine are
    unemployment statistics. So the dedication covers exactly the subset where the employer is a
    public body, and stops precisely where third-party employer content begins. That is not an
    oversight, it is the same boundary ELA's licence draws: **the portal is the agency's, the
    vacancy texts are the employers'.** No terms page could be found on `nva.gov.lv` and
    `cvvp.nva.gov.lv` serves no robots.txt, so nothing *refuses* us either — this is the Personio
    and hipo.ro shape, **permission unestablished, which is not permission**. Do not build it on
    the strength of the endpoint working; that is the Bundesagentur error. Two further facts for
    whoever revisits: descriptions carry employer contact emails inline ("CV sūtīt uz
    info@starfish.lv"), so the MPSV scrubbing discipline would be mandatory; and `limit=5000`
    returns 400, so the page ceiling is lower and would need probing.

  **Closed for the usual reasons, recorded so nobody re-probes them:** ES Empléate answers every
  path with a session-gated error notice and its UI is a hash-route SPA (**and Adzuna covers ES
  anyway**) · EE Töötukassa renders **10 characters** of visible text · SI's ESS portal
  `poiscidelo.si` and LU's `jobboard.adem.lu` are login-gated · HU's VMP has two job links and no
  sitemap · HR's `burzarada.hzz.hr` is a session-cookie ASP.NET app · IS folded
  Vinnumálastofnun into island.is (`alfred.is` is the only route with any promise, and IS holds
  2 postings). **Dead to the honest agent:** duunitori.fi, jobs.bg, Lithuania's uzt.lt and
  Malta's jobsplus.gov.mt all 403; job.is 429s. **And two new non-availability modes:**
  Bulgaria's Agency for Employment (`az.government.bg`) **cannot complete a TLS handshake**
  (`SSL: DH_KEY_TOO_SMALL`) — unreachable by accident rather than by policy; and a sweep of
  twelve national open-data portals for vacancy datasets returned **statistics only**, for the
  fourth time after AMS/IEFP/Barometro. Do not re-derive that.
  **Robots is now refusing API paths specifically** — Työmarkkinatori (`Disallow: /api/`,
  `/*/api/`), jobs.ch (`/api/`, `/api_proxy/`), alfred.is and kariera.gr all disallow their data
  endpoint while allowing their HTML. Where that is the shape, the pages are the invitation and
  the endpoint is not.
- **Cyprus and Liechtenstein were the last two selectable countries with no settled verdict,
  and both are now closed** (checked 2026-08-07). **Cyprus is the rare case gated by an eID
  rather than by terms.** The Department of Labour's PES at `pescps.dl.mlsi.gov.cy/CPSWeb/`
  answers the honest agent with a *server-rendered* page reporting **3 431 vacancies** and
  serves no robots.txt — and is still shut, because since 2 June 2025 access *"θα γίνεται
  **μόνο μέσω του CY-Login**"*, the national eID, and the published vacancies are for
  *"Κύπριοι και Κοινοτικοί **που διαμένουν στην Κύπρο**"*. A verification step plus a residency
  gate is the skip rule, not a pending state. Both domestic boards then refuse on top of a
  permissive robots.txt: **ergodotisi.com** forbids *"Use bots or automated systems to scrape
  or misuse Platform data"*, and **carierista.com** — whose robots is a bare `Disallow:` —
  forbids *"any data mining, robots or similar data gathering or extraction methods"* and
  *"aggregate, copy or duplicate in any manner"*. `kariera.com.cy` 403s, `careerfinder.com.cy`
  does not answer, and `cyprusjobs.com`/`goldencareers.com.cy` publish **no terms at all** —
  permission unestablished, the Personio shape. `data.gov.cy` is 222 datasets of employment
  **statistics**. **Liechtenstein is the second source in this repo refused by robots alone**,
  after Austria's AMS eJob-Room: the **AMS FL Stellenbörse** at `www.amsfl.li` is a flat
  **`Disallow: /`** at the host root, and `www.llv.li` 403s the honest agent. **joblie.li** is
  the sharpest case anywhere in this file — its robots.txt carves out `Allow: /api/feeds/`
  *and explicitly allows `ClaudeBot` and `anthropic-ai` by name*, while its Nutzungsbedingungen
  §6 prohibit *"**automatisiertes Auslesen von Daten**, Data Mining, Scraping"* and §15 reserves
  the **Datenbanken**; a friendlier robots.txt does not exist, and it still loses to the terms.
  **liechtensteinjobs.li** disallows `x28-job-bot`/`JobRoboter` by name and requires *"ausdrückliche
  Zustimmung von Somedia Press AG"*; `jobs.li` is a parked domain, `stellen.li` 301s to jobs.ch,
  and `opendata.li` carries three organisations and no vacancy records. Both countries are
  ATS-only, and `scripts/cy_companies.csv` / `scripts/li_companies.csv` are the whole buildable
  surface. **Expect LI to yield zero**: its eight professional employers are the
  Hilti/Ivoclar/ThyssenKrupp-Presta/LGT tier, exactly the profile that runs SuccessFactors
  (closed — needs a browser) or Workday (found-and-not-added). A well-evidenced zero is the
  finding; do not re-derive it.
- **`postings.eligibility` is a subscriber-specific judgement stored in a posting-level column,
  and a constant allowlist over it is always wrong for somebody** (fixed 2026-08-07).
  `search_jobs.eligibility`'s own docstring still reads *"Coarse EU-eligibility flag for a
  **Czech-based candidate**"* — it dates from when this repo served one person. Both shortlist
  queries filtered on a hardcoded `('eligible','verify UK right-to-work','unknown')`, and
  `profiles.eligible_only` defaults **true** with **no UI control** (it exists only as a type in
  `web/lib/api.ts`; no form writes it). Measured on production: **21 291 of 21 677 active US
  postings (98%) carry `likely needs US work auth`**, which that list excludes — so the US half
  of the 2026-08-05 "US is selectable" change **had never worked**, and a subscriber who ticked
  the United States could not turn the filter off. Nothing failed and nothing logged; it bit
  nobody only because all three subscribers were CZ-only. `store.eligibility_allowlist` now
  derives from `profile["countries"]`, shared by both call sites so they cannot drift:
  `likely needs US work auth` is admitted only when US is selected; `verify UK right-to-work`
  stays **unconditional** (advisory, and 5 557 of 5 749 GB rows carry it — making it conditional
  would *narrow* what existing subscribers see); `blocked (clearance/US-only)` is never admitted,
  because a clearance is not something a country preference unlocks. **Canada passes only because
  `work_region("Toronto","CA")` returns `"other"` → `unknown`** — luck, not design, which is why
  `test_eligibility_sql.py` pins it: if a `CA` branch is ever added to `work_region`, that test
  fails instead of 1 946 postings silently vanishing.
- **`taxonomy.py` reads English, and three of the sources feeding it do not.** Measured
  2026-08-07 against `classify()`: `Systemutvikler`, `Dataingeniør`, `Produktsjef`, `IT-arkitekt`
  and `Testleder` all return `uncategorised`; `Backend utvikler` and `Fullstack-utvikler` classify
  only because *Backend* and *Fullstack* are English; and `Sikkerhetsanalytiker` classifies as
  **`data_analysis`, which is wrong** — it is a security analyst matched on "analytiker". So a
  Norwegian source would arrive mostly `uncategorised` and reach a subscriber only through the
  keyword half of the `category OR keyword` recall predicate. **This is already the condition of
  `platsbanken` (Swedish) and `mpsv` (Czech)**, our two largest non-English sources, so it is
  accepted rather than broken — but it means **the marginal value of a fourth national source may
  be lower than teaching the taxonomy Norwegian, Swedish and Czech role words.** Settle it with
  `select source, role_category, count(*)` over the non-English sources on the box before choosing
  which to do; that measurement has **not** been taken.
- **Austria was swept end to end on 2026-08-07 and every non-ATS route is closed. Do not
  re-open one because AT looks thin.** Seven doors, and the useful thing is that no two shut
  for the same reason:
  - **AMS eJob-Room** — `jobroom.ams.or.at/robots.txt` is **`Disallow: /`**. The one source
    here refused by robots *alone*, and the inverse of the recurring lesson: usually robots
    permits what the terms refuse. Its data.gv.at open data is statistics, as recorded above.
  - **karriere.at** (Austria's largest board) — robots.txt is `Disallow:` (allow-all) but the
    Nutzungsbedingungen §2.8 read *"Du darfst die Informationen auf unserer Website bloß für
    deine persönliche Jobsuche und den privaten Gebrauch verwenden. **Sämtliche Formen der
    automatisierten Auswertung unserer Plattform sind verboten.**"* Same bar as Alma Career.
  - **hokify** — its robots.txt literally opens `# crawler friendly, as long as you link to
    hokify.at`, and its AGB then forbid *"das Benutzen … automatischer Software … Script-Robots
    … (insbesondere Browser, Spider, Robots …)"* and *"das Sammeln, Kopieren oder
    Vervielfältigen von Informationen … ohne vorherige schriftliche Genehmigung"*. **The
    friendliest robots.txt in the repo sits on top of a flat refusal** — settle a source on the
    narrowest applicable clause, never on robots.
  - **derStandard Jobs** — AGB §VI reserves commercial **text and data mining** under
    § 42h Abs 6 UrhG / Art. 4(3) RL (EU) 2019/790 and points at `legal@derstandard.at` for a
    licence. A machine-readable TDM reservation is a refusal, and buying past it is
    correspondence → skipped either way.
  - **willhaben** — robots.txt opens *"It is expressively forbidden to use spiders, search
    robots or other automatic methods to access willhaben.at."* **StepStone.at** returns 403 to
    the honest `JobDigest/1.0` agent, and **devjobs.at** answers a Vercel security checkpoint
    (429) — both dead sources by this repo's own rule. **metajob.at** is a meta-aggregator whose
    legal pages are an unreadable SPA shell, and it indexes karriere.at and StepStone, so taking
    it would launder their refusals — the EURES rule.
  - **jobboerse.gv.at** (the Republic's own civil-service board) looked like the MPSV shape and
    is not: its REST API is `/wf-rest-api/` and answers **401**, and the one bulk page that
    exists, `/statische-jobsuche`, is robots-disallowed.
  - **eRecruiter** is the near-miss worth recording. Austria's market-leading ATS, its portals
    are server-rendered with a sitemap, `Allow: /`, and **schema.org/JobPosting JSON-LD** — one
    parser could in principle reach many Austrian employers, which is exactly what a company's
    own careers page cannot offer. Two things stop it: **karriere.at owns 51% of eRecruiter**,
    so the group whose terms forbid automated evaluation is the operator; and customer portals
    run on the *employer's* own host under no single ATS document, so permission is a
    per-employer question with no scalable answer. Its public API path is 404 to anonymous
    callers anyway.
- **Italy has no open route either, and one of the closures is a market fact rather than a
  legal one** (checked 2026-08-07). **InfoJobs.it — Adevinta's Italian board — has shut down**:
  every path on the host returns one page reading *"Questa piattaforma è ufficialmente chiusa e
  non più disponibile."* The rest: **Cliclavoro** no longer runs a vacancy search at all, having
  moved it into **SIISL**, a SPID-gated 10 KB SPA; the Ministry's open data is statistics
  (Barometro del lavoro, avviamenti/rapporti di lavoro), the AMS shape; the **regional** portals
  are the devolved route and the sampled one (cliclavoroveneto.it) is a Liferay portlet whose
  job search renders client-side — 1 985 characters of visible text, no JSON-LD, and it is one
  region of twenty. **subito.it** and **monster.it** return **403** to the honest agent,
  **trovolavoro.it** is `Allow: /$` + `Disallow: /`, and **jobrapido**/**talent.com** disallow
  their own posting paths *and* are aggregators over boards that refuse us. Both **Italian
  ATSes** are closed on robots, and precisely on the pages that matter: inRecruiting (Zucchetti)
  disallows `/*LAC*`, the career-page parameter, and altamira.it disallows exactly
  `/html/people/people_sfoglia_annunci.asp` and `/Annunci/Dettaglio.htm`.
  **The one open-licensed Italian vacancy dataset is real and empty.** Regione Siciliana
  publishes *"Offerte di Lavoro"* on dati.gov.it under **CC BY 4.0** with CSV/JSON/TTL — the
  licence MPSV and CBOP set the bar with — and it holds **18 records, none newer than March
  2022**, mostly bakers, shop assistants and holiday-village entertainers. A perfect licence
  over no data; the inverse of France Travail, which is perfectly licensed data we cannot
  display. The Trento *"Offerte di lavoro Eures"* set is a 2019 hackathon sample. Do not
  re-derive this: a CKAN sweep of dati.gov.it for vacancy datasets returns statistics and
  nothing else.
- **Romania is closed on every route too, and the useful number is how thin its ATS surface
  is** (checked 2026-08-07). **ANOFM**, the national agency, runs its register as an app at
  `mediere.anofm.ro` that renders **no vacancies server-side** and exposes no public list
  endpoint; `data.gov.ro` disallows `/api/` in robots (`Crawl-Delay: 10`) and, searched for
  *anofm* / *vacante* / *ocupare* through the permitted HTML pages, carries only training-
  programme and employment-programme **statistics**. So the register exists and is published
  nowhere machine-readable — a third distinct way for a national register to be unavailable,
  after Austria's `Disallow: /` and Italy's SPID-gated SPA. **eJobs.ro**, the largest board,
  forbids *"reproducerea, în orice fel, integrală sau parțială, a informațiilor cuprinse …
  **în anunțurile de recrutare ale Companiilor**, fără acordul scris"* and adds that its
  content may not be reproduced or exploited *"indiferent de scopul comercial sau
  necomercial"*. **BestJobs.eu** lists *"colectarea neautorizată de date (**scraping**)"* among
  prohibited conduct — while its robots.txt is a bare `Disallow:`, i.e. allow-all, making it
  the **eighth** source where robots permits what the terms refuse. **undelucram.ro** returns a
  Cloudflare 403 to the honest agent. **hipo.ro** is the one genuinely unresolved case: robots
  permits its job pages, but its terms grant access to store or reproduce material only *"în
  scopuri personale"* and never to a third party — permission unestablished is not permission.
  And the measurement worth keeping: **64 Romanian employers probed produced 6 live boards, 4
  on a supported ATS, of which 2 were impostors.** Romania's inventory is a delivery-centre
  economy whose employers hire through parent-company systems, not public boards.
- **The UK's national vacancy service no longer exists, and that is the one closure here with a
  real reopen trigger** (checked 2026-08-07). **DWP's "Find a Job" shut down on 30 June 2026** —
  `findajob.dwp.gov.uk` answers 503 with *"This site is now closed"* — and DWP is building an
  in-house replacement. The closed site's own footer reads *"All content is available under the
  **Open Government Licence v3.0**"*, which is the licence MPSV and CBOP set the bar with. **So
  when the replacement launches, re-check it**: an OGL-licensed national vacancy service with a
  machine-readable route would be the UK's `mpsv`, and it is the only pending source in this
  file that is pending on someone else's build rather than on correspondence. Everything else is
  shut: **Civil Service Jobs** has `Allow: /` in robots and then a *"we just need to confirm
  you're a real person"* JS wall; **Reed.co.uk** (403), **CV-Library** (403 despite `Allow: /`)
  and **Totaljobs** (timeout) all refuse the honest `JobDigest/1.0` agent outright;
  **jobs.ac.uk** — the academic board, and tempting because its inventory is entirely ISCO 1–2 —
  refuses on terms: *"You can download, print and copy material … **for your own personal
  use**. You **must not reproduce any part of the Company's Website or its material unless the
  Company have granted you permission**"*; and **NHS Jobs** serves no robots.txt and its terms
  page is a 954-character shell, so permission is unestablished — and its bulk is clinical
  anyway, which is the `ashby:serenis` problem at national scale. **Adzuna already covers GB**
  through its GB index, which is the existing route and remains the only one.
- **Belgium's public route is three regional services, shut three different ways — and one of
  them, Actiris, is the strongest unbuilt lead in this file** (checked 2026-08-07). **VDAB**
  (Flanders) still needs an approved partnership and a signed cooperation agreement, so it stays
  skipped. **Le Forem** (Wallonia) disallows `/recherche-offres/` in robots — the job-search
  path itself — and blocks `ClaudeBot` and `GPTBot` by name. **Actiris** (Brussels) is the
  exception, and everything about it points the right way:
  - `robots.txt` disallows **only `/media/`**, and publishes **`sitemapoffers-fr.xml`** — a
    sitemap enumerating **9 943 live offers**, with the Dutch equivalent alongside it.
  - Offer pages are **server-rendered** (~9 000 characters of visible text), so no browser is
    needed — the constraint that closed SuccessFactors and the Italian regional portals.
  - The *mentions légales* grant, in terms: *"**L'utilisation des informations présentes sur les
    sites est autorisée à condition d'en citer la source**"* and *"**Actiris autorise les
    utilisateurs de ce site à copier les informations qui y sont présentes, de les imprimer et
    de les communiquer à des fins d'informations**"*. The one restriction — that the data not be
    used *"à d'autres fins que celles pour lesquelles elles sont mises en ligne, à savoir l'offre
    et la recherche d'un emploi"* — is satisfied on its face by a product that delivers job
    offers to jobseekers. There is no anti-robot clause at all.

  **What stops it is arithmetic, not permission.** No bulk or search endpoint was found: the
  site's own front end is a `<search-offers-widget>` whose XHR path is not in either shipped
  bundle, so today the only route is page-by-page, and **9 943 fetches at `politeness`'s
  1 s/host is ~2.8 hours against a ~60-minute export window** — the EURES `Crawl-delay` problem
  in a different guise. **Reopen the moment that endpoint is identified**; the widget calls
  *something*. Three things to settle first if it is built: whether offers name a contact person
  (MPSV and Platsbanken both had to scrub them), an occupation filter, since like MPSV this is a
  whole labour market and the sampled offer was a youth-work coordinator, and where the required
  source citation goes — the Remote OK precedent, so Terms §3 and the `MatchCard`.
- **`werkenvoornederland.nl` is the best-licensed and best-structured public source this repo has
  ever found, and unlike Actiris nothing stops it being built** (checked 2026-08-07). The Dutch
  central government's own vacancy site:
  - **Licence: CC0.** *"Tenzij anders vermeld is op de inhoud van deze website de **Creative
    Commons zero verklaring (CC0)** van toepassing … hergebruik van de inhoud van deze site is
    toegestaan … Bij hergebruik van de inhoud van deze website is **naamsvermelding niet
    verplicht**."* A public-domain dedication with attribution *not even required* — stronger
    than MPSV (which merely disclaims the database right) and stronger than CBOP's CC BY. Only
    photographs are carved out, and we use none.
  - **robots.txt disallows only `/login`**, and carries `Request-rate: 10/1` — an explicit
    invitation to go ten times faster than `politeness`'s 1 s/host.
  - **`sitemap-vacatures.xml` enumerates 1 296 vacancies**, and every page carries
    **schema.org/JobPosting JSON-LD**: title, full description, `datePosted`, `validThrough`,
    `employmentType`, `hiringOrganization` (the real ministry name), `jobLocation` with city,
    postcode and `addressCountry: NL`, and **`baseSalary` with min/max EUR per month**. Better
    structured data than most adapters here parse by hand.
  - **The roles fit the product**: senior business analysts, legal advisers, policy advisers,
    PostgreSQL platform engineers. Central-government professional work, so unlike `mpsv` and
    Actiris it needs no ISCO filter.
  - **Personal data looks clean but was not exhaustively checked**: 0 email addresses across 6
    sampled pages, a phone number on 3 of 6. Before building, apply the `mpsv`/`platsbanken`
    discipline — never read a contact field, scrub contacts out of description text, and verify
    across the whole corpus, not a sample.

  At 1 296 pages it costs ~22 minutes at the current throttle, which fits the export window but
  is not free; the `Request-rate` header is the argument for a per-host exception if it matters.
  **This is a build, not a lead** — it is recorded here rather than built because discovering it
  was the task. By contrast **`werk.nl` (UWV), the much larger national board, is closed**: its
  vacancy route renders 110 characters of visible text (a SPA) and its sitemap holds 155
  informational pages and no vacancies — the SIISL shape.
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
  local** (15:00–05:00 UTC in summer — and since the export moved to 03:00 on 2026-08-07 that
  window now *contains* the export, so this would no longer need its own timer; it was the
  05:00 start that fell outside it), serves a 16:00 snapshot, and caps **20
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
