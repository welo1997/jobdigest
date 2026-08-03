# Running JobDigest on the laptop

So a change does not have to reach the VPS to be seen. Three processes, no image rebuilds in
the loop:

```
postgres   docker, host :5433   service/db/docker-compose.yml (schema.sql on first boot)
api        uvicorn --reload     service.webapp:app on localhost:8811
web        next dev             localhost:3000, NEXT_PUBLIC_API_URL=http://localhost:8811
```

Nothing here talks to production, and nothing here can: `deploy/.deploy-target` is not read,
`dev\seed.py` refuses a `DATABASE_URL` that does not name localhost, and `MAIL_BACKEND=file`
means no mail leaves the machine.

## First run

```powershell
.\dev\db.ps1 reset          # destroys the local volume, re-applies schema.sql
. .\dev\env.ps1
python dev\seed.py          # 40 synthetic postings, a confirmed subscriber, matches
```

Then in two more terminals:

```powershell
.\dev\api.ps1               # http://localhost:8811   (interactive docs at /docs)
.\dev\web.ps1               # http://localhost:3000
```

`seed.py` finishes by printing the `/matches`, `/preferences` and `/hidden` URLs — there is no
email to click, so those printed links are how you get in. The token in them is a login; it is
traded for a session cookie on first load and disappears from the address bar.

Day to day it is just `.\dev\db.ps1 up` plus the two servers. `reset` is only for a schema
change (see below).

## The files

| | |
|---|---|
| `env.ps1` | every environment value, set only if unset. Dot-source it before `seed.py`. |
| `db.ps1` | `up` · `down` · `reset` · `status` · `psql` · `logs` |
| `api.ps1` | uvicorn on `service.webapp:app` (**not** `service.api` — see below) |
| `web.ps1` | `next dev` with `NEXT_PUBLIC_API_URL` pointed at the local API |
| `seed.py` | synthetic postings + a confirmed subscriber + matches, idempotent |

`seed.py` is deliberately **not** under `service/`: the Dockerfile does `COPY service/`, and a
script whose job is to create a confirmed subscriber with a live `manage_token` must not be
able to ship in the production image.

## Things that will cost you an hour if you don't know them

**`dev\db.ps1 reset` is the only migration path.** `schema.sql` is mounted into
`/docker-entrypoint-initdb.d/` and Postgres runs it **only on an empty data directory**. A
volume created before migration 012/013/014 keeps the old schema for ever, and what you see is
a 500 from a column that isn't there — an application bug, seemingly. `dev\db.ps1 status`
prints whether those columns exist, and `seed.py` refuses to run without them.

**Ports collide with the other local projects on this machine.** `price-dev-db` is also bound
to 5433 and `metabase` to 3000. A collision is a loud `docker compose up` failure — except for
`next dev`, which silently moves to 3001 and gives you a CORS error in the console instead of a
port message. `CORS_ORIGINS` covers 3001 for that reason. To move a port, set `JD_DB_PORT` /
`JD_API_PORT` / `JD_WEB_PORT` before dot-sourcing `env.ps1`; everything else is derived.

**Both servers are on `localhost`, and that is load-bearing.** The session cookie is
`SameSite=Lax`, so `localhost:3000` → `127.0.0.1:8811` would be *cross-site* and the browser
would refuse to send it; `localhost:3000` → `localhost:8811` is same-site, because ports are
not part of a site. If you ever find yourself wanting to loosen `samesite` in
`service/webapp.py` to make dev work, that is the bug — the dev environment must not be the
reason a production security property changes.

**`service/README.md`'s "Run locally" block is the ancestor of this, and it starts
`service.api:app` on 8099.** That is the pre-v1 internal API: no auth, not deployed. The live
webapp is `service.webapp`.

**`next dev` is not the artefact that ships.** Production is `output: "export"` — static files
behind Caddy, same-origin `/api`, with the compatibility redirects and the CSP header. Two
checks before deploying anything that only ever ran under `next dev`:

```powershell
cd web; npm run build      # export-time breakage (two root layouts, generateStaticParams)
# and the real prod-shaped stack on plain http at :8085 (needs deploy/.env):
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.smoke.yml up -d db api web
```

**Turnstile and Google sign-in need nothing.** Both are dormant by absence — no widget without
`NEXT_PUBLIC_TURNSTILE_SITE_KEY`, no verification without `TURNSTILE_SECRET`, and the OAuth
endpoints 404 without `GOOGLE_CLIENT_ID`. Signup works end to end with all of them unset.

## Mail

`MAIL_BACKEND=file` writes each message to `service/outbox/` as `.html` + `.txt`
(gitignored). That is where confirm links, magic links and digests go. A confirm mail is a
real end-to-end test of the language setting: sign up under `/cs/` and the outbox file is in
Czech, with links back to the Czech site.

Note that `/subscribe` validates addresses with `email-validator`, which **refuses reserved
TLDs** — `you@something.test` is rejected before it reaches the database. Use `example.com`
addresses, as `seed.py` does.

## What the seed gives you

40 postings across all nine role categories and ten countries, spread over `work_mode`
(remote / hybrid / on-site / unknown) and `education_min` — with the great majority of both
left **null**, because unknown is the common case in production and is exactly what the SQL
gates get wrong: `x = any(...)` on a NULL column is NULL, and `WHERE` discards NULL the same
way it discards false. A few rows are deliberate near-misses (a bare "Hybrid." that stays
unknown, because only a *named* schedule classifies) so the rules stay visible.

One confirmed subscriber (`dev@example.com`, no preference on work setup or education) with
ten visible matches spanning both thresholds — some ≥ `EMAIL_MIN_SCORE` (6) so they would be
emailed, some 4–5 that are page-only — and two hidden ones, so `/hidden` is not an empty page
the first time it opens.

Re-running is safe: postings upsert by id and the subscriber is looked up by address. It does
re-apply the two hidden rows, so anything you unhid by hand comes back hidden.

Options: `--email` for a second subscriber, `--language cs|de|sk|pl|es|fr|it` to see the site
and the emails in another language.

Want real inventory instead? `python -m service.ingest --cz` ingests genuinely (network, ~15
min for a full international run). **Never copy the production database down** — it holds real
addresses and `manage_token`s, each of which is full control of a subscription.

## The AI matcher is not in this loop

The live matcher is a claude.ai routine, and `service/matcher.py --match` needs an API key this
box does not have. The seeded scores stand in for it. To exercise the file-exchange path
locally without a key:

```powershell
python -m service.matcher --export     # writes exchange/shortlists.json
python -m service.matcher --import     # reads exchange/picks.json, validates, writes matches
```

## Tests

```powershell
. .\dev\env.ps1                        # <- the SQL suites read DATABASE_URL from here
$env:TEST_DATABASE_URL = $env:DATABASE_URL
python -m pytest                       # 601 tests: 564 + the 37 SQL-backed cases
```

Each PowerShell session starts clean, so forgetting the dot-source leaves
`TEST_DATABASE_URL` empty and the run reports **"564 passed, 37 skipped"** — green, with the
most expensive tests silently dropped. Check the skip count, not the colour.

The SQL-backed tests skip themselves without `TEST_DATABASE_URL`. They will happily run
against the dev database — they create and clean up their own rows — but the honest way to
read a green run is against a throwaway one, which is what CI does.
