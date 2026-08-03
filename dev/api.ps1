<#
.SYNOPSIS
    The JobDigest API (service/webapp.py) on http://localhost:8811, with auto-reload.

.DESCRIPTION
    Note the module: `service.webapp`, NOT `service.api`. service/api.py is the pre-v1
    internal API - no auth, not deployed (the Dockerfile runs service.webapp). The "Run
    locally" block in service/README.md still names it and is the ancestor of this script.

    Needs the database up first: dev\db.ps1 up
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\env.ps1"

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "api      http://localhost:$($env:JD_API_PORT)   (docs: /docs)" -ForegroundColor Cyan
    Write-Host "db       $($env:DATABASE_URL)"
    Write-Host "mail     MAIL_BACKEND=$($env:MAIL_BACKEND) -> service\outbox\"
    # --reload-dir service is not optional: uvicorn's reloader otherwise watches the whole cwd,
    # and the cwd contains web/node_modules - tens of thousands of files, which on Windows
    # means the watcher either pegs a core or hits the handle limit and stops reloading at all.
    # --host localhost binds both ::1 and 127.0.0.1, so the browser reaches it whichever way
    # Windows resolves the name; the name itself matters for the session cookie (see env.ps1).
    & python -m uvicorn service.webapp:app --reload --reload-dir service `
        --host localhost --port $env:JD_API_PORT @Rest
} finally {
    Pop-Location
}
