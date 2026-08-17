# JobDigest — the daily matching routine (no-API, free tier)

> **DECOMMISSIONED 2026-08-17.** Matching now runs **on the VPS with a metered
> `ANTHROPIC_API_KEY`** — `service.matcher.run()` reranks the due subscribers inside the daily
> `jobdigest-pipeline` unit (`python -m service.pipeline --ingest --cz --match`). There is no
> Drive exchange, no claude.ai routine, and no ~06:00 deadline. Using a personal Claude/Codex
> subscription as a headless backend was ruled out on terms (Anthropic Consumer Terms + OpenAI
> ToS both forbid commercial + automated use of the subscription). This file is kept as the
> record of how the free-tier routine worked; the `export_shortlists` / `import_picks` code in
> `matcher.py` stays for debugging but is no longer scheduled. **To operate the live system read
> `deploy/jobdigest-pipeline.service` and the "Metered API path (live)" section at the bottom of
> this file — everything between here and there describes the retired flow.**

Free-tier matching ran inside a **claude.ai cloud routine** so it used Claude subscription
compute, not metered API credits. The routine sandbox has only a git checkout + Google
connectors — **no DB access, no API key** — so it works purely on two JSON files:

```
                shortlists.json                              picks.json
 credentialed  ───────────────▶   claude.ai routine   ───────────────▶  credentialed
 job (has DB)   (export step)      (reads → picks)       (import step)     job (has DB)
```

- **Export** (needs DB): `python -m service.matcher --export shortlists.json`
- **Routine** (no DB, no key): reads `shortlists.json`, writes `picks.json`
- **Import** (needs DB): `python -m service.matcher --import picks.json`
- **Send** (needs DB): `python -m service.pipeline`   (reads picks from `matches`, emails)

The credentialed steps run wherever the DB is reachable (VPS cron, or GitHub Actions if the
DB is Supabase). The routine only ever touches the two files.

## shortlists.json (input to the routine)

```json
{
  "generated_at": "2026-07-19T…Z",
  "instructions": "…embedded matcher instructions…",
  "profiles": [
    {
      "profile_id": "uuid",
      "profile": {"label": "...", "role_categories": ["product"], "stack": ["figma","sql"],
                  "seniorities": ["mid"], "work_types": ["permanent"],
                  "part_time_only": true,
                  "locations": "Czechia (Prague, Brno only); plus fully remote roles anywhere in the EU",
                  "countries": ["CZ"], "cities": ["cz:prague","cz:brno"], "remote_scope": "eu",
                  "regions": ["cz","eu"],
                  "work_modes": ["hybrid","remote"],
                  "work_setup": "hybrid or fully remote roles only",
                  "education_levels": ["secondary","vocational","bachelor"],
                  "education": "only roles requiring High school, Vocational / apprenticeship or Bachelor's",
                  "education_field": "Economics",
                  "sectors": ["ecommerce"], "years_experience": 3, "cv_summary": "..."},
      "candidates": [
        {"posting_id": "md5…", "title": "...", "company": "...", "location": "...",
         "city": "Brno", "work_mode": "hybrid", "seniority": "lead",
         "work_type": "permanent", "remote": true, "reach": "in-your-country",
         "part_time": true,
         "education_min": "bachelor",
         "salary": "45 000 – 90 000 Kč", "description": "…≤320 chars…"}
      ]
    }
  ]
}
```

**That candidate is the maximal shape — a posting that stated everything.** Since 2026-08-12
a candidate carries **only the fields its posting actually stated**: a field the posting never
gave is *absent*, not null or `false` or `"?"`. Most candidates are therefore much smaller than
the example. `education_min` is absent on 90.6% of active postings, `salary` on ~65%,
`work_mode` on most, and `remote` / `part_time` appear only when true. `reach` follows the same
rule and is the one field that is *derived against the subscriber* rather than stated by the
posting — absent means we could not establish which countries the job may be held from, never
that it may be held from any. That removed
~100-125 bytes per candidate — 117 candidates per subscriber per day — but the reason it is
*better* is that null used to mean three different things (unread, never-stated, not-claimed)
taught field by field, and absence means one thing everywhere, including on fields nobody has
added yet. **The prompt below teaches that rule; the two are one change.**

## picks.json (output the routine writes — the import step's ONLY input)

```json
{
  "picks": [
    {"profile_id": "uuid",
     "jobs": [{"posting_id": "md5…", "score": 9, "reason": "one sentence why it fits"}]}
  ]
}
```

Rules the import step enforces (so the routine can't break the DB, and so a tampered file
can't decide what lands in an inbox): unknown or inactive `posting_id`s are dropped, and
**`profile_id` must be a real subscriber** — picks cannot be attributed to a profile they
weren't generated for. `score` is clamped 0–10, anything below the floor (`MATCH_FLOOR`,
default **4**) is dropped, `reason` is truncated to 280 chars, and a malformed entry is
logged and skipped rather than aborting the run. The email shows only the strong picks
(`digest.EMAIL_MIN_SCORE`, default 6); the 4–5s appear on the subscriber's `/matches`
web page.

## Routine prompt (paste into the claude.ai routine)

**This prompt is a second copy of rules that also live in `matcher.py`
(`SYSTEM` / `ROUTINE_INSTRUCTIONS`), and it is the one the model actually follows.** Changing
a matching rule in Python and not here changes nothing in production — the routine is the
live matcher. Update all three together, then push the routine with `RemoteTrigger update`
(id `trig_01NxgGUCCEaSDhFagRKPRV9x`); the paste-by-hand step below is the fallback. This
drifted once already: the part-time and `unstated`-seniority rules shipped in Python on
2026-07-26 while the routine still ran the old text.

**The routine lives on the owner's personal claude.ai account, not the work one.** Verified
2026-08-12: from a CLI authenticated to the work account, `RemoteTrigger get` on the id above
returns 404, and `list` shows ten triggers, all of them Footshop jobs — no JobDigest matcher
anywhere. That is consistent with the go-live note (2026-07-19): the routine was created on
the account that owns the Drive connector (`owner@example.com`), and CLAUDE.md's
"owner's own connector" rule means it must stay there. So a `RemoteTrigger` 404 on this id
does **not** mean the routine is gone or the id is stale — check which account the CLI is
holding before concluding anything. Pushing an update requires a session authenticated to
the personal account, or the paste-by-hand fallback below. **Never create a new routine from
the work account as a workaround**: two matchers racing over one `picks.json` would look like
partial or alternating picks, not an error — `import_picks` validates content, not authorship.

The live routine carries **one clause this file does not**, deliberately: a transitional
fallback saying that a profile with no `locations` field came from an older export and should
be judged on `regions` as before. It was pushed on 2026-07-27, before the VPS had the code
that emits `locations`/`city`/`remote`, so without it the run in between would have applied a
location rule to fields that were not there yet. **Once the export has been deployed and one
run has succeeded with the new fields, that paragraph can be dropped** — the Python side
always emits them, which is why `ROUTINE_INSTRUCTIONS` has no such clause.

> You are JobDigest's daily job matcher. Read `shortlists.json`. For EACH subscriber, read
> their `profile` and their `candidates`, and pick the postings that genuinely fit that
> specific person — weigh the whole context (role type, seniority, skills/stack, work setup,
> location, sector interest), not just keyword overlap.
>
> **A candidate carries only the fields its posting actually stated, so a missing field means
> we do not know — never that the answer is no.** Where a field you need is missing, read the
> `description` if there is one and judge it yourself; never exclude a posting for being
> silent.
>
> Treat **seniority as a hard filter**. The six levels are `intern` (a placement —
> internship/Praktikum/Werkstudent), `entry_level` (a first permanent job — graduate
> scheme/absolvent/trainee), `junior`, `mid`, `senior` (senior individual contributor) and
> `lead` (people leadership — lead/head/director/VP/C-level). Exclude any posting whose level
> clearly differs from the subscriber's target level(s) — a lead or senior role for a
> junior-only subscriber, an internship for anyone who did not ask for one, a graduate role
> for a senior-only subscriber — even if the role, skills and location fit perfectly (omit it,
> or score it below 4). **Senior and lead are different kinds of job, not adjacent rungs**: a
> "Head of" role is not what someone targeting senior asked for. **A candidate with no
> `seniority` field never named a level at all — that is NOT a mismatch, judge it on overall
> fit.** 70% of postings name none; treating them as exclusions would empty the digest.
>
> Treat **location as a hard filter for anything that is not fully remote**. The profile's
> `locations` line names the countries and, where given, the exact cities the subscriber can
> work in (`countries` / `cities` / `remote_scope` say the same thing in structured form; a
> city is written `"cz:prague"`). A posting that requires being anywhere else — another city,
> or a country they did not pick — is not a fit however well the role matches: omit it, or
> score it below 4. Being emailed an on-site job in Brno when you live in Prague is the
> failure this rule exists to prevent. **Candidates carrying `"remote": true` are exempt from
> the *city* test only, not from `reach` below** — `"hybrid"` is not remote, it means being in
> that city most weeks, and a candidate
> with no `remote` field is not exempt. A candidate with **no `city` field** did not resolve to
> a city we recognise: the prefilter deliberately lets those through, so read its `location`
> text and judge it yourself rather than assuming it was checked. Naming no city for a country
> means any city in that country.
>
> **`reach` — "remote" is two questions.** Whether there is an office is one; which countries
> you may *live in* while holding the job is another, and in this inventory "remote"
> overwhelmingly means work-from-home inside one single country. `"reach": "anywhere"` — they
> may live anywhere, location satisfied outright. `"reach": "in-your-country"` — the role's
> remote area covers a country the subscriber picked, so it is genuinely open to them.
> `"reach": "other-country-only"` — remote, but only from a country they did **not** pick: not a
> fit however perfectly the role, stack and seniority match (omit it, or score it below 4).
> **No `reach` field** means we could not establish the breadth — read the posting's own words
> and judge it, neither assuming it is open to them nor refusing it for being silent. And a
> `locations` line offering "fully remote roles worldwide" is a willingness to work for an
> employer in any country, **not** permission to relocate or to live anywhere: they still live
> in the countries named, so this rule applies unchanged.
>
> Emailing someone in Prague a "100% remote" job that means work-from-home inside Poland is the
> twin of the Brno failure above. It happened on 2026-08-17 on the live metered path — five such
> roles in one email, scored 7-8 — because `remote_reach` reached neither the SQL gate nor the
> prompt. See `service/tests/test_reach_gate_sql.py`.
>
> **Work setup**: `"work_modes"` / `"work_setup"` appear on a profile **only when the
> subscriber has ruled some arrangements out** — most have not, and their absence means
> anything goes. When present, a candidate whose `"work_mode"` is not one of the modes they
> accept is not a fit: omit it, or score it below 4. **No `work_mode` field** means the posting
> never stated an arrangement, which is the common case and is deliberately not guessed at by
> the prefilter — read the description and judge it, rather than letting it through because
> the field was missing. Note `"work_mode": "remote"` and `"remote": true` are the same claim
> said twice; `"hybrid"` means part of the week in that city, so the location rule above
> applies to it in full.
>
> **Education**: `"education_levels"` / `"education"` appear on a profile **only when the
> subscriber has ruled some requirement levels out** — most have not, and their absence means
> anything goes. When present, a candidate whose `"education_min"` is above every level they
> accept is not a fit: omit it, or score it below 4.
>
> **No `education_min` field** is by far the most common case and means **the requirement was
> never read**, not that there is none. About 91% of active postings carry none (measured
> 2026-08-08 against 98 858 rows). So where there *is* a
> description, read it and judge; and **never drop a posting merely for being silent** — a
> requirement nobody wrote down is not a requirement. A degree named as "preferred", "nice to
> have" or "or equivalent experience" disqualifies nobody, and the classifier already refuses
> to record those.
>
> `"education_field"` is what the subscriber studied. It is context for judging how well a role
> suits them and is **never** a reason to exclude one — whether a degree is "in a related field"
> is your judgement to make, which is exactly why it is not a filter.
>
> **Work schedule**: if the profile has `"part_time_only": true`, a full-time posting is not
> what they asked for — score it at most 5 (it still appears on their matches page, it just
> must not headline their email) and prefer candidates with `"part_time": true`. Do not drop
> full-time postings entirely; part-time inventory is thin and a subscriber with no picks at
> all gets no digest.
>
> Postings may be in Czech,
> Slovak, or English; judge them equally (a "Vývojář" is a developer, "Obchodní zástupce" a
> sales rep, "Účetní" an accountant). Score each posting 0–10 on overall fit (9–10 excellent,
> 6–7 solid, 4–5 plausible-but-weaker) — be honest, use the full range. Include every posting
> scoring 4+ (skip only clear non-fits); a later step decides which get emailed vs shown on the
> web page. Order best-first; at most 20 per subscriber. Only use `posting_id`s present in the
> file; never invent one. Write the result to `picks.json` in exactly this shape:
> `{"picks":[{"profile_id":"…","jobs":[{"posting_id":"…","score":<int>,"reason":"…"}]}]}`
> Output only the file — no commentary.

## PII note

`shortlists.json` carries no email addresses — a profile is identified only by its opaque
uuid. It does still contain each subscriber's stated preferences and their CV-derived
summary (e.g. "Detected: data engineering, dbt, snowflake · ~4 yrs") — including the cities
they chose, which is a rough indication of where they live — so it is personal
data: **do not commit it to the public repo** (`exchange/` is gitignored). It travels via a
private Google Drive folder.

The folder is owner-only (verified 2026-07-20) — but it is still ordinary Drive storage on a
personal Google account, which has two consequences:

- **Anything in it is readable by any claude.ai session with the Drive connector**, not just
  the matcher routine. That is fine for job listings and an opaque profile id; it is not fine
  for a database dump. Backups therefore live *outside* this folder
  (`gdrive:JobDigest-Backups`) and are encrypted before upload —
  `jobdigest-backup.sh` refuses to run if pointed inside it. Nothing here should ever hold a
  credential, and `manage_token` is a credential.
- **Write access to the folder is write access to inboxes.** Whoever can replace `picks.json`
  proposes which jobs each subscriber is emailed. The import step bounds the damage — both
  ids are validated against the DB, so only real active postings can reach real subscribers
  (see `service/tests/test_matcher_exchange.py`) — but treat the folder's sharing list as a
  security control and keep it empty.

## VPS setup (one-time)

The DB-credentialed phases run on the VPS (the Postgres is compose-internal, not reachable
from GitHub Actions). Two systemd timers bracket the routine; `rclone` moves the JSON.

1. **Install rclone + authorise Google Drive** (interactive, on the VPS as `deploy`):
   ```bash
   sudo apt-get install -y rclone
   rclone config          # n) new remote, name: gdrive, type: drive, follow the OAuth flow
   rclone mkdir gdrive:JobDigest
   ```
   **Leave the folder unshared.** The claude.ai routine reaches it through *your own*
   account's Drive connector, so it needs no sharing at all — and verified 2026-07-20, the
   folder's permission list is owner-only. Do not "share it with the account the routine
   uses": that would be the one action turning a private folder into a third-party-readable
   one, for a file containing every subscriber's preferences and CV summary. If you ever do
   move the routine to a separate Google account, share *only* this folder — never a parent —
   and re-check where backups point (see above).

2. **Install the units + script:**
   ```bash
   cd /opt/jobdigest
   chmod +x deploy/jobdigest-match.sh
   sudo cp deploy/jobdigest-match-*.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now jobdigest-match-export.timer jobdigest-match-import.timer
   # retire the old single send timer (it sent without AI matching):
   sudo systemctl disable --now jobdigest-pipeline.timer 2>/dev/null || true
   ```
   (Requires the redeploy that ships `service/matcher.py`, the `exchange` volume, and
   migration 004 — rebuild the pipeline image: `sudo docker compose build pipeline`.)

3. **Schedule the claude.ai routine** for ~06:00 UTC (between the 05:00 export and 07:00
   import; claude.ai cron minimum is 1h). Point it at the shared Drive folder with the
   prompt above.

### Daily timeline (UTC)

| Time  | Where            | What |
|-------|------------------|------|
| 05:00 | VPS (export)     | ingest + write `shortlists.json` → Drive |
| ~06:00| claude.ai routine| read shortlists → write `picks.json` → Drive |
| 07:00 | VPS (import)     | pull picks → load `matches` → send digests |

Run a phase by hand any time: `deploy/jobdigest-match.sh export` / `import`.

## Metered API path (live)

This is the production path since 2026-08-17. One systemd unit does the whole run on the box.

**One-time cutover on the VPS:**

```bash
# 1. Put the key on the box — directly, never through a chat/commit (security rule 2):
printf 'ANTHROPIC_API_KEY=%s\n' 'sk-ant-...' >> /opt/jobdigest/deploy/.env   # paste your key

# 2. Apply migration 024 (schema.sql only runs on an empty data dir, so add the column by hand):
sudo docker compose exec -T db psql -U jobmatch -d jobmatch \
  -c 'alter table profiles add column if not exists last_ondemand_at timestamptz;'

# 3. Swap the schedule: retire the routine's two-phase timers, run the single daily unit.
sudo systemctl disable --now jobdigest-match-export.timer jobdigest-match-import.timer
sudo cp deploy/jobdigest-pipeline.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now jobdigest-pipeline.timer

# 4. Delete the claude.ai routine trigger (personal account) so nothing writes a stale picks.json.
```

- **Daily run:** `jobdigest-pipeline` at 05:00 UTC → `python -m service.pipeline --ingest --cz
  --match`. `--match` reranks only the **due** subscribers (a weekly subscriber is not billed on
  the five off days). `MATCHER_MODEL` defaults to `claude-haiku-4-5` (~3c/subscriber/day);
  `MATCHER_MAX_TOKENS` (default 0 = off) is a per-run abort budget.
- **On-demand:** `POST /digest/run` (webapp, subscriber-authenticated, POST-only) re-matches one
  subscriber and emails them, bounded by `ONDEMAND_COOLDOWN_MIN` (default 360 = 6h) via
  `store.claim_ondemand_run`. The `api` container therefore also needs `ANTHROPIC_API_KEY`.
- **Raising `MATCHER_MODEL` was a silent no-op until 2026-08-17, and the failure was empty
  digests.** Every model above Haiku 4.5 thinks by default, so its response *opens* with a
  `thinking` block whose text is empty; `match_profile` read `content[0].text`, parsed `""` as
  JSON, logged one warning and returned **zero picks for every subscriber**. Nothing downstream
  can tell that apart from a quiet inventory day — `digest_runs.picks_n` is 0 either way and the
  watchdog needs three days. It now takes the first *text* block and returns `[]` loudly if there
  is none (`test_matcher_metered.py`). The knob is safe to turn; a model *change* still needs the
  measurement below rather than an argument.
- **"Is Haiku as good as the routine was?" is a measurement, and there are two harnesses for it.**
  Neither writes anything — no `matches` row, no `digest_runs` row, no email — and both call the
  real `match_profile`, not a copy of the prompt:
  - `scripts/matcher_gate_eval.py` — a synthetic, adversarial fixture with a **known answer key**,
    so a model is graded rather than compared. It exists because the hard gates are where being
    wrong is expensive and where nothing else checks: **seniority has no SQL gate on the digest
    path at all**, so the model is the whole enforcement, and `seniority=unstated` is 70% of the
    corpus, so over-applying the rule is as bad as under-applying it. No database, no subscriber
    data, a few cents a pass.
  - `scripts/matcher_model_ab.py` — the live version: builds each subscriber's shortlist **once**
    so several models read byte-identical input, then reports hard-rule violations, the strong
    picks the cheaper model missed, and score drift. Run it on the box (the DB is there);
    `--repeat 2` measures each model's agreement with *itself*, which is the noise floor any
    cross-model overlap has to beat — `match_profile` sets no temperature, so a single pass is a
    single sample. Its location audit reads `postings.reach_countries`, because a posting naming
    twelve countries is not an out-of-country pick.
- **Cost lever, not yet built:** the Anthropic Batch API is 50% off and fits the daily window —
  pull it when subscriber count makes token cost binding, the same measured gate as the vector
  shortlist. Synchronous is right at current scale.
