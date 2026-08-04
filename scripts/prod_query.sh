#!/usr/bin/env bash
# Run one read-only SELECT against the production JobDigest database and print it.
#
# Why this exists rather than a raw ssh line
# ------------------------------------------
# Aggregate questions about live inventory ("how many postings per country?") can only be
# answered from production Postgres, and the honest alternative — pasting an ssh+docker+psql
# one-liner each time — has two problems. It is a compound shell command, so no permission
# rule can match its prefix and it falls through to the auto-mode classifier every time; and
# it hands the caller a full psql session on a database holding real subscribers' data.
#
# This wrapper is the bounded version. Three independent limits, in order of how much they
# are worth:
#
#   1. `default_transaction_read_only=on` is set on the *server* side via PGOPTIONS. Postgres
#      itself then refuses INSERT/UPDATE/DELETE/DDL. This is the only guarantee here that is
#      not a string check, and it is the one to keep if the others are ever simplified away.
#   2. `statement_timeout` bounds a careless query on a live box. `postings` is ~50k rows and
#      the API shares this database; a seq scan under a lock is a real outage risk.
#   3. A syntax gate refuses anything that is not a single SELECT/WITH. Defence in depth, and
#      cheap, but do not mistake it for the security boundary — (1) is.
#
# It deliberately cannot: pick a database, run psql meta-commands (\copy, \!), read files
# (pg_read_file), or run more than one statement.
#
# **This reads real personal data.** Subscriber rows live in `profiles`; `postings` and the
# aggregates over it do not. Keep queries aggregate. Nothing here should ever print an email
# address into a transcript — security rule 2.
#
# Usage
# -----
#     bash scripts/prod_query.sh "select country_code, count(*) from postings group by 1"
#     bash scripts/prod_query.sh --csv "select source, count(*) from postings group by 1"

set -euo pipefail

FORMAT=()
if [ "${1:-}" = "--csv" ]; then
    FORMAT=(--csv)
    shift
fi

SQL="${1:-}"
if [ -z "$SQL" ]; then
    echo "usage: prod_query.sh [--csv] \"select ...\"" >&2
    exit 2
fi

# --- syntax gate (defence in depth; the real guarantee is PGOPTIONS below) ----------------

# Strip a single trailing semicolon and surrounding whitespace, then require exactly one
# statement. An embedded ';' is how a second statement would ride along.
TRIMMED="$(printf '%s' "$SQL" | sed -e 's/[[:space:]]*;[[:space:]]*$//' -e 's/^[[:space:]]*//')"
case "$TRIMMED" in
    *\;*)
        echo "refused: more than one statement (embedded ';')" >&2
        exit 3
        ;;
esac

LOWER="$(printf '%s' "$TRIMMED" | tr '[:upper:]' '[:lower:]')"
case "$LOWER" in
    select\ *|select$'\n'*|select$'\t'*|with\ *|with$'\n'*|with$'\t'*) ;;
    *)
        echo "refused: only SELECT / WITH are allowed, got: ${TRIMMED:0:40}..." >&2
        exit 3
        ;;
esac

# psql meta-commands and the file-access functions, none of which a SELECT needs. `\!` would
# be shell execution on the database host; pg_read_file reads the container's filesystem.
if printf '%s' "$LOWER" | grep -Eq '(^|[^a-z_])(pg_read_file|pg_read_binary_file|pg_ls_dir|lo_import|lo_export|pg_sleep|dblink|copy)([^a-z_]|$)|\\\\'; then
    echo "refused: query contains a file-access, sleep or meta-command construct" >&2
    exit 3
fi

# --- target ------------------------------------------------------------------------------

TARGET_FILE="$(dirname "$0")/../deploy/.deploy-target"
if [ ! -f "$TARGET_FILE" ]; then
    echo "no deploy/.deploy-target — see deploy/README.md" >&2
    exit 4
fi
# shellcheck disable=SC1090
. "$TARGET_FILE"

: "${JOBDIGEST_HOST:?JOBDIGEST_HOST not set in deploy/.deploy-target}"

DB_CONTAINER="${JOBDIGEST_DB_CONTAINER:-jobdigest-db-1}"
DB_USER="${JOBDIGEST_DB_USER:-jobmatch}"
DB_NAME="${JOBDIGEST_DB_NAME:-jobmatch}"
TIMEOUT_MS="${JOBDIGEST_QUERY_TIMEOUT_MS:-30000}"

# ssh concatenates its argv into one string and hands it to a **remote shell**, so local
# quoting is gone by the time the command runs there: an unescaped `count(*)` reached bash as
# a syntax error. Everything variable therefore has to be single-quoted for that second
# parse, with embedded single quotes escaped the only way sh allows — close, backslash-quote,
# reopen.
squote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

REMOTE="docker exec -e $(squote "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=${TIMEOUT_MS}")"
REMOTE="$REMOTE $(squote "$DB_CONTAINER") psql -U $(squote "$DB_USER") -d $(squote "$DB_NAME") -v ON_ERROR_STOP=1"
for flag in ${FORMAT[@]+"${FORMAT[@]}"}; do
    REMOTE="$REMOTE $(squote "$flag")"
done
# default_transaction_read_only is the actual boundary: Postgres refuses writes regardless of
# what got past the syntax gate above.
REMOTE="$REMOTE -c $(squote "$TRIMMED")"

ssh -o BatchMode=yes -i ~/.ssh/id_ed25519_personal "$JOBDIGEST_HOST" "$REMOTE"
