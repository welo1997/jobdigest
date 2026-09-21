# Sources: what is built, what is refused, and why

Every source verdict this project has reached, with the clause or the measurement that
settled it. **Read this before adding, re-probing, or re-arguing any job source** — most
questions that look open here are already closed, and the reasons are not guessable.

The standing policy is in `CLAUDE.md`: if a source does not already permit us, we skip it,
and we do not write to publishers.

## Link checking and board probing

**`scripts/check_links.py` is the one check that leaves the machine, and it exists because
nothing here had ever fetched a URL it stored.** `posting_id = md5(url)` and the link goes
straight into an email `href`, so a dead link is invisible to every other check: right row
count, fresh ids, no churn, correct country split, green tests — and a subscriber clicking
nothing. That failed twice in three days (startupjobs 404s on 2026-08-06, MPSV's non-route
`?id=` on 2026-08-08). It runs each adapter under bounded sampling, builds URLs through the
real `normalize`, fetches them and asks whether the page carries **the posting's own title**.
Five things are load-bearing:

- **A 200 is not a pass, and neither is the employer's name.** A link landing on
  `jobs.lever.co/spotify` rather than the posting carries the employer on every row, so
  employer-only is `WEAK` — accepting it passes the exact failure being hunted.
- **A shell is detected by text-to-HTML *ratio*, not length.** Platsbanken answers 124 KB
  holding 1 619 characters of navigation chrome (1.3%) and read as `MISMATCH` under a length
  test — a working adapter reported broken. Two of its links also passed on a *single* title
  word matched against that chrome, so title evidence needs two distinct words.
- **`BLOCKED` ≠ `DEAD`.** Himalayas 403s the honest agent *and* a spoofed Chrome string, and
  opens fine in a browser. Failing on it daily is how a red check gets muted.
- **`BROWSER_CONFIRMED` is a dated record, not an exemption.** Six sources are unprovable over
  HTTP by construction (workday, oraclecloud, platsbanken, mpsv render client-side; himalayas
  is behind Cloudflare; workingnomads is a redirector). All six were opened by hand on
  2026-08-08 and were correct. A run flags only sources **nobody has ever looked at**.
- **Sample boards across the list, and links across employers.** `--boards N` widens the ATS
  board lists via `Spread` (evenly spaced, deterministic) — never `Trim`, which takes the head,
  and never a query dimension like search terms, which multiplies requests without exercising
  any new URL construction. Both halves matter: the first wide run fetched eight Greenhouse
  boards and still probed six Stripe links, because `normalize` concatenates board by board and
  Stripe alone carries hundreds. `_pick` round-robins by employer, after which Greenhouse alone
  exercised five distinct URL shapes (`stripe.com/jobs/search?gh_jid=`, `boards.greenhouse.io`,
  `job-boards.greenhouse.io`, `job-boards.**eu**.greenhouse.io`, `careers.toasttab.com`).
- **Never manufacture a failure as you widen.** Three false verdicts appeared the moment the run
  got bigger, each fixed rather than tolerated: a 429 from probing one host six times reads
  `THROTTLED`, not `DEAD` (waited out once, `Retry-After` honoured, capped at 30 s); a refusal
  *after a redirect* belongs to the destination (`jobs.livestorm.co` forwards to
  welcometothejungle.com, which 403s bots and serves the job to a browser); and a title with no
  distinctive tokens — "PHP Engineer", "QA Engineer", where `php`/`qa` are under the length
  floor and `engineer` is a stopword — now matches verbatim first. A checker that goes red for
  its own reasons stops being read.
- **Not in CI**, deliberately — two dozen third-party sites would make it red for reasons that
  are not ours. `--self-check` *is* in the suite, covering the part that rots silently: the
  sampling table names attributes that live in the adapters, and a rename does not raise, it
  silently stops bounding a source. Same lesson as `pending_boards.py` and the CI skip-check.

It samples the **adapters**, not the database, so it proves the URL an adapter builds *today*
resolves — not that stored rows carry it. Baseline 2026-08-08, after the two removals below and at 6 boards × 5 links: **105 links
across 21 sources — 77 OK, 0 DEAD, 0 MISMATCH**, every non-OK result one of the six
browser-confirmed sources above, exit 0. The narrow first pass (63 links, one board per ATS)
missed both dead boards; widening to 8 × 8 is what found them.

**A live API is not a live board, and `probe_boards.py` structurally cannot see the
difference** — it reads the API, which is the half that stays alive. The wide run found two:

- **`ashby:forto`** — the posting API answers with **12 jobs, all `isListed: true`**, and every
  `jobUrl`, plus the board root, renders "Page not found". Not expiry; the newest was three days
  old. The employer left Ashby and the API was never torn down. Right count, right titles,
  stable ids, twelve 404s in a subscriber's inbox.
- **`recruitee:payconiq`** — all three offers still come back with `careers_url`s, and all three
  **redirect to `recruitee.com/`**, the ATS vendor's marketing homepage: 200, 18 898 characters
  of real text, defeating every status-, length- and ratio-based test. Only "does the page carry
  the posting's title" catches it. `KNOWN_IMPOSTORS` does not cover this and must not be
  stretched to — payconiq was genuinely Payconiq's board, and then it stopped being one.

Both are removed. When a board's volume looks healthy but oddly static, probe the **public
page**, not the API.

**It also found a bug in a field nobody was watching, because it prints the employer next to
the link.** Every Himalayas row came back with `companyName: "name"` — the literal string, all
20, from a 66-minute-old CDN cache, while other parameter combinations returned real employers
at the same moment. A poisoned upstream cache variant, and the variant our ingest uses.
Undetectable otherwise: right count, right titles, right links, and a plausible-looking
string that would have shown as the employer in digests. `himalayas._company` refuses
placeholders, and **dropping the name is the safe direction**: `digest.dedupe_key` treats an
empty key as always unique, never a match, whereas a uniform wrong employer collapses two real
employers advertising the same role in the same city and the second is never emailed.
`normalize` logs an error when a whole run shares one employer, because the next degraded
payload will pick a different string and that *shape* is the thing to watch.

---

## Adapter constraints, source verdicts and country sweeps

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
- **`scripts/discover_seed.py verify` automates the identity read on `discover_ats` output**
  (2026-08-27). `discover_ats` proves a board *serves jobs*; the impostor check — does it *belong*
  to the company whose name it spells? — was a human reading `inspect_hits.py`. `discover_seed`
  reads the board's own postings and returns **PASS** (the ATS exposes the employer's own name —
  `teamtailor`/`workable`/`greenhouse`/`recruitee` — and it matches the probed company, in the
  target region), **REJECT** (a positive name mismatch — the impostor caught), or **REVIEW**
  (`lever`/`ashby` expose no org name; no target-region posting; borderline name — a human's call,
  evidence attached). Verified rows are still hand-copied into the curated lists.
  **Candidate *generation* stays manual — three automated sources were tried and all fail, each
  verified against live data before the conclusion, so do not rebuild them:**
  - **Certificate Transparency (crt.sh / certspotter): dead.** `teamtailor` and `recruitee` serve
    every tenant under a single **wildcard cert** (`CN=*.teamtailor.com`), so no per-tenant
    certificate is ever issued and CT logs cannot enumerate tenant slugs at all — the log holds
    only the ATS's own infra subdomains (`blog`, `assets`, `dashboard`…). crt.sh being 502 at run
    time was a red herring; the approach never worked. (Checked with `openssl s_client` on live
    tenants.)
  - **ARES (CZ register, CZ-NACE 62): a flood.** `czNace:["62"]` matches ~151 000 subjects (ARES
    caps a query at 1 000, 400s above), ~3 100 even narrowed to joint-stock `a.s.` — overwhelmingly
    dormant micro-`s.r.o.` that run no ATS. Near-zero yield; the CZ-is-platform-bound measurement
    met head-on (`mpsv` already carries 6 804 CZ postings / 3 818 companies; permitted ATS reaches
    ~58).
  - **Common Crawl: no coverage.** Its URL index for `*.teamtailor.com` returns only
    `www.teamtailor.com`; tenant boards are `noindex`/low-rank and are not crawled.

  The lesson: **an ATS whose tenants share a wildcard cert and are un-indexed cannot be enumerated
  from outside** — the only route to its tenants is a company's own careers page, i.e.
  `discover_ats` with a hand-assembled company list. The measurement stands: CZ coverage is
  platform-bound, not discovery-bound, and there is no cheap automated seed for it.
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
  per-vacancy portal link was `up.gov.cz/volna-mista-v-cr?id=` — stable, unique, and marked
  **unverified** because the server returns an identical shell for a bogus id. It was also
  dead: `up.gov.cz` is client-rendered and routes on the **fragment**, so the vacancy is at
  `#/volna-mista-detail/{portalId}` and `?id=` is not a route — the app ignores it and shows
  its own empty search page. **Every MPSV link ever emailed was broken**, and it surfaced
  only when a subscriber clicked one (2026-08-08). Fixed by the startupjobs shape:
  `JOB_URL` carries the fragment route and `posting_id` keeps hashing `ID_URL`, the id-only
  string every previous run stored, so the links repair in place via `upsert_postings` and
  **no id churns** — hashing the new link would have re-created all ~7 300 CZ rows. The
  general rule, now twice: a source returning the right *number* of rows says nothing about
  whether its links resolve, and "unverified" in a comment is a bug nobody has looked at.
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
- **Never use LinkedIn beyond its public RSS** — account ban risk. And **no browser
  automation on the sourcing path**: the ban is scoped to *sourcing* and is about terms/legal
  risk, not the tool — if a board can only be read by driving a headless browser, its anti-bot
  wall or terms are refusing us, so it is closed (`test_politeness.py` guards adapter agents).
  Browser automation for dev/UX testing or a future *user-authorised* auto-apply flow is a
  separate, permitted matter. As
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
- **The same failure has a quieter form: a field fetched and never read.** Volume was right,
  ids were fresh, links worked, and four adapters were dropping the geographic-scope field
  their board publishes (found 2026-08-14 while asking whether "remote" could be split into
  work-from-home and work-from-anywhere — see `docs/jobdigest.md`). What each one holds, now
  carried in `JobPosting.scope_raw`:
  - **WeWorkRemotely** — `region` is a closed publisher vocabulary ("Anywhere in the World",
    "Europe Only", "Asia Only") present on 100 of 100 entries, plus `country` (an explicit
    allowlist) and `state`. `normalize` passed **`location=None` outright**, so all 170 rows
    were location-empty — the only such source in the corpus, and it looked like the board's
    fault. `state` is still deliberately **not** read: it is the *employer's* seat, and Proxify
    advertises "Anywhere in the World" with `state: Stockholm`.
  - **Himalayas** — `timezoneRestrictions`, a list of UTC offsets, on 20 of 20 sampled rows.
    Fetched and dropped, while the module docstring claimed it was kept. It is the only
    structured timezone data in the corpus.
  - **Ashby** — `secondaryLocations`, the other countries a role is open in ("Germany",
    "Portugal (remote)"). On the primary `location` alone, a role open in three countries is
    indistinguishable from a desk job.
  - **Greenhouse** — `offices`, a country-level list that names the country a bare city
    location omits ("Remote, Bangalore" + `[{"name": "India"}]`).

  **RemoteOK genuinely has no scope field** — checked, not assumed — so its 31% coverage is the
  board's ceiling rather than an adapter bug. That distinction is the reason to probe the raw
  payload before "fixing" a source that looks thin. And **a recovered field must not be folded
  into `location`**: `geo.resolve_location` takes the first country n-gram it finds, so a scope
  list in front of the real city silently re-homes the job to another country, with nothing
  failing.
- **That 2026-08-14 pass probed the six remote boards and stopped there, and the ATS adapters had
  the same bug** (found 2026-08-15). Each of the four holds a `locations[]` array and read exactly
  one element of it. Measured against live payloads, 16 boards per source:

  | source | field | postings naming >1 location | …and >1 country |
  |---|---|---:|---:|
  | **teamtailor** | schema.org `jobLocation[]` | 32 of 174 | **19** |
  | **lever** | `categories.allLocations` | 32 of 349 | 4 remote |
  | **recruitee** | `locations[]` | 12 of 106 | 3 remote |
  | **workable** | `locations[]` | 1 of 427 | 1 |

  Teamtailor is the worst and was the least suspected: the reader was called `_first_address`, so
  the discard was in the function's own name. One Printful posting lists **twelve countries** and
  was stored as Barcelona alone. Workable's rate is the lowest and its one hit is the clearest —
  open in ES, CZ, GB, IE, PT and NL, stored as Spain — which is the argument against ranking this
  work by hit rate. Note also that `_country` in workable *already* iterated `locations[]` and
  stopped at the first usable code: the array was known to be there.
  **A multi-entry list is not by itself a remote scope.** Most of Lever's are an employer's office
  pair (Spotify runs "Stockholm, London" on hybrid and on-site roles alike). They still name two
  countries the job can be held in, which is `reach_countries`; they say nothing about where its
  holder may live, which is why `remote_reach` is still computed only for fully-remote rows.
  The entries are joined with **`;`**, because the comma is already spoken for inside one entry
  ("Warsaw, PL") — and `geo.countries_in_scope` splits on it, resolving each entry independently.
  Without that split, `countries_named`'s "an explicit name discards every city-implied country"
  rule — correct within one phrase — read five cities in five countries as Ukraine alone.
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
  and large employers, and that killed seven of the boards it justified: see the `dedupe_key` note in
  `docs/jobdigest.md`. **Varbi, Sweden's academic/public-sector ATS and the analogue of Norway's Jobbnorge,
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
- **SmartRecruiters is REMOVED (2026-08-11), and it is the first source settled on robots
  alone in the *refusing* direction.** `api.smartrecruiters.com/robots.txt` is, in full:
  `User-agent: LinkedInBot` / `Allow: /v1/companies/` / `User-agent: *` / `Disallow: /`. That
  is not a blanket rule we happen to fall under — it names **the exact path the adapter used**
  and grants it to one crawler that is not us, so whoever wrote it considered precisely this
  access and decided who may have it. `dev.smartrecruiters.com` serves the byte-identical file,
  so their own developer documentation is refused too.
  **Checked for a published grant that could outrank robots, because one would have:**
  `www.smartrecruiters.com/legal/` is a complete legal index listing **no API Terms of Use and
  no Developer Terms**; the visitor-facing Terms of Use prohibit *"Use automatic means to
  access content or data from other users"* and *"Harvest, collect, gather or assemble
  information or data"*, and grant no republication right. Stated honestly: that clause is
  scoped to *"other users"* and a job ad is arguably the employer's public advertisement, so it
  is suggestive rather than squarely on point — **the robots file is what decides this**, and
  the only remaining route to permission is correspondence.
  **This inverts the rule above, which is why it is worth stating separately.** Ten sources
  permit in robots what they refuse in their terms, so the rule reads "never settle on robots
  alone" — but that is about *permissive* robots being weak evidence. A refusing robots is
  strong evidence on its own. Kept as tested code on the `jobscz`/`profesia` footing so
  reversing it is a decision rather than a rewrite; `test_source_exclusions.py` fails if it
  returns to `gather()`.
  **The cost is real but was first stated 4.5× too high, and the error is instructive.** The
  removal was argued as "~7 700 postings", taken from `probe_boards.py`'s
  `smartrecruiters 9/7 692` — which is **what the boards hold**, not what we stored, and not
  what a subscriber could reach. Measured on production the evening of the removal: **3 286
  active rows, of which only 1 690 are in a selectable country** (plus 59 with a null country,
  which are kept and left to the AI matcher). The largest single country was **India at 445**,
  ahead of US 335, DE 258, GB 202, CN 202, VN 158 — roughly half the adapter's stored output
  was inventory the geo gate already dropped. So the honest price is **~1 690 reachable
  postings**, and the decision is easier than it looked, not harder.
  That is this file's own recurring lesson arriving through this file: **a number measured at
  one stage does not describe another.** Board-held, stored-active and subscriber-reachable are
  three different quantities, and the first is the one most often to hand. No backfill is needed
  or wanted — `store.deactivate_stale` runs each ingest at `--stale-days 7`, so the rows drain
  on their own within a week of the source ceasing to be fetched.
  **The finding that outlives the source:** the adapter imported `politeness` and took only
  `HEADERS` from it — so it sent the honest agent and then made the request anyway, up to
  ~6 150 times a night at 8-way concurrency with **no throttle at all**. Nothing checked that an
  adapter which leaves the machine actually *calls* `robots_allows()` and `throttle()`;
  `test_politeness.py` only checks the user-agent, which is the failure that had already
  happened. **That guard is still unwritten, and it will go red for roughly eight adapters when
  it lands** — that is the finding, not a reason to weaken it. Full workings in
  `notes/2026-08-10-smartrecruiters-robots.md`.
- **StartupJobs is REMOVED (2026-08-26) — on Czech database rights, and separately on a
  refusing robots.txt.** It was removed on the robots half alone and **the terms were read a few
  hours later, in answer to "do they actually forbid it?"** — the wrong order, and the honest
  record of it is below. The terms are the binding half. The
  robots reasoning that follows is still correct and still sufficient, but it is no longer the
  reason.
  **The terms (effective 15 June 2026, governing `www.startupjobs.cz` *and*
  `www.startupjobs.com`), section "Copyright, databases, and licenses":** *"StartupJobs.cz and
  the content published thereon constitutes a database within the meaning of applicable legal
  regulations, in particular Act No. 121/2000 Coll. … The Provider holds all rights to the
  database. It is prohibited to interfere with the database; furthermore, it is expressly
  prohibited to extract from the database or to qualitatively or quantitatively exploit its
  content or part thereof."* **`extract` and `exploit` are the statutory verbs of the sui generis
  database right** — 96/9/EC art. 7, Czech *vytěžovat / zužitkovávat* — which is the same right,
  named in the same Act, that put Jobs.cz and Profesia out of reach on 2026-08-03. A database
  right binds **with or without a contract**, so "we never accepted their terms" is not an
  answer here either.
  **Stated on the narrowest applicable clause, which cuts against overstating it:** that clause
  sits in the **B2B – For companies** half of the document (char 30 635 of 33 442; the B2B
  section begins at 12 097), addressed to paying advertisers, and the **B2C/candidates half
  contains no database, extraction, exploitation, robots, scraping or crawling language at all**.
  So as a *contract term* it is weaker than Alma Career's §4.11, which governs every user. What
  it establishes unambiguously is that the operator **holds and expressly asserts** the database
  right over the content — and that right is statutory rather than contractual, which is what
  makes it dispositive.
  **Why this matters beyond being a better citation: it closes a reopening path the robots-only
  reasoning left open.** The robots argument turns on *which host serves the data*, so it invites
  the thought that a keyless feed appearing on the permitted `www` host would reopen the source.
  Under the database clause it would not. Re-adding startupjobs now needs a published grant, on
  the jobs.cz/profesia footing — not a change of hosting.
  **The robots half, which was the original and sufficient reason.** The
  board's Symfony API moved for the second time in three weeks. `core.startupjobs.cz` — the
  host the 2026-08-06 rewrite was built against — is **gone entirely**: 404 on `/` and on
  `/api`, not just on the collection, from 2026-08-25's ingest onwards. The same API Platform
  now answers on **`back.startupjobs.com`** (`/api` returns
  `{"@context":"/api/contexts/Entrypoint","@type":"Entrypoint"}`), found by reading the hosts
  the site's own SSR payload names. **`back.startupjobs.com/robots.txt` is, in full:
  `User-agent: *` / `Disallow: /`.** That is dispositive on its own — the 2026-08-11 finding
  that a *refusing* robots needs no terms check to back it — and it is not academic either:
  `politeness.robots_allows()` refuses every request, so repointing `API_BASE` ships an adapter
  that fetches nothing and reports a clean zero, which is this file's own silent-zero failure
  wearing a new hat.
  **There is no permitted route left, which is what makes this a retirement rather than a
  repair.** `www.startupjobs.com` is robots-allow-all and serves `sitemap/offers.xml`, but the
  listing page is rendered from the refused backend and contains **zero** `/job/` links, and
  the sitemap lists its **442 offers as bare ids** — and `/job/{id}` without the slug is a
  clean 404, the exact fact that made the 2026-08-06 rewrite emit 450 dead links. So the
  compliant host cannot even be walked. Driving it with a browser is closed for a second,
  independent reason: no browser automation on the sourcing path.
  **The cost, measured on production the day of the removal, not argued:** **414 active rows —
  389 CZ, 11 SK, 14 with no resolved country** (kept, and left to the AI matcher). Unlike
  SmartRecruiters, where half the output was in countries nobody can select, essentially the
  whole of this adapter's inventory was subscriber-reachable: it was the **second-largest
  Czech source in the stack at 4.9% of 7 930 active CZ rows**, and the largest *startup/tech*
  one — `mpsv`, the 6 884-row leader, is a public register of largely non-professional
  vacancies. 142 of its rows had ever been scored into `matches`, 70 had actually been
  emailed. That price lands on top of Alma Career's, which already costs 92% of Czech
  inventory. No backfill is needed: `deactivate_stale` at `--stale-days 7` drains the rows
  within a week of the source ceasing to be fetched, and `source_watchdog` files an absent
  source as *"retired, ageing out"* automatically because it reads `search_jobs.source_classes`
  rather than a list of its own.
  **The finding that outlives the source: a source that has moved once will move again, and the
  second move is the one that closes it.** The 2026-08-06 fix was good work — it paid off two
  debts and rewrote the adapter onto a documented API — and it bought eighteen days. Nothing
  detected the move except the watchdog's SILENT ZERO on day two, which is precisely what that
  monitor exists for and the one part of this story that worked as designed. Kept as tested
  code on the `jobscz`/`profesia`/`smartrecruiters` footing; `test_source_exclusions.py` fails
  if it returns to `gather()`. Full workings in `notes/2026-08-26-startupjobs-retired.md`.
- **Workday: the ceiling is the lever and the 39 unadded sites are not (measured 2026-08-26).**
  This file has carried "~38 Workday sites found-and-not-added, pending an export-window
  measurement" since 2026-08-14. The measurement was done, and it says do not add them.
  **Recovering the list first**: `scripts/pending_boards.py`'s `carried_by_ats()` over every
  `scripts/*_discovery.csv` yields 47 pending `(tenant, site)` pairs, of which **7 are
  rejections already recorded here** (genpact, aliaxis, adient, thyssenkruppmaterialsna,
  proofpoint, jlp, wonder) and **1 is dead** (`bbva/wd3/es`, 404). So the backlog is **39 live
  candidates holding 10 391 board-side postings** — 34 EEA/EFTA boards with 3 615 between them
  (sandvik 423, galderma 362, georgfischer 332, essity 311…) and 5 US-HQ giants with 6 776
  (abbott, amgen, stryker, medtronic, nike). The 2026-08-14 note's identification of "the 38"
  as the FR/ES/IT industrials is **stale**: every one of those is now in `SITES` (74 sites,
  matching the box exactly).
  **Why they buy nothing at the current ceiling.** The detail pool is **3.3× oversubscribed**:
  19 954 term-matching uniques feed 6 000 detail calls, so `_age_rank` is already discarding
  two thirds of what the wired boards hold. Adding 39 boards adds ~5 150 hits, taking the
  detail share from 30.1% to 23.9% — **every posting a new board wins displaces one from a
  board already wired**, at a measured cost of 2m13s more list-stage wall clock and ~120 more
  requests against hosts that already 429 us. That is a net-negative change, and it is the
  honest answer to the question as asked.
  **The three-number rule, on Workday's own 10 466 active rows**: 40 265 board-held → 19 954
  term-matching → 6 000 detail attempts → 4 729 stored that day → **6 345 in a selectable
  country (60.6%)** → **5 076 subscriber-reachable (48.5%)**. And the fourth number that
  decides the priority: Workday is **7.6% of the active corpus but 1.7% of matches (18 of
  1 033) and 2.7% of sends** — 1.72 matches per 1 000 active rows against a corpus average of
  7.47, i.e. **23% of average**, reconfirming the 2026-08-07 finding on a 1.7× larger
  denominator. Per-board reachable share on the wired 74 runs from 7.3% (accenture) to 90.3%
  (paypal), so the 48.5% prior is weak for an industrial candidate set whose right peers are
  kone 24.4% and valeo 31.5%.
  **The window, measured over eight consecutive runs (2026-08-19..26)**: Workday costs **8m02s
  of a 3h27m pipeline**; the detail stage runs at **24.8 attempts/s median, worst 15.2/s** (the
  adapter's docstring said 9.5, measured on a laptop — the box is 2.6× faster); the upsert costs
  **0.073 s per fetched row**. So 1 000 more details costs 40 s of fetching **plus 64 s of
  upsert = ~104 s, and the upsert is the larger half** — invisible in Workday's own log line.
  Pipeline finish is 06:27 UTC median, **worst observed 07:16:52**, i.e. 43 minutes before the
  08:00 watchdog, and that variance is upstream (platsbanken, nav, the upsert), not Workday.
  **`MAX_DETAILS` raised 6 000 → 10 000** on that arithmetic (~7 min median, ~9 min worst,
  leaving ~30 min); 12 000 fits on paper and was deliberately not taken, because the margin is
  what absorbs a bad day elsewhere.
  **The failure that made all of this hard to see, and is now fixed:** `_detail` returned `None`
  on any non-200 **with no log line at all**, so 9–21% of every run vanished while the list
  stage logged 53–137 HTTP 429s a day — Workday rate-limiting us silently, inside the adapter
  this file's own rules single out. The losses are now counted by status and summarised once per
  run (a warning past 25%). **There is no measurement of that loss rate at 10 000**, so read
  the new line for a week before raising the ceiling again: doubling volume against a limit
  already being applied is how a raise turns into a smaller harvest. `MAX_PAGES_PER_QUERY=10`
  may also be truncating the largest boards' list stage — nothing distinguishes "stopped
  because a page added nothing" from "stopped at page 10", so the 19 954 pool is itself possibly
  a floor.
  **Gate any further widening on conversion, not inventory.** If doubling Workday's inventory
  does not roughly double its 1.7% match share within a fortnight, then neither the ceiling nor
  the 39 boards are the constraint — the keyword prefilter is, and this whole line closes.
- **Workday is the N+1 adapter, and the source of large-EU-employer inventory** (SmartRecruiters
  was the other until the entry above). Its list endpoint carries no description, so each posting
  needs its own detail call, so it needs a way to decide which postings are worth one. It runs
  last in `gather()` because it is by far the slowest and a failure there should not cost
  everything before it. **The SmartRecruiters measurements below are kept as the record of what
  was learned** — the paging lesson is general and the next N+1 adapter will meet it again.
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
- **NAV (Norway) is BUILT — `ingestion/sources/nav.py`, 2026-08-11.** It is the third national
  register after `mpsv` and `platsbanken`, and the only one whose terms *grant* republication
  rather than merely disclaiming a database right. Everything below is the verdict as it stood
  before the build and is kept because the reasoning is the spec; what changed on the day:
  **the endpoints recorded here were dead and were re-derived** (the live base is
  `pam-stilling-feed.nav.no`, not `arbeidsplassen.nav.no/api/*`), **`workLocations` is the
  location field** (not `locationList`), and **`applicationUrl` is empty on every ad sampled** —
  the real permalink is `link`. Measured over 2 000 live ads rather than the 86-ad sample below:
  **majors 1-3 are 51.2%**, **95.5% carry a structured contact**, **0% carry an email in the
  description** and **13.6% carry more than one STYRK08 code** (the 86-ad figures of 59%/9% and
  "70 with an email" were sample artefacts; the email figure was counting the contact block, not
  the text).
  **It is the first and only source that keeps persisted state, and the reason is
  `deactivate_stale`.** The feed is an append-only change log: an ad created a month ago and
  never edited appears in no recent window, while `store.deactivate_stale(days=7)` deactivates
  anything not re-seen in seven days and has no per-source exemption. Without a mirror,
  Norwegian inventory would decay to nothing within a week of each ad's last edit — silently,
  with no error and no failed timer. So the adapter mirrors `uuid -> ad` in a **named Docker
  volume** (`nav_state:/state`; a bind mount under the shipped tree would be destroyed by
  `deploy.sh`'s `git archive | tar -x` and cold-start on every deploy), walks the feed forward
  from a persisted cursor, applies INACTIVE and `expires`, and **re-emits the whole active set
  every run** — so from the pipeline's point of view it is an ordinary adapter.
  **Two traps worth carrying to any future NAV work.** `If-Modified-Since` must be built with
  `email.utils.format_datetime`: a weekday that does not match its date is *silently ignored*
  and the feed serves its 2023-06-14 head, a 200 full of real three-year-old ads (reproduced
  live, one second apart). And **HTML tags must be stripped BEFORE unescaping** — Norwegian
  employers write quotation marks as escaped angle brackets (`&lt;løsninger&gt;`), so unescaping
  first turns them into markup the stripper then eats, silently deleting the quoted word.
- **The pre-build verdict, kept for the reasoning** (checked 2026-08-07, full workings in
  `notes/2026-08-07-norway.md`). `arbeidsplassen.nav.no`'s
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
  **Re-probed 2026-08-09: the terms still permit us and the endpoints have moved.**
  `/vilkar-api` is unchanged and still carries the three clauses above, verbatim. But
  `/api/publicToken`, `/api/v1/feed` and the legacy `/public-feed/api/v1/ads` **all return the
  site's 404 page now**, so the paths recorded on 2026-08-07 are stale and whoever builds this
  must re-derive the current ones from the Datasettbeskrivelse in Felles datakatalog, which is
  where `/vilkar-api`'s own "Slik får du tilgang" section points. Nothing about the verdict
  changes — permitted, self-service — but **do not start from the URLs in this file or in
  `notes/2026-08-07-norway.md`; verify them first.** The token endpoint is the one to find, and
  it is still not `nav.team.arbeidsplassen@nav.no`: mailing for the stable token is
  correspondence and remains refused.
  **That warning paid for itself on 2026-08-11**: the paths were re-derived from the
  Datasettbeskrivelse exactly as instructed and landed on `pam-stilling-feed.nav.no`, which is
  what `nav.py` now uses. The lesson generalises — **a recorded endpoint is a dated observation,
  not a fact**, and this one went stale twice in four days.
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
  is a third permitted register** — see the NAV entry above. "Every remaining country" was true of the ones
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
- **`taxonomy.py` read only English, and the Norwegian half of that was CLOSED on 2026-08-11.**
  The 2026-08-07 measurement recorded here — `Systemutvikler`, `Dataingeniør`, `Produktsjef`,
  `IT-arkitekt` and `Testleder` all `uncategorised`, and `Sikkerhetsanalytiker` misfiled as
  `data_analysis` because "analytiker" matched — is **no longer true of any of the six**. All
  are pinned by `test_the_documented_norwegian_failures_are_fixed`; the security analyst now
  reads `cybersecurity`, and `Dataingeniør` reads `data_engineering` (it had been filing as
  generic `engineering`, because that pattern carries a broad `ingeniør` and
  `data_engineering` knew only the Swedish `dataingenjör` — the same defect one vowel away).
  Measured on a live 2 673-ad NAV corpus: **68.5% → 42.7% uncategorised on a held-out half**,
  with both graded slices holding row for row.
  **The order is the transferable part.** The answer key was built *first*
  (`--fetch-no`, STYRK-08), so boundary calls were settled by the publisher's own coding
  instead of by argument — which is why `miljøarbeider` was **declined**: NAV splits it
  healthcare 5 / social_care 4 / education 3 even after dropping every title that also names
  another profession, so any single answer is a coin flip and the only safe error is a miss.
  Do the same for any further language: **key before vocabulary.**
  **That measurement was taken on 2026-08-08 and it found something worse than blindness.**
  `select source, role_category, count(*)` over the non-English sources did not return
  `uncategorised` for platsbanken — it returned **954 distinct Swedish SSYK labels**, because
  `classify` was handing the source's own hint back verbatim (see the `role_category` convention in `CLAUDE.md`
  and the detail in `docs/jobdigest.md`). So the Swedish corpus was not sitting in a first-class "unknown" bucket, it was in
  14 135 categories that no query could name. The guard fixes the column; **it does not teach the
  taxonomy Swedish**, and the tech labels it now files as `uncategorised` are the concrete
  argument for doing so: `Systemutvecklare/Programmerare` 220, `Projektledare, IT` 140,
  `Mjukvaruutvecklare` 108, `IT-arkitekt/Lösningsarkitekt` 97, `Systemförvaltare` 118 —
  ~600 rows of unambiguous software/platform work, plus ~1 500 sales and ~317 IT-support rows
  in categories the taxonomy does not model at all. Do this **after** the vector gate
  (`scripts/measure_shadow_recall.py`), not before: if cosine replaces the recall predicate, a
  hand-curated SSYK map is work done for a code path being retired.
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
  **BUILT 2026-08-15** (`ingestion/sources/werkenvoornederland.py`), and two things found in the
  building are worth more than the entry above:
  - **The JSON-LD `description` is a one-line summary**, not the vacancy text — *"Senior
    Business Analist ERP in Den Haag voor 32-38 uur bij Ministerie van Defensie"*. The real
    description is in the page body, so the adapter reads both from the one fetch. Anyone
    trusting the JSON-LD alone would have shipped 1 260 postings with no description and a
    matcher with nothing to read.
  - **The personal-data measurement here was understated, and the "0 emails" was an artefact.**
    Re-measured over a 20-page random sample: **15 of 20 pages (75%) carry a phone number, 33 in
    all** — not "3 of 6" — nearly all `06` mobiles belonging to named individuals under a *"Stel
    gerust je vraag"* heading, two per page ("Rianne van Os, Coördinator cluster informatie
    06-25698769"). The zero-email finding holds only for the *rendered* text: the pages carry an
    obfuscated `[email protected]` placeholder that a script rehydrates, so a regex sees nothing
    where a browser shows an address. So the description is assembled from an **allowlist** of
    content-section ids and the contact section is never read — an allowlist, not a denylist,
    because a section the publisher adds tomorrow must be dropped by default. `_scrub` is the
    backstop for a number typed into a body paragraph. `test_werkenvoornederland.py` fails if
    either layer breaks (mutation-checked: adding the contact id to the allowlist goes red).
    **A sample of six is not a corpus check** — that is the transferable lesson.

  By contrast **`werk.nl` (UWV), the much larger national board, is closed**: its
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
- **Unestablished permission: the rule, and the deliberate exception made on 2026-08-15.**
  The standing rule is that a source publishing no terms has not permitted anything — it has
  merely not spoken, and a working endpoint is not a grant. That is the Bundesagentur error,
  and it is why `cyprusjobs`/`goldencareers`, NHS Jobs, NVA's `cvvp`, `hipo.ro` and Personio
  are all closed above.

  **The owner reviewed that class on 2026-08-15 and reversed it for a named, finite set**,
  beginning with **`remoteineurope`** (built the same day). The reasoning recorded at the time:
  the host publishes no terms of use and no privacy policy, none has ever existed in Wayback,
  and its `robots.txt` affirmatively permits the job pages while disallowing `/search`,
  `/companies`, `/checkout`, `/post-a-job-2` and `/internal/` — so nothing anywhere refuses
  automated reading. It was wired for its **`schema-loc` country arrays** (a Wikimedia posting
  names France + Germany + Italy + Netherlands; an Agorapulse one names 42), which is precisely
  the multi-country remote inventory `reach_countries` and the EU-International row are short
  of and which almost no other adapter emits.

  **Four boundaries, and they are the point of the entry:**
  - It is **a decision about risk appetite on the owner's own product**, not a finding that the
    old rule was wrong. Do not cite it as precedent for a source not named in it.
  - It **does not reach a source that refuses.** An anti-automation clause, a personal-use-only
    licence, a refusing robots.txt and an application gate all still close a source; nothing in
    the refusal groups above moves, and `test_source_exclusions.py` is unchanged.
  - It **does not license ignoring robots.txt.** Every adapter added under it still calls
    `politeness.robots_allows` and `throttle`, and `test_politeness.py` is unchanged.
  - The set is **enumerated in `ingestion/tests/test_unestablished_permission.py`**, which fails
    if a source is wired on this reasoning without being listed with the reason. That exists
    because the real risk here is not the first source, it is "we already read boards with no
    terms" quietly becoming a general argument.

  **Wired under it so far (2026-08-15): `remoteineurope`, `goldencareers` (CY, ~62 agency
  postings) — and since 2026-09-21 `goldencareers` alone.** `remoteineurope` was **removed
  2026-09-21 because the board stopped existing**, not on permission: `remoteineurope.com`
  now returns a 301 to `weworkremotely.com/remote-europe-jobs`, and its `robots.txt` and
  every sitemap path (`/sitemap.xml`, `/sitemap_index.xml`, `/sitemap-0.xml`) 404. There is
  nothing to repoint at — the inventory was absorbed by WeWorkRemotely, which `weworkremotely`
  already reads. **Its measured contribution over five wired weeks was zero rows**: the
  adapter raised on the sitemap fetch every run, and `source_watchdog` reported its freshness
  as `never` rather than as a silent zero, which is the one status in that table that means
  "this has never worked" and is easy to read past as "new". Worth remembering when the next
  source is judged: a `never` is not a slow start. Pinned by the enumerated set above; the
  adapter and `test_remoteineurope.py` are kept as working code.
  `nva` (Latvia's live `cvvp` register) was wired here too and **removed
  2026-08-19 — not on permission, on cost/value**: the sector field it gates on lives only on
  each vacancy's detail page, so it fetched ~4 460 pages one-per-second to keep ~570
  low-professional-fit Latvian rows — ~30% of the whole pipeline's wall clock — and no
  subscriber currently wants that inventory. The cheap pre-filter does not exist (the list
  carries the *profession*, but the kept *sector* is the employer's industry, so a cleaner at a
  ministry is `Valsts pārvalde`, a kept sector). Pinned in `test_source_exclusions.py`; the
  adapter is kept as code. Re-add it for a Latvian audience, after solving the crawl cost (the
  827-name profession answer-key project), not because the digest looks thin.

  **Four members of the group were NOT wired, and only one of those is about permission:**
  - **Personio** — its feed answers with a job count for **bogus** slugs (`amazon`, `johnson`,
    `kbc`, none of them customers) and carries no `hiringOrganization` to check against, so an
    adapter cannot tell whose board it is reading. That is a **correctness** blocker: it would
    file postings under the wrong employer. Unrelated to terms, and not fixed by this decision.
  - **`euroremotejobs` and `cyprusjobs`** — both publish no terms *but* serve a Cloudflare
    Content-Signal block reserving Art. 4 EU DSM TDM rights. That is a machine-readable
    **reservation**, not silence, which puts them in a different class from `remoteineurope`
    even though none of the three has a terms page. Left for an explicit call rather than
    assumed either way. `cyprusjobs` is the more tempting of the two — 346 postings with clean
    `JobPosting` JSON-LD — and also carries a freshness problem: the sampled ad was
    `datePosted: 2025-08-22` with `validThrough` a year later, the Muse zombie shape.
  - **NHS Jobs** — closed on a *technical* ground, not permission. `/robots.txt` returns an
    HTML app shell rather than a robots file (so a parser reads it as allow), there is no
    sitemap — `/sitemap.xml` returns the same 15 413-byte shell as every other path, the SPA-200
    trap — and `/candidate/search` renders **2 386 characters of visible text and zero job
    links**. Enumerating it needs a browser, which the sourcing rule forbids. Not a candidate
    until it server-renders.

- **Check robots.txt at the host root, and quote the clause that would refuse you.** Both
  errors happened in one session: `europa.eu/eures/robots.txt` 404'd and was reported as a
  finding, when robots.txt is only ever authoritative at the root — where it exists, is 4 930
  bytes, and carries the `Crawl-delay` above; and ELA's one-line reuse permission was quoted as
  the licence answer while the sentence that guts it sat one page away in the policy it cites.
  A source's terms are settled by the narrowest applicable clause, never the friendliest one.
- **The curated EU-remote boards: six probed, six refuse (2026-08-15).** These were the last
  untried idea for the EU-International row — small curated boards whose whole premise is
  multi-country remote inventory, which is exactly what that row is short of. Settled on terms,
  not probes. **Every one is closed, and none is a near miss.**

  | Board | robots at host root (`JobDigest/1.0`) | The dispositive clause |
  |---|---|---|
  | **euremotejobs.com** | `*` Allow + Cloudflare Content-Signal | `/tos/` forbids *"robots, spiders, scrapers, crawlers, data mining tools, **APIs**, or other automated devices … to monitor, extract, or copy content"*, and the licence *"does not permit any commercial use, redistribution, or republication"*. |
  | **remotifyeurope.com** (QubicleFree OÜ, Tallinn) | `*` Allow + Content-Signal | T&C §8.1 *"Users shall not scrape, harvest…"* and §8.4 *"**Automated access is permitted only through documented interfaces where specifically authorised.**"* — a refusal **and** an authorisation gate. |
  | **nodesk.co** | `*` Allow + Content-Signal | `/legal/terms/`: *"Use any technical or other means … to 'mirror', 'frame', '**scrape**', '**crawl**' or '**spider**' any web pages"*; *"Attempt to or actually access our Site by any means other than through the interfaces provided by our Site"*; and *"You must not use any part of the content on our Site for **commercial purposes** without obtaining a licence."* |
  | **europelanguagejobs.com** (Europe Language Jobs SL, Barcelona) | permits `/jobs`, **and offers `sitemap-jobs.xml`** | `/eulangjobs/legal-conditions`: *"Without the **prior written authorization** … it is forbidden to use, copy, transfer, manipulate"*; the private-copy carve-out holds only where *"it is not used for **commercial gain**, and it is exclusively for the candidate's personal information."* |
  | **euroremotejobs.com** | `*` Allow + Content-Signal | **No terms exist.** Not in `page-sitemap.xml`, every candidate path 404s, the footer's own "Privacy" link 404s, and Wayback has never held one. |
  | **remoteineurope.com** | ordinary robots; `/search`, `/companies`, `/checkout` disallowed, job pages permitted | **No terms and no privacy policy exist** — a one-person board (~410 job pages), sitemap lists every page and none is legal, Wayback has never held one. |

  Four more where robots permits what the terms refuse, taking that count from ten to
  **fourteen**; `europelanguagejobs` is the starkest yet, because it *advertises a job sitemap
  in robots* and then requires prior written authorisation in its terms. Three things worth
  keeping beyond the six verdicts:

  - **`ClaudeBot: Disallow: /` is not about us, and the Content-Signal is not boilerplate to
    wave away.** Four of the six served a **byte-identical Cloudflare Managed Content** block —
    `Content-Signal: search=yes,ai-train=no,use=reference` plus a disallow list of named AI
    crawlers. We are not ClaudeBot; `JobDigest` matches `User-agent: *` → `Allow: /`, so on
    *path permission* those four permit us. Do not cite the ClaudeBot line as if it bound us
    (the 2026-08-13 note on euremotejobs reached the right verdict leaning on the wrong
    artefact — the terms are what actually refuse). Equally, do not dismiss the signal: it is
    served under the operator's domain, prefaced *"As a condition of accessing this website,
    you agree to abide by the following content signals"*, and states that any restriction is
    an **express reservation under Art. 4 of EU DSM 2019/790**. It is a real reservation that
    is *not* evidence of a considered per-source decision — so it is a reason to read the
    terms, never the dispositive clause when terms exist. Expect it on every Cloudflare-fronted
    source from now on.
  - **A 200 from a client-rendered SPA means nothing, and the terms are still readable without
    a browser.** `europelanguagejobs.com` returned **200 for `/terms` and `/terms-and-conditions`
    alike — both nonexistent** (`X-Debug-Fallback: 1`, `X-Debug-URI: /index.html`, 1 441 bytes
    of JS shell for any path). A path probe would have recorded "terms found". What worked:
    **the site's own sitemap named the real route** (`/eulangjobs/legal-conditions`) and the
    **Wayback Machine served the rendered text** (10 113 characters). That retires the
    2026-08-13 verdict shape *"terms cannot be read without a browser (Playwright = out)"* —
    sitemap + archive is not browser automation and is not on the sourcing path, so
    `devitjobs` and `germantechjobs` are re-readable by the same route if anyone wants them.
  - **REVERSED IN PART, 2026-08-15 — see the entry below on unestablished permission.**
    `remoteineurope` is now ingested despite this reasoning, by the owner's explicit decision.
    The paragraph below stands as the argument that was made and the default that still
    governs every source not named in that decision.
  - **No terms at all is a skip, not an opening — and `teamtailor` is the exception that fixes
    the rule's wording.** Two of the six (`euroremotejobs`, `remoteineurope`) publish none.
    Absence of a refusal is not permission — the standing rule is *if a source does not already
    permit us, we skip it*, there is nobody to read, and asking would be correspondence, so
    these close on the same footing as a refusal. That is the "Personio shape" already recorded
    for `cyprusjobs`/`goldencareers`, NHS Jobs and NVA's `cvvp`. **But `teamtailor` publishes no
    ToS either and is built and permitted**, because silence there sits on top of an affirmative
    grant: its robots carries `Content-Signal: ai-input=yes` and the keyless `jobs.json` feed
    exists to be read. So the test is not *"are there terms"* but **"is there a positive
    permission anywhere"** — silence plus an invitation is permission, silence alone is not.
    Neither of these two offers anything of the kind.

  **Do not re-open these because the EU-International row looks thin.** The row's shortfall is
  not a sourcing gap that these boards would have filled: they are curated **re-listers** over
  the same startup/ATS pool already read via Remotive/WWR/Himalayas/Jobicy/Ashby/Greenhouse/
  Lever, so `dedupe_key` would have absorbed much of what they hold and the marginal inventory
  is well below their row counts. `remoteineurope`'s own front page lists Alpaca, Wikimedia,
  Grafana, GitLab, Dataiku, Goodnotes and Storyblok — all ATS-reachable without anyone's
  permission. **Europe Language Jobs was the one genuinely additive slice** (multilingual
  customer-support/BPO roles across ES/PT/GR/MT, which nothing here covers) and it is the
  clearest refusal of the six. The route to that inventory is `scripts/discover_ats.py`, not
  these boards.
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
