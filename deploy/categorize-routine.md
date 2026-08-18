# JobDigest — the weekly title classification

The matcher decides *who sees which job*; this decides *what a job is* — the `role_category`
for titles that neither `service/taxonomy.py`'s patterns nor the publishers' occupation codes
could read.

> **The scheduled path is the metered API (since 2026-08-17).** One systemd unit,
> `jobdigest-categorize.timer`, runs `service.categorize_exchange classify` on the box with
> `ANTHROPIC_API_KEY`. The three-phase Drive exchange described from *"The file exchange
> (retained, unscheduled)"* onwards is **kept as the fallback and as the audited trust
> boundary, but is no longer enabled** — the same treatment `deploy/matcher-routine.md` gave
> the matcher's routine on the same day, for the same reason. To operate the live system you
> need this page down to the end of *"Checking it is working"*; the rest is the record of the
> free-tier flow.

## The metered path (live)

```
uncategorised titles  ──▶  Claude (metered, batched)  ──▶  validate  ──▶  title_categories
```

- **One command:** `python -m service.categorize_exchange classify [--limit N] [--batch N]`
- **Or through the wrapper:** `deploy/jobdigest-categorize.sh classify`
- **See the prompt without spending anything:** `classify --dry-run` (no API call, no key
  needed — it prints the system prompt and the first batch's payload)

Nothing reads `title_categories` until the next ingest: `upsert_postings` rewrites
`role_category` on conflict and every active posting is re-seen daily, so answers stored on
Sunday reach the corpus at Monday 03:00 and show up in `categorization_daily` on Monday
morning. **There is deliberately no backfill step** — the same reason classifier changes need
none.

### Why the key, and why now

The exchange was built on 2026-08-09 for one recorded reason: `notes/categorization/PLAN.md`
item 4, *"No API key. Decided 2026-08-09"*. That premise expired on 2026-08-17, when matching
moved to a metered key on the box. Keeping the file exchange after that bought nothing and
cost a weekly human step — and the measured consequence was that it stopped: production held
**250 rows in `title_categories`, newest 2026-08-10**, against a residue of **17 770 active
postings across 15 139 distinct titles** (1.17 postings per title — a pure long tail that no
pattern reaches). The routine was not broken; it was simply not being run, and nothing about
that is visible from the outside, because a residue that stops shrinking looks exactly like a
taxonomy that has caught everything it can.

### The knobs

| Env var | Default | What it does |
|---|---|---|
| `CATEGORIZE_MODEL` | `claude-haiku-4-5` | Mirrors `MATCHER_MODEL`, same default and same reasoning. Safe to raise: the parser takes the first *text* block, so a thinking model does not silently answer nothing. |
| `CATEGORIZE_BATCH_SIZE` | `200` | Titles per API call. ~2.5k input / ~4k output tokens per call. |
| `CATEGORIZE_LIMIT` | `8000` | Distinct titles one run may classify. A cost ceiling, not a context bound — the run batches. |
| `CATEGORIZE_MAX_OUTPUT_TOKENS` | `0` = derive | Output ceiling per call; derived as `512 + 48×batch` so a batch-size change cannot become a silent truncation. **A ceiling, not a reservation.** |
| `CATEGORIZE_MAX_TOKENS` | `0` = off | Run-level abort budget, mirroring `MATCHER_MAX_TOKENS`. A safety cap against a bug, not a cost target. |

**Cost is not a design constraint here and the numbers say why.** At Haiku 4.5 list rates
($1/MTok in, $5/MTok out) a title is ~10 input tokens and its answer ~18 output tokens, so the
15k-title backlog is on the order of **$1–2 in total** and steady state (~2 400 new titles a
day, drained weekly) is a few tens of cents a week. It is logged anyway, the way
`service.matcher` logs its run — the line carries tokens in / cache_read / out, the call count
and a dollar figure, because an unlogged cost is one nobody notices changing:

```
categorize classify: stored 7841 titles (912 of them recorded declines) over 40/40 batches;
model claude-haiku-4-5 tokens in=104233 cache_read=0 out=151902 in 40 call(s) ~ $0.8637
```

`cache_read=0` is expected, not a regression: Haiku 4.5's minimum cacheable prefix is 4 096
tokens and the system block is a few hundred, so the `cache_control` on it writes no entry and
reports none. It is kept because that minimum is not monotonic across models — the same block
starts caching the moment `CATEGORIZE_MODEL` moves up, and the log keeps the two numbers apart
so the transition is visible rather than inferred.

### What is trusted (nothing)

The model's answer gets **exactly** the validation the file exchange's answers get, because
both call `service.categorize_exchange._validated_rows`. That sharing is the design, not
tidiness: the guarantees below were written against a claude.ai routine, and *a returned value
is untrusted input whoever produced it* — an API response no less than a file off cloud
storage. Two copies would drift, and a wrong category raises nothing; it just files a job in a
stranger's digest.

| The answer says | What happens | Why |
|---|---|---|
| a category not in `taxonomy.CATEGORIES` | dropped, counted, logged | 2026-08-08: 14 135 postings sat in categories no subscriber could select and no query could match, because a third-party string was trusted to be a category. A model's answer is a third-party string too. |
| an index that was not in this batch | dropped | indices are **local to the batch**, so a model cannot answer for a title it was not shown — nor reach one from another batch |
| a malformed record | skipped | one bad row must not cost the other 199 titles in the call |
| `"uncategorised"` | **stored** | "asked, no answer" has to be remembered, or the same unreadable titles are re-asked every run for ever |
| nothing at all for a title | logged, asked again next run | an unanswered title is not a declined one |
| the same title twice | one row, last answer wins | rows are keyed on the title key, so a repetitive model cannot produce two conflicting writes |

**A batch is the unit of failure, not the run.** Each batch is stored as soon as it validates;
an exception inside one — a transient API error, a truncated reply, a DB hiccup on the write —
is logged and skipped. A partial answer means the residue shrinks a bit less, which is
strictly better than not at all, and nobody is waiting on this. `test_categorize_metered.py`
pins that, and every guard in it was mutation-checked red.

**Payload discipline:** the request body carries **titles and integer indices only** — no
company, no URL, no posting id, no subscriber, no email. These are public job titles, so this
is discipline rather than necessity, and it is what makes this the cheapest boundary in the
repo to defend. Asserted against the serialised request body, not against the code that builds
it.

### VPS setup (one-time cutover)

```bash
# 1. The key is already there if the matcher is running (deploy/.env, security rule 2 —
#    generate/paste it on the box, never through a transcript). Nothing new to add.

# 2. Ship the code (deploy/deploy.sh), then swap the schedule. deploy.sh copies units by
#    glob but never enables or disables them, so both halves are by hand:
cd /opt/jobdigest
sudo systemctl enable --now jobdigest-categorize.timer
sudo systemctl disable --now jobdigest-categorize-export.timer \
                             jobdigest-categorize-import.timer

# 3. Delete the weekly claude.ai routine trigger (personal account) so nothing writes a
#    stale categories.json into the Drive folder for a future operator to import by hand.
```

Migration 019 is unchanged and needs no re-application. Run it by hand any time with
`deploy/jobdigest-categorize.sh classify`.

---

## The file exchange (retained, unscheduled)

Everything below describes the free-tier flow that ran from 2026-08-09 to 2026-08-17. The code
(`export_uncategorised` / `import_categories`) and the units
(`jobdigest-categorize-export.*`, `jobdigest-categorize-import.*`) are all still in the tree —
they are the fallback if the key ever goes away, and `import_categories` is where the shared
validation above was first written. **Do not enable both schedules**: they write the same
table, and two paths answering one residue would read as a routine that had stopped rather
than as an error.

```
                titles.json                               categories.json
 credentialed  ──────────────▶   claude.ai routine   ──────────────▶  credentialed
 job (has DB)  (export step)     (reads → answers)      (import step)   job (has DB)
```

- **Export** (needs DB): `python -m service.categorize_exchange export titles.json`
- **Routine** (no DB, no key): reads `titles.json`, writes `categories.json`
- **Import** (needs DB): `python -m service.categorize_exchange import categories.json --asked titles.json`

## Why weekly, and why nowhere near the digest

**This survived the move to the metered key unchanged, and it is the one design decision here
that is not about billing.** The daily pipeline runs at 03:00 (ingest → AI match → send) and
is what real subscribers are waiting on; work added to that window does not make a digest
late, it makes it *miss*, silently, for every subscriber (`CLAUDE.md`). This job has no such
deadline: the residue shrinks a week later instead of a day later, and nobody is waiting on
it. So it runs Sunday 09:30 UTC — clear of the 08:00 source watchdog, the 08:30 categorisation
watchdog and the 09:00 digest watchdog — and a total failure costs a slower-shrinking residue
and nothing else.

Steady state is ~2 400 genuinely new titles a day ≈ 50k tokens a week. The one-off residue
was ~33k distinct titles ≈ 340k tokens, which the default `--limit 8000` drains over the
first few runs, commonest-titles-first.

## titles.json (input to the routine)

```json
{
  "generated_at": "2026-08-09T…Z",
  "instructions": "…embedded classifier instructions…",
  "categories": ["healthcare", "education", …, "uncategorised"],
  "titles": [
    {"i": 0, "title": "Ställare inom formsprutning"},
    {"i": 1, "title": "Undersköterskor till hemtjänsten"}
  ]
}
```

**This file carries no personal data at all** — public job titles and integer indices, no
company, no URL, no posting id, no subscriber, no email. That is asserted by
`test_categorize_exchange.py` against the serialised payload, not against the code that
builds it. It makes this exchange much cheaper to defend than the matcher's, whose file
carries subscriber preferences and CV summaries.

## categories.json (output the routine writes — the import step's ONLY input)

```json
{"categories": [{"i": 0, "category": "manufacturing_production"},
                {"i": 1, "category": "healthcare"},
                {"i": 2, "category": "uncategorised"}]}
```

A bare list is accepted too. **Answers are by index, and the index is checked against
`titles.json`** — which is why the import takes `--asked` and refuses to run without it. The
file cannot introduce a title, and it cannot introduce a category:

| The file says | What happens | Why |
|---|---|---|
| a category not in `taxonomy.CATEGORIES` | dropped, counted, logged | 2026-08-08: 14 135 postings sat in categories no subscriber could select and no query could match, because a third-party string was trusted to be a category. A model's answer is a third-party string too. |
| an index that was never exported | dropped | the file cannot invent a job to classify |
| a malformed record | skipped | one bad row must not cost the whole run |
| `"uncategorised"` | **stored** | "asked, no answer" has to be remembered, or the export asks about the same unreadable titles forever |
| nothing at all for an exported title | logged, asked again next run | an unanswered title is not a declined one |

## Routine prompt (paste into the claude.ai routine)

> Weekly. In my Google Drive folder `JobDigest/categorize`, read `titles.json`.
>
> It contains `instructions`, a `categories` list, and a `titles` list where each entry has
> an integer `i` and a job `title`. Follow the embedded `instructions`. For every `i`, choose
> the single best category **from the `categories` list only** — never invent one, never
> reword one, and copy it exactly as spelled there.
>
> The titles are multilingual (Swedish, Czech, Slovak, Norwegian, German, English). Translate
> as needed and read the head noun of compound words.
>
> If no category genuinely fits — the title names no profession ("Extrajobb", "Är du den vi
> söker?", a bare company or city name) — answer `"uncategorised"`. That is a real answer and
> the right one: a wrong category puts a job in a stranger's digest, while `uncategorised`
> only leaves it where it already is.
>
> Write the result to `JobDigest/categorize/categories.json` as:
> `{"categories": [{"i": 0, "category": "sales"}, ...]}`
> — JSON only, no prose, one entry per index, and answer for every index in the file.

## VPS setup for the exchange (historical — do not run alongside the metered timer)

Assumes rclone is already configured for the matcher (`gdrive:JobDigest`); this uses the
`categorize/` subfolder of the same private, **unshared** folder. The routine reaches it
through the owner's own Drive connector — sharing the folder is the one action that would
create third-party access, and it is not needed.

1. **Apply the migration first** (the deploy runs no migrations):
   ```bash
   cd /opt/jobdigest/deploy
   sudo docker compose exec -T db psql -U jobmatch -d jobmatch -v ON_ERROR_STOP=1 \
     < ../service/db/migration_019_title_categories.sql
   ```
2. **Ship the code** (`deploy/deploy.sh`), which copies the units by glob and rebuilds the
   images. Then enable the timers — `deploy.sh` copies units but does not enable new ones:
   ```bash
   cd /opt/jobdigest
   chmod +x deploy/jobdigest-categorize.sh
   sudo systemctl daemon-reload
   # STOP — this is the RETIRED free-tier path (see this section's heading). Running it while
   # jobdigest-categorize.timer is enabled puts two jobs in the same Sunday 09:30 slot, which
   # CLAUDE.md says must never happen. The live cutover is at the top of this file:
   #   enable jobdigest-categorize.timer, disable these two.
   # Only run the line below if you are deliberately reverting to the Drive exchange, and
   # disable the metered timer first.
   sudo systemctl enable --now jobdigest-categorize-export.timer \
                              jobdigest-categorize-import.timer
   ```
3. **Schedule the claude.ai routine** weekly, Sunday afternoon UTC — any time between the
   Sunday 09:30 export and the Monday 09:30 import. It needs the Drive connector and nothing
   else: no DB, no API key.

### Weekly timeline (UTC)

**What runs now:**

| When | Where | What |
|---|---|---|
| Sun 09:30 | VPS (`jobdigest-categorize`) | uncategorised titles → Claude (metered) → validate → `title_categories` |
| Mon 03:00 | VPS (`jobdigest-pipeline`) | the corpus picks the answers up; `categorization_daily` records it Monday 08:30 |

Run it by hand any time: `deploy/jobdigest-categorize.sh classify`.

**The retired exchange, for the record:**

| When | Where | What |
|---|---|---|
| Sun 09:30 | VPS (export) | uncategorised titles → `titles.json` → Drive |
| Sun ~12:00 | claude.ai routine | read titles → write `categories.json` → Drive |
| Mon 09:30 | VPS (import) | pull answers → validate → `title_categories` |
| Tue 03:00 | VPS (ingest) | the corpus picks the answers up; `categorization_daily` records it |

Run a phase by hand any time: `deploy/jobdigest-categorize.sh export` / `import`.

## Checking it is working

**The run's own log line is the receipt** (count, declines, batches covered, tokens, cost —
see "The knobs" above). Read it for two things beyond the total: `over N/N batches` — a
denominator larger than the numerator means batches were skipped and the log above says which
and why; and the dollar figure, which at ~$1 for a full backlog drain and cents for a steady
week is the cheapest tripwire available on the batch size and model knobs.

**But the number that actually matters is one the *ingest* logs**, and `PLAN.md` item 4 sets
the test:

```
Title cache: 2287/2401 of the pattern residue had a memoised answer (95.3%); 31204 titles cached in total
```

**If that hit rate settles below ~95%, the key is wrong** — not the model. The cache is keyed
on `categorize_exchange.normalise_title`, and a key too specific (an unnormalised title, or
one that keeps the employer) still classifies plenty of rows while missing most of what it
was asked about. The ratio is the only thing that shows the difference; the raw count looks
healthy either way. **This test is unchanged by the move to the metered key**, and it is the
one that would have caught the routine going quiet: 250 cached titles against a 15 139-title
residue is a hit rate near zero, and no other check looks at it.

`title_categories.source` records which path answered a row — `api` for the metered classifier,
`routine` for the file exchange, `manual` for a hand-written correction. That is a new *value*
in a column that already existed, not a new field, and it is the first thing to check when a
category looks wrong.
