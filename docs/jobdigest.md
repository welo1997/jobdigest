# JobDigest internals

The live product: Postgres on a Hetzner VPS, `service/`, `web/`, `deploy/`.
Read this before changing anything under `service/`, `web/` or `deploy/`.
The invariants are summarised in `CLAUDE.md`; this is the reasoning and the measurements
behind them.


## Flow

```
03:00 UTC  export   ingest sources → Postgres → embed new rows → shortlists.json → Drive
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

**The shortlist stays keyword-retrieved at ~120, and that is a measured decision, not inertia**
(2026-08-08, `notes/2026-08-08-phase1-vector-gate.md`). The plan was to hand the matcher ~25
vector-retrieved candidates instead — a 5–10× token cut, and the thing that makes cost survive
growth. `scripts/measure_shadow_recall.py` was built as the falsifiable gate for it and **came
back negative**: against 100% vector coverage, recall@25 of the AI's own past picks is
**16.4% / 6.2% / 1.4%** across the three subscribers, and even recall@120 only reaches
41% / 16% / 5%. Narrowing would drop jobs subscribers currently receive.

Read the reason carefully, because the obvious conclusion is wrong. **The vector path is not
broken** — inspected directly on production, it returns on-topic candidates (Data Engineer,
Platform Data Engineer, Product Data Analyst in the right country for a data/product profile).
It surfaces a *different* set of equally plausible jobs than the keyword path did. And `matches`
was populated from **keyword** shortlists, so anything only vectors would find cannot appear in
the ground truth and cannot be credited — recall@K is a necessary condition, never evidence of
improvement, as the script's own docstring says. What is proven is "narrowing loses things",
not "vectors don't work". **The seniority blindness is now visible in production rather than in
a fixture**: for a junior–mid subscriber the vector top two are *Senior* Data Platform Engineer
and *Manager*, Data & AI. Filtering `profiles.seniorities` before the vector sort, instead of
hoping cosine encodes it, is the cheap next experiment.

So **the live vector path was deliberately not built** — the handoff gated it on this gate, and
the gate said no; an opt-in flag would be a switch nobody should throw. What *is* running is
`shortlist_shadow`: `export_shortlists` records the vector top-K alongside the live shortlist
every day, reading nothing on the delivery path, and it is the only thing that can measure
improvement rather than agreement. **Do not re-open this from intuition** — re-open it on
accumulated shadow days, on seniority being gated outside cosine, or on subscriber count making
the token cost binding. All three are stated in the note.

**The embed step runs between the ingest and the shortlist export, in its own container, and is
non-fatal.** Own container so peak RAM is `max(ingest, embed)` — measured **1334 MiB** and
**+740 MB** against 3814 MB with no swap, where an over-commit is the OOM killer taking
Postgres. *Before* the export because a null embedding is invisible to `query_shortlist_vector`,
so embedding afterwards would leave the shadow blind to the 7–11k rows ingested minutes earlier
— the freshest inventory, which is exactly what the live path favours via `first_seen_at desc`,
rigging the comparison against the vector side daily with nothing reporting it. Non-fatal
because `jobdigest-match.sh` runs under `set -euo pipefail` and a measurement must never cost a
day of digests. Costs ~8–13 min for a normal daily delta (measured 14.4 docs/s).

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

## Hiding a job is a move, never a delete

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

## Nothing a subscriber states may vanish, and no subscriber may starve silently

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

## Tables (Postgres)

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

**That measurement described a corpus that no longer exists, and the reason it inverted is worth
knowing.** It read: 70% of the corpus has no description, median length 35 characters, because
`jobscz` (9 639 postings) averaged 32 and stored scraps like "70 000 – 80 000 Kč", `profesia`
(4 365) stored the empty string, `cocuma` (319) averaged 9. Those sources were excluded on
2026-08-03 (Alma Career's terms) and platsbanken, mpsv and the ATS boards replaced them.
**Re-measured on production 2026-08-08: 91 818 of 96 583 active postings — 95% — carry a
description, averaging 4 861 characters.** The old figures survive above only as the reasoning
for the education classifier's design; do not cite them as current. The claim that "the entire
Czech and Slovak inventory will hold `null` for ever" is also void — the sources it referred to
are gone, and `mpsv` ships descriptions on 94% of its rows.

**That re-run has now happened (2026-08-08, end of day), and the ~3% ceiling was an artefact of
the unreadable corpus: it is 9.4%, three times higher.** Against 98 858 active postings, of
which 94 026 (95.1%) carry a description averaging 4 791 characters:

| `education_min` | rows | share |
|---|---|---|
| `null` | 89 607 | **90.6%** |
| `bachelor` | 6 946 | 7.0% |
| `master` | 1 173 | 1.2% |
| `secondary` | 641 | 0.65% |
| `vocational` | 247 | 0.25% |
| `doctorate` | 244 | 0.25% |
| **any requirement** | **9 251** | **9.4%** |

Nothing about the design changes and the three consequences below all still hold — but note the
first one now rests on 90.6% rather than ~97%, which strengthens rather than weakens it: a gate
that dropped nulls would still empty a digest rather than narrow it. The classifier did not get
better; the corpus became readable, and the same rules now have text to read. **Do not re-derive
this**; re-measure only after changing `service/education.py`'s patterns, which requires
`python -m service.backfill_education` anyway.

Three consequences, which still hold on their own terms:

- **Null always passes the gate**, as with `work_mode` and an unresolved city. It is **90.6%**
  of rows (measured 2026-08-08; it was ~97% when the corpus was mostly description-less, and the
  conclusion is unchanged by the shift); a gate that dropped nulls would not narrow a digest, it
  would empty it. `test_education_sql.py`
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

## The file exchange is a trust boundary

`picks.json` is written by a claude.ai routine and travels via cloud storage. Nothing in it
is trusted. `import_picks` validates **both** sides of every match against the DB —
`posting_id` must be a real active posting, `profile_id` a real subscriber — clamps scores,
and skips a malformed record rather than aborting everyone's digest. Do not relax this.

`shortlists.json` must **never** contain email addresses. A profile is an opaque UUID. This
is what makes the transfer non-identifying and is load-bearing for the privacy policy.

**A `picks.json` that covers only some subscribers imports cleanly and exits 0 — nothing
except `matcher.coverage_gap` can see it.** `import_picks` iterates the entries the file
happens to contain, so a file covering 5 of 30 profiles writes those 5 and leaves the other
25 with no `picks_n`, no email and no error; `jobdigest-match.sh` checks only that the file
*exists* and is under 20h old, both true of a truncated one. The watchdog needs three days
**and** `starved_profiles` excludes profiles younger than that window, so a new subscriber's
first three days of silence are invisible by design — the window a first impression lives in.
Four things make the check work, and each is a trap avoided:

- **It reads the file, not `digest_runs`.** `picks_n` is `not null default 0` (migration 011),
  so "absent from the file" and "the model returned nothing for them" are the *same stored
  value*, and `watchdog.diagnose` reads the second meaning (`MATCHER`). The distinction only
  exists in the file, so a profile mentioned with every pick below `MATCH_FLOOR` counts as
  **covered** — considered and rejected is not skipped.
- **`store.exported_profile_ids` filters `shortlist_n > 0`**, because `export_shortlists`
  records the run and *then* `continue`s on an empty shortlist. Those profiles are
  legitimately not in the file and are already the watchdog's `RETRIEVAL` case; counting them
  would fire the alert daily for a problem it cannot name, which is how an alert channel gets
  filtered into a folder nobody opens.
- **An unreadable or truncated file reports the *whole* export as uncovered**, never "no gap".
- **The exit code lives in a separate step placed after the send and the archive.**
  `jobdigest-match.sh` runs under `set -euo pipefail`, so failing inside the import would skip
  `service.pipeline` and cost the *covered* subscribers their digest over the uncovered ones.
  `import_picks` logs the gap; `--check-coverage` carries the exit code and runs last, where
  systemd's existing `OnFailure` alerter picks it up and noticing costs nobody anything.

**`sendable_profiles`' `order by last_digest_at nulls first` is a priority policy, not tidy
SQL.** It is the order profiles appear in `shortlists.json`, and any finite matcher budget —
a context window, a subscription's usage cap, a batch that dies half way — drops the *tail* of
that file. `nulls first` therefore guarantees the people a truncated run starves are
long-tenured subscribers who had a digest recently, never someone waiting on their first.
Rewriting it as `order by created_at` looks like cleanup and inverts the policy exactly
(newest signup last); `nulls last` does the same while keeping the ascending sort.
`test_export_coverage_sql.py` fails on both.

## The site speaks eight languages, and the stored subscription speaks none of them

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

## Running it locally

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

## Deployment reality

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

**The `db` service is a BUILT image now, not a pulled one, and the reason is a corruption
hazard rather than a preference.** `deploy/db.Dockerfile` compiles pgvector into
`postgres:16-alpine`; `image: pgvector/pgvector:pg16` is the obvious alternative and would
silently corrupt this database. That image is Debian/glibc, this cluster was initdb'd on
Alpine/musl with `collate=en_US.utf8`, and the two libcs order that same locale name
differently — measured on both images 2026-08-08, musl says `'a' < 'B'` is **false** and glibc
says **true**. Point the existing data directory at the other libc and all 19 indexed
text/varchar columns stay physically sorted by the old rules while the server compares by the
new ones, so index scans miss rows that exist, including through the unique index on
`profiles.email`. Nothing raises. **Never swap this image for a glibc one without a dump and
restore**, and `db` must stay in `deploy.sh`'s build list — a built image that no deploy step
builds is the 2026-08-02 "deployed is not running" failure waiting to recur. Verified after the
switch on 2026-08-08: `server_version` 16.14 unchanged, musl ordering unchanged, 96 583 active
postings unchanged, 0 invalid indexes.

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


---

## The role taxonomy and the source_category hint

- `role_category` has **one** definition: `service/taxonomy.py`. The dbt YAML and
  `web/lib/options.ts` cannot import it, so tests assert they do not drift. Adding a category
  means: pattern, subject word, shortlist keywords, dbt `accepted_values`, a chip in
  `ROLE_OPTIONS` + a label in all eight catalogues, run tests.
  **A source's `source_category` hint must already be a value in `CATEGORIES`, or `None` —
  `classify` discards anything else, and until 2026-08-08 it did not.** The line was
  `return hint or UNCATEGORISED`, so the hint went verbatim into `postings.role_category`.
  Four adapters map their source's vocabulary first and use `None` where there is no confident
  mapping (`jobscz.FIELD_CATEGORIES`, `profesia`'s profession pairs,
  `smartrecruiters.FUNCTION_HINTS`, `themuse.CATEGORIES`); five passed a raw third-party string
  — `platsbanken` (the Swedish SSYK leaf label), `workable` (`function`/`department`, employer
  free text), `startupjobs` (field slug), `recruitee` (`category_code`), `oraclecloud`
  (`JobFamily`). Measured on production: **14 135 of 98 858 active postings — 14% — held a
  value outside the canonical ten**, platsbanken alone 13 561 rows across **954** distinct
  labels; workable's were not job functions at all (`"Greenvolt Next España, S.L."`,
  `"Engine by Starling"`, `"Wild Card"`). The recall predicate is
  `role_category = any(...) OR search_tsv @@ (...)`, so every one of those rows was unreachable
  through the category half and survived on keyword alone, **including genuine tech roles** —
  `Systemutvecklare/Programmerare` 220, `Projektledare, IT` 140, `Mjukvaruutvecklare` 108,
  `IT-arkitekt/Lösningsarkitekt` 97. Nothing reported it and nothing could: the dbt
  `accepted_values` test runs in Snowflake against `stg_job_postings.sql`'s own SQL `case`,
  which never sees a hint — and that side is decommissioned. There is deliberately **no
  backfill**: `upsert_postings` rewrites `role_category` on conflict and `deactivate_stale`
  retires the rest, so the column drains over the 7-day staleness window, and bad-value →
  `uncategorised` changes nothing for retrieval since neither is selectable as a preference.
  `service.ingest._report_discarded_hints` now logs, per source, how many rows carried a
  discarded hint and its top values — a report for a human in the shape of
  `unmet_demand_terms()`, so writing a curated map becomes evidence-driven. **Writing one is
  deliberately open**, gated on whether the vector path replaces the recall predicate that
  would consume it; the measured labels are in `notes/2026-08-08-role-category-hint-guard.md`.

---

## Open items: the security review, and what was measured and left alone

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

### Seniority: six levels and a NULL (measured 2026-08-12, shipped the same day)

The three-value scheme was replaced after measuring it against the live corpus — 128 080
active postings. What the measurement said:

| | rows | % of active |
|---|---:|---:|
| titles naming **no level at all** | 79 719 | **62.2%** |
| `manager` as the *only* level signal | 11 210 | 8.8% |
| genuine leadership word, no senior word | 8 165 | 6.4% |
| senior/principal/architect/expert only | 16 102 | 12.6% |
| `staff` only | 1 663 | 1.3% |
| intern/Praktikum/Werkstudent only | 1 761 | 1.4% |
| junior only | 915 | 0.7% |
| roman-numeral `II` only | 854 | 0.7% |
| medior / mid-level only | 319 | 0.25% |
| graduate / entry-level only | 262 | 0.2% |

Three conclusions, none of them re-derivable from intuition:

1. **`mid` was never a level.** Stored `mid` was 82 427 rows against 79 719 titles containing
   no level word — the same number. The column could not say "the title did not say", so it
   said "mid" instead, and every consumer read a default as a fact. `mid` is now a *stated*
   level of ~1% and NULL carries the rest. A small honest bucket beats a large dishonest one.
2. **27% of everything stored `senior` was senior because of the word "manager"** — *Account
   Manager*, *Territory Sales Manager*, *Assistant Manager*. All of them were hidden from every
   junior/mid filter on `/jobs` and `/matches`. Bare `manager` now sets no level. Symmetrically
   `associate` was in the *junior* pattern and tested first, so *Associate Director* classified
   as a junior hire; it is ambiguous across industries and now says nothing.
3. **`staff` was suspected and cleared.** The UK "Staff Nurse means an ordinary nurse" false
   positive does not exist in this corpus — all 1 663 rows are senior IC. Recorded because the
   next person to read the pattern will have the same suspicion.

**Stated-rate by country is the finding that constrains the feature**, not the vocabulary:

```
SE 37 623 active — 5.6% state a level    US 27 041 — 41.0%    DE 7 268 — 42.9%
CZ  9 237        — 15.3%                 GB  9 313 — 36.7%    CA 2 457 — 43.6%
NO    959        — 13.3%                 PL  2 996 — 22.4%
```

Sweden alone is 29% of inventory and essentially never states a level in the title; SE+CZ+NO+PL
is ~40% of the corpus at single-to-low-double-digit rates. **This is why seniority is not a SQL
gate on the delivery path** and why `/jobs`' Level menu is offered unfaceted — a faceted menu
would be honest about the thin markets, and is the open question below.

`"Mid-Market"` and `"Mid-Enterprise"` are sales *segments*, not levels — the same class of trap
as the `georgia` rule. `_MID_FALSE` in `search_jobs.py` is what excludes them.

**What the column actually holds after the backfill** (128 080 active, 2026-08-12):

```
NULL 90 613 (70.7%)   senior 22 109 (17.3%)   lead    11 209 (8.8%)
mid   1 063 ( 0.8%)   intern  1 398 ( 1.1%)   junior     996 (0.8%)   entry_level 692 (0.5%)
```

NULL is 70.7% rather than the 62.2% of titles containing no level *word*, because the
`manager`-only and `associate`-only rows also stopped claiming a level. **Quote 70.7%.**

**Still open, deliberately not done in the same commit:** `/jobs` offers the six levels
unfaceted and with no counts, so a Swedish visitor ticking *Senior* gets an empty page with no
explanation, where `/matches` would have shown them a count of 0 before they clicked.
`search_facets` does not compute a seniority facet today. **Trigger:** any signal that visitors
are filtering to empty on the public feed.

### Remote is two questions, and we were only answering one (measured 2026-08-14)

`work_mode` answers *is there an office*. The question subscribers actually read into the word
"remote" is *and where am I allowed to live while doing it* — "remote within Germany" and "work
from anywhere" are the same chip on `/jobs` today and are not the same job. `postings.remote_reach`
(migration 021, `geo.remote_reach`) is the second answer: `anywhere | region | country | NULL`.

**The headline is the distribution, not the coverage.** Backfilled over the whole corpus
(170 299 scanned, 18 160 written); all 16 296 active fully-remote rows:

| verdict | rows | % |
|---|---:|---:|
| `country` — work from home, one named country | 13 377 | **82.1%** |
| `region` — a macro-region, a timezone band, or ≥2 named countries | 878 | 5.4% |
| `anywhere` — no geographic restriction stated | **83** | **0.5%** |
| NULL — the posting never said | 1 958 | 12.0% |

A stratified 300-per-source sample taken first said 71.6 / 5.3 / 0.75 / 22.3, and the difference
is instructive rather than an error: 300 rows each from cocuma, recruitee and themuse against 300
from ashby is not what the corpus looks like, and those small sources are the badly-covered ones.
**Quote the full-corpus numbers.** What the sample was actually good for was making per-source
auditing cheap — it is how the three false positives were found — not estimating a total.

So the intuition that remote usually means work-from-home is correct, and it is *overwhelmingly*
correct. **83 postings** in the entire live corpus are provably work-from-anywhere. **Do not build
a feature on the assumption that this inventory is large, and do not re-argue its size from
intuition.** The economic reason is not a data problem: a company can
usually only employ you where it has a legal entity and payroll, so country-bound is the default
and genuinely global roles are rare enough to be a selling point — which is exactly why the ones
that exist tend to say so, and why positive detection has decent recall on that specific class.

**The first measurement was wrong because it read the wrong layer.** Scanning description prose
found an explicit scope on 5.8% of remote rows, which read as "mostly unprovable" — the
education/on-site situation. It was not. The dedicated remote boards publish scope as a
*structured field*, and three adapters were fetching theirs and discarding it:

| source | field | was |
|---|---|---|
| weworkremotely | `region` ("Anywhere in the World", "Europe Only") + `country` | **`location=None`, hardcoded.** 100% of rows location-empty — the only such source in the corpus |
| himalayas | `timezoneRestrictions` (UTC offsets) | fetched, dropped; the docstring claimed it was kept |
| ashby | `secondaryLocations` (the other countries a role is open in) | dropped |
| greenhouse | `offices` (country-level list) | dropped |
| remoteok | — | genuinely has no scope field; 31% coverage is the board's ceiling, not a bug |

Coverage per source is a property of the *board*, so the corpus average describes neither half:
workday/oraclecloud/smartrecruiters are 100% `country` (big employers, payroll-bound), the remote
boards run 94–100%, remoteok 31%.

Three design rules, each of which was a false positive on live data first:

1. **A named country outranks every wider signal.** "Anywhere in the United States" is a US-only
   role; matching "anywhere" first called it `anywhere`, which is the single most expensive error
   available here. A Himalayas timezone band (`["United States"]` + `[-10..-5, 14]`) read as
   `region` on **all 300 sampled rows** — the band is a refinement *inside* the country. And a
   city-implied country never counts beside a stated one ("London, London, Ontario, Canada").
2. **Prose is read through a keyhole.** `anywhere` from a location field was right 22/22; from
   body copy, 1/3. Both failures promised freedom and withdrew it in the same breath ("almost
   anywhere"; "anywhere in the world as long as you are between UTC-5 and UTC+2"). A bare "based
   in" is an employer's head office — five of eighteen prose verdicts were a company address read
   as an eligibility rule.
3. **`scope_raw` must never be folded into `location`.** `resolve_location` takes the first
   country n-gram it finds, so Ashby's secondaries in front of "Paris offices" resolve to DE with
   no city, and the Paris job leaves every Paris subscriber's digest while appearing in Germany's.
   Nothing fails — a country resolved is a country resolved.

NULL is 22% and passes every gate, on the same rule as `work_mode` and `education_min`. The
column is set **only for fully-remote rows**: the reach of an on-site Berlin job is a category
error, and deriving one anyway would fill the column with trivially-`country` rows and make
coverage look far better than it is.

### What `/jobs` does with it: Country means where, Work setup means how

Shipped 2026-08-14, immediately after the above. The measurement said "work from anywhere" is 83
postings, which killed the obvious feature (an opt-in work-from-anywhere refinement) and pointed at
a better one — because the useful question was never "is this unrestricted" but **"can I, here,
hold this job"**.

`remote_reach` alone cannot answer that: `region` covers "Europe", "APAC" and "US or Canada" alike.
`postings.reach_areas` (migration 022, `geo.REACH_AREAS`) is the second half — which of the areas we
can name a scope actually includes. Over the 961 multi-country remote postings:

| Country-menu row | postings | |
|---|---:|---|
| **EU-International** | 568 | scope includes an EEA country, a European macro-region, or a European timezone band |
| **North America-International** | 403 | scope includes the US or Canada |
| — in both | 150 | **the only set where someone in the EEA can hold a US-facing role** |
| EEA only, closed to North America | 418 | "Europe", "EMEA", "CET ±3" |
| NA only, **closed to Europeans** | 253 | "Remote, Canada; Remote, US" |
| neither (APAC / LATAM / Middle East) | 114 | a real region, no row for it |

**The two rows overlap and their counts must never be summed** — that would double-count the 150,
which are the most interesting postings in the set. It is why `reach_areas` is a `text[]` and not a
single-valued column: one value would have to pick, and lose them.

**A remote job bound to one country is in neither row.** It sits under its own country, and Work
setup's "Fully remote" is what says it is remote. That split is what keeps the rows meaningful:
13 377 of 16 296 active remote postings are single-country, and admitting them would make an
international row mean nothing. It also replaced the thing that was actually broken — a synthetic
"Remote" row at the top of the Country menu that ORed *every* fully-remote posting into the location
filter, so a location control answered a work-arrangement question and a Prague visitor ticking it
was shown US-only roles.

`eea` is `EEA_COUNTRIES`, **not** `COUNTRIES`: GB is selectable and outside the EEA, so "Australia,
Canada, New Zealand, United Kingdom, United States" is `na` alone. Same decoupling, and the same
consequence, as the `eu` remote scope in `location_predicate`.

Three things that were wrong first and are now pinned:

1. **Named countries and macro-regions are unioned, not chosen between.** "Canada, Europe, USA"
   names two countries *and* a region; reading only the countries filed 26 live postings that a
   European may hold as North-America-only.
2. **The timezone patterns read the raw scope text.** `normalise` reduces punctuation to spaces, so
   "UTC+2" (Europe) and "UTC-8" (California) both become "utc 2" / "utc 8" with the sign — the whole
   signal — gone. A signed-offset pattern applied to normalised text is a regex that can never
   match, and it fails silently. They are case-insensitive for the same reason: boards write "CET".
3. **Both columns come from one `geo.classify_reach` pass.** Read from separate calls, a posting
   whose scope field says EMEA and whose location says APAC is `region` from the first and filed
   under the second — and neither column looks wrong on its own.

An unrecognised `intl` id is **dropped**, which widens, so frontend/backend drift on `REACH_AREAS`
would make the filter silently return every job rather than error. That is why the mirror has its
own drift test even though the ids are two short strings.

**What is still deferred:** the ~1 400 remote postings whose scope is unstated *and* whose country
is unresolved are reachable through no Country row at all — only through Work setup = Fully remote
with no country picked. Nothing is hidden, but the Country menu cannot name them. **Never "fix" that
by folding them into an international row**: 12% of remote inventory would then carry a promise none
of those postings made. There is also no row for APAC or LATAM (114 postings) and none for a
GB-inclusive-but-not-EEA scope, and no timezone filter — the offsets exist for one source only, so a
hard filter would have a near-empty menu; a badge is the shape if it is ever wanted.

**Re-measure before extending any of this.** `scope_raw` was still 0 on every source when the
backfill ran, and fills in only as each adapter re-ingests over the staleness window, so the boards
publishing the cleanest scope fields (WeWorkRemotely, still reading 90% unknown off pre-fix rows)
are exactly the ones these numbers understate. `backfill_remote_reach`'s own by-source report is the
check.

### The country a posting is stored under is one of the several it names (2026-08-15)

The two sections above both work on the assumption that a posting has *a* country. It does not.
`resolve_location` has to pick one, because `postings.city` has to agree with it — and every other
country the posting named was then reachable by nothing: not the Country filter, not the facet
counts, not `location_predicate`. A Printful posting listing twelve countries answered eleven
country filters with silence, and no test, watchdog or coverage report could see it, because each
of those eleven was individually a correct "no rows".

`postings.reach_countries` (migration 023, `geo.reach_countries`) is the fix: every country the
posting names, when it names two or more. It is ORed into the country test on both query paths.

**It is deliberately not gated on being remote, and that asymmetry is the point.** `remote_reach`
and `reach_areas` answer *where may I live*, which is a category error for an on-site job — that
is why they are remote-only, and that rule stands. This column answers *is there a job for me in
country X*, and a role listed at offices in two countries answers it yes in both. Gating it on
remoteness is the obvious symmetry and would discard most of what the Teamtailor probe found.

Three things are load-bearing:

- **`countries_named`'s "an explicit name discards every city-implied country" rule is right for
  one location phrase and wrong for a list of them.** `"Warsaw, PL; Kyiv, UA; Bucharest, RO;
  Tallinn, EE; Barcelona, ES"` has five cities in five countries, of which only `ua` is a token
  this module reads as a country outright. That one hit filled the `named` bucket, the other four
  were dropped as merely implied, and a role open across five countries classified `country` —
  Ukraine, the country the posting says least about. `geo.countries_in_scope` splits on `;` and
  resolves each entry independently, so both rules hold: "London, London, Ontario, Canada" is
  still one phrase and still Canada alone.
- **The facet has to count what the filter returns.** The country menu counted `country_code`
  alone; ticking Ireland would have returned a row the menu said did not exist. Counted with
  `array_agg(distinct ...)` over `country_code ∪ reach_countries`, because the primary country is
  usually *also* in the array and a naive unnest would double-count exactly the numbers this
  change corrects.
- **`eu` remote scope tests the array against `EEA_COUNTRIES`, not `COUNTRIES`.** Same rule as
  everywhere else, same counterexample: a role reaching only GB and US reaches two *selectable*
  countries and no EEA one, and must not be admitted at `eu` scope.

**This widens the digest's candidate pool**, deliberately — a subscriber who picked Poland now
also sees a role whose stored country is Spain and which names Poland among its own locations.
Every country in the column was named outright by the board; **nothing here is read from prose**.
Reach classification reads prose through a keyhole because it is unreliable there (`anywhere` from
body copy was right one time in three, and five of eighteen prose verdicts were an employer's head
office read as an eligibility rule); minting a *country-filter entry* from that would be a stronger
claim on the same weak evidence.

### The macro-region words were English-only (2026-08-15)

Measured while doing the above: `geo` read "Europe" in English and French, so a German board
writing `Europaweit`, a Dutch one writing `Europees` and a Czech one writing `Evropa` all
classified as **no scope at all** — silently, because unknown is the safe answer everywhere in
this module. Arbeitnow, a German board, ran 48% unknown. Country names were never the gap:
`COUNTRY_ALIASES` has read "Deutschland" and "Nederland" all along. Only the region words were
English, and they are the half that produces an *international* row.

`_EUROPE` and `_EUROPE_SUB` are one constant feeding both `_MACRO_REGION` (is this a region) and
`_EEA_AREA` (does that region include the EEA); two copies of a word list drift, and dropping
`emea` from one of them while refactoring is precisely what the regression list caught. The stems
are enumerated rather than written `europ\w*` — the short form also matches *Europcar*, and a
company name is not a scope. `norden` is **excluded** under the `georgia` rule: it is the Nordics
in three languages, a town in Lower Saxony, and the ordinary German word for "the north";
`nordisk` carries the meaning with no collision.

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
