<#
.SYNOPSIS
    The Next.js site (`next dev`) on http://localhost:3000, pointed at the local API.

.DESCRIPTION
    `next dev` is NOT the artefact that ships. Production is `output: "export"` - static files
    behind Caddy, same-origin /api, with the compatibility redirects and the CSP header. Two
    things to run before deploying anything the dev server made look fine:

        cd web; npm run build      export-time breakage (the two root layouts,
                                   a missing generateStaticParams) only appears here
        deploy/docker-compose.smoke.yml   the real prod-shaped stack on :8085

    See dev\README.md.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\env.ps1"

# Next reads PORT itself. Passing --port through `npm run` would mean threading a bare `--`
# through PowerShell's native-command parsing, which is its own small trap.
$env:PORT = $env:JD_WEB_PORT

$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'web')
try {
    Write-Host "web      http://localhost:$($env:JD_WEB_PORT)" -ForegroundColor Cyan
    Write-Host "api      NEXT_PUBLIC_API_URL=$($env:NEXT_PUBLIC_API_URL)"
    & npm run dev @Rest
} finally {
    Pop-Location
}
