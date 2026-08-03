# Local development environment for JobDigest - the one place the values live.
#
#   . dev\env.ps1        (dot-source it; dev\api.ps1 and dev\web.ps1 do this for you)
#
# Every variable here is set ONLY IF UNSET, so anything already exported in the shell wins -
# that is how you move a port when one of the other local projects on this machine is holding
# it (price-dev-db is also bound to 5433, metabase to 3000).
#
# Nothing in this file may ever name the VPS. `dev\seed.py` refuses a non-local DATABASE_URL,
# but the first line of defence is that no production value is written down here at all.

function Use-Default([string]$Name, [string]$Value) {
    if (-not [Environment]::GetEnvironmentVariable($Name)) {
        [Environment]::SetEnvironmentVariable($Name, $Value)
    }
}

Use-Default JD_DB_PORT  "5433"
Use-Default JD_API_PORT "8811"
Use-Default JD_WEB_PORT "3000"

Use-Default DATABASE_URL "postgresql://jobmatch:jobmatch@127.0.0.1:$($env:JD_DB_PORT)/jobmatch"

# Nothing is ever sent locally: the file backend writes the .html + .txt to service/outbox/,
# which is gitignored. Confirm links, magic links and digests are all read from there.
Use-Default MAIL_BACKEND "file"

# `localhost` on BOTH sides, deliberately. The session cookie is SameSite=Lax, and
# localhost:3000 -> 127.0.0.1:8811 are different hosts, therefore cross-site, so the browser
# would refuse to send it and every /matches XHR would fall back to the token path. Same host
# name, different port, is same-site - ports are not part of a site. Do not "fix" a cookie
# problem here by loosening samesite in service/webapp.py: that is a production security
# property and the dev environment must not be the reason it changes.
Use-Default SITE_URL       "http://localhost:$($env:JD_WEB_PORT)"
Use-Default API_PUBLIC_URL "http://localhost:$($env:JD_API_PORT)"

# Plain http locally, so the Secure flag would make the cookie unstorable. Documented escape
# hatch in webapp.py; never set this on the box.
Use-Default SESSION_COOKIE_SECURE "0"

# `next dev` silently increments to 3001 when 3000 is taken, and the only symptom is a CORS
# error in the browser console - never a port message. Both are allowed so that costs nothing.
Use-Default CORS_ORIGINS ("http://localhost:$($env:JD_WEB_PORT),http://127.0.0.1:$($env:JD_WEB_PORT)," +
                          "http://localhost:3001,http://127.0.0.1:3001")

# The Windows console is cp1250 here and the copy is Czech, Polish and German. Without this a
# log line with a diacritic raises UnicodeEncodeError inside a request handler.
Use-Default PYTHONIOENCODING "utf-8"

# Baked into the browser bundle by `next dev`. Must match API_PUBLIC_URL's host for the reason
# above.
Use-Default NEXT_PUBLIC_API_URL "http://localhost:$($env:JD_API_PORT)"

# Turnstile and Google sign-in are dormant by absence - Turnstile.tsx renders no widget without
# NEXT_PUBLIC_TURNSTILE_SITE_KEY, webapp.py skips verification without TURNSTILE_SECRET, and
# _google_configured() 404s the OAuth endpoints. Signup works end to end with all of them unset,
# so they are deliberately not listed here.
