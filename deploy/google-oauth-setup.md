# Enabling "Sign in with Google"

Google sign-in is **login for existing subscribers**: Google confirms a user's email, we match
it to a live subscription and mint the same session a magic link would. It stores nothing extra
from Google (not even the Google account id). The feature is **dormant** until you do the steps
below — with no credentials the backend endpoints 404 and the button is hidden, so shipping the
code changed nothing user-visible.

## 1. Create an OAuth client in Google Cloud

1. <https://console.cloud.google.com/> → create (or pick) a project.
2. **APIs & Services → OAuth consent screen**:
   - User type **External**; publish it (Testing mode only lets allow-listed accounts in).
   - Scopes: just `.../auth/userinfo.email` and `openid` — nothing else. We only want a
     verified email. Adding more scopes triggers Google's app-verification review; don't.
   - App name "JobDigest", support email, your domain, privacy-policy URL
     `https://jobdigest.eu/privacy`.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Web application**.
   - **Authorized JavaScript origins:** `https://jobdigest.eu`
   - **Authorized redirect URIs:** `https://jobdigest.eu/api/auth/google/callback`
     (exactly — Caddy serves the API under `/api`; this must match byte-for-byte or Google
     rejects the callback with `redirect_uri_mismatch`).
   - Save. Copy the **Client ID** and **Client secret**.

## 2. Put the credentials on the box (never in git, never in a transcript)

The client secret is a real secret — type it directly into `deploy/.env` **on the VPS**, don't
paste it into chat or commit it. `.env` is gitignored and loaded by the `api` container via
`env_file`.

```bash
ssh deploy@<VPS>
cd /opt/jobdigest/deploy
# append to .env — the client id is public, the secret is not
cat >> .env <<'EOF'
GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-client-secret>
NEXT_PUBLIC_GOOGLE_AUTH=1
EOF
```

- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` → the API reads these; their presence is what
  makes `/auth/google/*` live (`_google_configured()`).
- `NEXT_PUBLIC_GOOGLE_AUTH=1` → a **build-time** flag that makes the web image render the
  button. It's baked at `docker build`, so the web image must be rebuilt after setting it.
- `GOOGLE_REDIRECT_URI` is **not** needed — it defaults to `API_PUBLIC_URL/auth/google/callback`
  = `https://jobdigest.eu/api/auth/google/callback`. Only set it if that default is ever wrong.

## 3. Rebuild + restart

The redirect URI and client id aren't secret, but the button flag is build-time, so rebuild web
and restart the api:

```bash
cd /opt/jobdigest/deploy
sudo docker compose build web            # bakes NEXT_PUBLIC_GOOGLE_AUTH into the static site
sudo docker compose up -d api web        # api picks up the new .env; web serves the button
```

(Or just re-run `deploy/deploy.sh` from a workstation, which rebuilds both.)

## 4. Verify

- `https://jobdigest.eu/manage` now shows **"Sign in with Google"** above the email form.
- Sign in with a Google account whose email **is** a live subscription → lands on
  `/preferences`, logged in (session cookie set).
- Sign in with a Google account that is **not** subscribed → bounces to
  `/manage?google=nosub` with a "not subscribed yet" note. No session is created.
- A cancelled/failed consent → `/manage?google=error`.

## Turning it off

Remove the three `.env` lines (or set `NEXT_PUBLIC_GOOGLE_AUTH=` empty), rebuild web, restart.
The endpoints go back to 404 and the button disappears — no data migration, nothing stored to
clean up.

## What this does NOT do (yet)

Google **signup** for brand-new users is deliberately out of scope: creating a subscription
needs the preference wizard and the double-opt-in consent step, so a non-subscriber is sent to
sign up normally. Adding Google-verified signup (skipping the confirm email) is a separate,
larger change to the signup flow.
