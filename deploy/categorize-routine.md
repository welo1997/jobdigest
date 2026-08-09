# JobDigest — the weekly title-classification routine (no-API, free tier)

The same shape as `matcher-routine.md`, for a different question. The matcher decides *who
sees which job*; this decides *what a job is* — the `role_category` for titles that neither
`service/taxonomy.py`'s patterns nor the publishers' occupation codes could read.

```
                titles.json                               categories.json
 credentialed  ──────────────▶   claude.ai routine   ──────────────▶  credentialed
 job (has DB)  (export step)     (reads → answers)      (import step)   job (has DB)
```

- **Export** (needs DB): `python -m service.categorize_exchange export titles.json`
- **Routine** (no DB, no key): reads `titles.json`, writes `categories.json`
- **Import** (needs DB): `python -m service.categorize_exchange import categories.json --asked titles.json`

Nothing reads `title_categories` until the next ingest: `upsert_postings` rewrites
`role_category` on conflict and every active posting is re-seen daily, so answers imported on
Monday reach the corpus at Tuesday 03:00 and show up in `categorization_daily` on Tuesday
morning. **There is deliberately no backfill step** — the same reason classifier changes need
none.

## Why weekly, and why nowhere near the digest

The daily window runs 03:00 → ~06:00 (routine) → 07:00 (send), and its real deadline is the
matcher routine, not the import. Work added to that window does not make a digest late, it
makes it *miss*, silently, for every subscriber (`CLAUDE.md`). This job has no such deadline:
the residue shrinks a week later instead of a day later, and nobody is waiting on it. So it
runs Sunday 09:30 / Monday 09:30 UTC, and a total failure costs a slower-shrinking residue
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

## VPS setup (one-time)

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
   sudo systemctl enable --now jobdigest-categorize-export.timer \
                              jobdigest-categorize-import.timer
   ```
3. **Schedule the claude.ai routine** weekly, Sunday afternoon UTC — any time between the
   Sunday 09:30 export and the Monday 09:30 import. It needs the Drive connector and nothing
   else: no DB, no API key.

### Weekly timeline (UTC)

| When | Where | What |
|---|---|---|
| Sun 09:30 | VPS (export) | uncategorised titles → `titles.json` → Drive |
| Sun ~12:00 | claude.ai routine | read titles → write `categories.json` → Drive |
| Mon 09:30 | VPS (import) | pull answers → validate → `title_categories` |
| Tue 03:00 | VPS (ingest) | the corpus picks the answers up; `categorization_daily` records it |

Run a phase by hand any time: `deploy/jobdigest-categorize.sh export` / `import`.

## Checking it is working

The ingest logs the number that matters, and `PLAN.md` item 4 sets the test:

```
Title cache: 2287/2401 of the pattern residue had a memoised answer (95.3%); 31204 titles cached in total
```

**If that hit rate settles below ~95%, the key is wrong** — not the model. The cache is keyed
on `categorize_exchange.normalise_title`, and a key too specific (an unnormalised title, or
one that keeps the employer) still classifies plenty of rows while missing most of what it
was asked about. The ratio is the only thing that shows the difference; the raw count looks
healthy either way.
