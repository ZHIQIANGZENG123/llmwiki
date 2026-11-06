<#
.SYNOPSIS
    Build a self-contained LLM Wiki bundle that installs and runs with no network access.

.DESCRIPTION
    Run this on a connected machine that has the same CPU architecture and the same
    Python major.minor as the air-gapped target. It produces:

        <Out>\repo\              source + web/node_modules + prebuilt web/.next
        <Out>\vendor\wheels\     Python wheels for offline install
        <Out>\vendor\requirements.txt
        <Out>\INSTALL-OFFLINE.md

    The frontend is intentionally prebuilt: `next build` downloads fonts at build time,
    so it cannot run on an isolated machine.

.PARAMETER Workspace
    Workspace folder to smoke-test the bundle against. It is only read, never copied.

.PARAMETER Out
    Destination folder for the bundle. Created if missing.

.PARAMETER Python
    Python executable used for `pip download`. Defaults to `python`.

.PARAMETER Build
    Run `npm ci` and `next build` before packaging instead of reusing web\.next.

.EXAMPLE
    pwsh tools/build-offline-bundle.ps1 -Workspace D:\wiki -Out D:\llmwiki-offline

.EXAMPLE
    pwsh tools/build-offline-bundle.ps1 -Workspace D:\wiki -Out D:\bundle -Build
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Workspace,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Python = 'python',
    [switch]$Build
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$web = Join-Path $repo 'web'
$outRoot = [System.IO.Path]::GetFullPath($Out)
$bundleRepo = Join-Path $outRoot 'repo'
$vendor = Join-Path $outRoot 'vendor'
$wheelDir = Join-Path $vendor 'wheels'

if (-not (Test-Path (Join-Path $Workspace '.llmwiki\index.db'))) {
    Write-Warn "Workspace '$Workspace' is not initialized. Run: python llmwiki init `"$Workspace`""
}

if (-not (Test-Path (Join-Path $web 'node_modules\next'))) {
    throw "web\node_modules is missing. Run 'npm ci' in web\ first (needs network)."
}

if ($Build) {
    Write-Step 'Installing web dependencies'
    Push-Location $web
    try {
        & 'npm.cmd' ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        & 'npm.cmd' run build
        if ($LASTEXITCODE -ne 0) { throw 'next build failed' }
    }
    finally { Pop-Location }
}

if (-not (Test-Path (Join-Path $web '.next\BUILD_ID'))) {
    throw "web\.next is not built. Re-run with -Build, or run 'npm run build' in web\ first."
}

Write-Step "Preparing $outRoot"
if (Test-Path $outRoot) {
    Write-Warn "Removing existing $outRoot"
    Remove-Item -LiteralPath $outRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $bundleRepo, $wheelDir -Force | Out-Null

Write-Step 'Copying source (excluding build output and virtualenvs)'
$excludeDirs = @(
    '.git', '.venv', 'venv', '.pydeps', '.piptmp', '.npmcache', '.pytest_cache',
    '__pycache__', 'node_modules', '.next', '.llmwiki'
)
$robocopyArgs = @($repo, $bundleRepo, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
    '/XD') + $excludeDirs
& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with code $LASTEXITCODE" }

Write-Step 'Copying web\node_modules (prebuilt, ready to use offline)'
& robocopy (Join-Path $web 'node_modules') (Join-Path $bundleRepo 'web\node_modules') /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy node_modules failed with code $LASTEXITCODE" }

Write-Step 'Copying prebuilt web\.next'
& robocopy (Join-Path $web '.next') (Join-Path $bundleRepo 'web\.next') /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy .next failed with code $LASTEXITCODE" }

# The worker is generated during install/build; make sure the copied tree has it.
$workerScript = Join-Path $bundleRepo 'web\scripts\copy-pdf-worker.mjs'
if (Test-Path $workerScript) {
    Push-Location (Join-Path $bundleRepo 'web')
    try { & node scripts\copy-pdf-worker.mjs }
    finally { Pop-Location }
}

Write-Step 'Downloading Python wheels'
$requirements = Join-Path $vendor 'requirements.txt'
Get-Content (Join-Path $repo 'api\requirements.txt'), (Join-Path $repo 'mcp\requirements.txt') |
    Set-Content -Path $requirements -Encoding utf8

& $Python -m pip download --dest $wheelDir -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'pip download failed — check the Python version and network' }

$pyVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"

$install = @"
# LLM Wiki — offline install

Built on $(Get-Date -Format 'yyyy-MM-dd HH:mm') from $repo
Requires Python $pyVersion on the target machine (same major.minor).

## 1. Copy this folder to the offline machine

## 2. Create a virtualenv and install the wheels

``````powershell
cd repo
python -m venv .venv
.venv\Scripts\python -m pip install --no-index --find-links ..\vendor\wheels -r ..\vendor\requirements.txt
``````

## 3. Start the server

Single machine only:

``````powershell
.venv\Scripts\python llmwiki open D:\wiki
``````

Shared on the LAN (this is the deployment shape for an isolated network):

``````powershell
.venv\Scripts\python llmwiki serve D:\wiki --host 0.0.0.0 --no-browser
``````

The first LAN start prints a generated workspace password and saves it to
``D:\wiki\.llmwiki\lan-password.txt``; pass ``--password`` to choose your own.

## Notes

- ``web\.next`` is prebuilt. Keep it: an isolated machine cannot run ``next build``
  because it downloads fonts at build time.
- If you change ``web\src``, rebuild on a connected machine and re-copy ``web\.next``.
- ``web\public\pdf.worker.min.mjs`` must exist for in-document PDF rendering.
- See ``DEPLOYMENT.md`` in the repo for HTTPS, firewall, and local-LLM setup.
"@
Set-Content -Path (Join-Path $outRoot 'INSTALL-OFFLINE.md') -Value $install -Encoding utf8

$size = (Get-ChildItem $outRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ''
Write-Host "Bundle ready: $outRoot  ($([math]::Round($size)) MiB)" -ForegroundColor Green
Write-Host "Next: read $outRoot\INSTALL-OFFLINE.md"
