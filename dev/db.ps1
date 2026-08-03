<#
.SYNOPSIS
    The local Postgres for JobDigest development.

.DESCRIPTION
    A wrapper round service/db/docker-compose.yml so the one thing that is easy to get wrong
    is a named command.

        dev\db.ps1 up        start it, wait until it answers
        dev\db.ps1 reset     down -v + up  - destroys the data and re-applies schema.sql
        dev\db.ps1 down      stop, keep the data
        dev\db.ps1 status    container state + what schema.sql-era columns exist
        dev\db.ps1 psql      interactive psql inside the container (extra args pass through)
        dev\db.ps1 logs      tail the container log

    WHY `reset` EXISTS AND WHEN YOU NEED IT: schema.sql is mounted into
    /docker-entrypoint-initdb.d/ and Postgres runs that **only on an empty data directory**.
    A volume created before a migration keeps the old schema for ever, and the failures that
    produces read as application bugs - a 500 from an INSERT naming a column that isn't there.
    Nothing migrates this volume; `reset` is the migration.
#>
[CmdletBinding()]
param(
    # Named $Action, not $Command: PowerShell binds parameters by prefix, so
    # `db.ps1 psql -c "select ..."` would bind the SQL to -Command and fail ValidateSet.
    # Nothing else here starts with "c", so -c now falls through to $Rest, where psql wants it.
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'reset', 'status', 'psql', 'logs')]
    [string]$Action = 'up',

    # `reset` destroys data, so it asks first. -Force skips the prompt for scripted use.
    [switch]$Force,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\env.ps1"

$root    = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'service\db\docker-compose.yml'
$container = 'jobmatch_pg'

function Invoke-Compose {
    # Not named $Args: that shadows PowerShell's automatic variable of the same name.
    param([string[]]$ComposeArgs)
    & docker compose -f $compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') failed ($LASTEXITCODE)"
    }
}

function Wait-Healthy {
    # The compose file declares a pg_isready healthcheck; trust it rather than racing a
    # connection. A seed run against a container that is up but still replaying WAL fails
    # with a connection error that looks like a wrong port.
    for ($i = 0; $i -lt 60; $i++) {
        $state = (& docker inspect -f '{{.State.Health.Status}}' $container 2>$null)
        if ($state -eq 'healthy') {
            Write-Host "postgres healthy on 127.0.0.1:$($env:JD_DB_PORT)" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "postgres did not become healthy - try: dev\db.ps1 logs"
}

switch ($Action) {

    'up' {
        Invoke-Compose @('up', '-d')
        Wait-Healthy
        Write-Host "DATABASE_URL=$($env:DATABASE_URL)"
    }

    'down' {
        Invoke-Compose @('down')
    }

    'reset' {
        Write-Host "This DESTROYS the local jobmatch database and re-applies schema.sql." -ForegroundColor Yellow
        if (-not $Force) {
            $answer = Read-Host "Type 'reset' to continue"
            if ($answer -ne 'reset') { Write-Host 'Aborted.'; break }
        }
        Invoke-Compose @('down', '-v')
        Invoke-Compose @('up', '-d')
        Wait-Healthy
        Write-Host "Schema re-applied. Next: python dev\seed.py" -ForegroundColor Green
    }

    'status' {
        & docker ps -a --filter "name=$container" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
        # The columns three migrations added. If these are missing the volume predates them
        # and every symptom you are about to chase is the stale schema, not your change.
        $sql = @'
select 'postings.work_mode'        as col, count(*) from information_schema.columns where table_name='postings' and column_name='work_mode'
union all select 'postings.education_min',  count(*) from information_schema.columns where table_name='postings' and column_name='education_min'
union all select 'profiles.language',       count(*) from information_schema.columns where table_name='profiles' and column_name='language'
union all select 'profiles.education_levels', count(*) from information_schema.columns where table_name='profiles' and column_name='education_levels'
union all select 'postings rows',           count(*) from postings
union all select 'profiles rows',           count(*) from profiles;
'@
        $sql | & docker compose -f $compose exec -T db psql -U jobmatch -d jobmatch -v ON_ERROR_STOP=1
    }

    'psql' {
        if ($Rest) {
            & docker compose -f $compose exec -T db psql -U jobmatch -d jobmatch @Rest
        } else {
            & docker compose -f $compose exec db psql -U jobmatch -d jobmatch
        }
    }

    'logs' {
        Invoke-Compose @('logs', '--tail', '80', 'db')
    }
}
