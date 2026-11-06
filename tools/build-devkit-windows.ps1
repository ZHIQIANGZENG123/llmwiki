<#
.SYNOPSIS
    Build an offline *development* kit for Debian 12 on Windows.

.DESCRIPTION
    The deployment bundle only carries built output, so it cannot be edited. This produces
    a kit you can actually develop in on an air-gapped Debian 12 host:

        repo\                full source, including web\src, package.json and scripts
        repo\web\node_modules  dev tree installed for linux (next/swc, oxide, lightningcss)
        pylib\               python deps plus pytest for the API
        runtime\             portable Node.js for linux
        dev.sh               activates the environment and runs dev/web/api/test

    Same cross-platform tricks as the deploy bundle, plus one repo change this script
    assumes: `web/src/app/layout.tsx` now uses `geist/font/*` (next/font/local) instead of
    `next/font/google`, so `next build` never reaches fonts.googleapis.com.

.PARAMETER Out
    Destination directory for the kit. Roughly 1 GB before compression.

.EXAMPLE
    pwsh tools/build-devkit-windows.ps1 -Out D:\llmwiki-dev
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64',
    [string]$NodeVersion = '',
    [string]$Tarball = ''
)

$ErrorActionPreference = 'Stop'

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ok $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "error: $m" -ForegroundColor Red; exit 1 }

$repo = Split-Path $PSScriptRoot -Parent
$web = Join-Path $repo 'web'
$outRoot = [System.IO.Path]::GetFullPath($Out)
$bundleRepo = Join-Path $outRoot 'repo'
$pylib = Join-Path $outRoot 'pylib'

if ($outRoot.TrimEnd('\') -eq $repo.TrimEnd('\')) { Die 'Out must not be the repo root' }
if ($outRoot.StartsWith($repo + '\')) { Die 'Out must not be inside the repo' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Die 'node not found' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die 'python not found' }

$npm = if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { 'npm.cmd' } else { 'npm' }
$NodeVersion = if ($NodeVersion) { $NodeVersion.TrimStart('v') } else { (& node --version).TrimStart('v') }

$tarballOut = if ($Tarball) { [System.IO.Path]::GetFullPath($Tarball) } else { "$outRoot.tar.gz" }
$tarballDir = Split-Path $tarballOut -Parent
if (-not (Test-Path $tarballDir)) { Die "tarball directory does not exist: $tarballDir" }
$probe = Join-Path $tarballDir ".llmwiki-write-probe"
try { Set-Content -Path $probe -Value 'x' -ErrorAction Stop; Remove-Item $probe -Force }
catch { Die "cannot write to $tarballDir (a drive root needs admin); pass -Out or -Tarball elsewhere" }

Write-Host "repo:   $repo"
Write-Host "kit:    $outRoot"
Write-Host "node:   v$NodeVersion (linux-$Arch)"
Write-Host ''

# The web app must not fetch fonts at build time, or an offline build fails outright.
$layout = Join-Path $web 'src\app\layout.tsx'
if (Select-String -Path $layout -Pattern 'next/font/google' -Quiet) {
    Die 'web/src/app/layout.tsx still uses next/font/google — an offline build cannot download the font'
}
if (-not (Test-Path (Join-Path $web 'node_modules\geist'))) {
    Die 'web/node_modules/geist is missing — run: npm install geist'
}

# ---------------------------------------------------------------- source
Step 'Copying sources'
if (Test-Path $bundleRepo) { Remove-Item $bundleRepo -Recurse -Force }
New-Item -ItemType Directory $bundleRepo -Force | Out-Null

$excludeDirs = @('.next', 'node_modules', '.venv', '__pycache__', '.pytest_cache', '.llmwiki')
foreach ($dir in @('api', 'mcp', 'shared', 'tools', 'tests', 'web')) {
    $src = Join-Path $repo $dir
    $dst = Join-Path $bundleRepo $dir
    New-Item -ItemType Directory $dst -Force | Out-Null
    Get-ChildItem $src -Force | Where-Object { $excludeDirs -notcontains $_.Name } | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $dst $_.Name) -Recurse -Force
    }
}
foreach ($file in @('llmwiki', 'README.md', 'DEPLOYMENT.md', 'LICENSE', 'pytest.ini', 'ruff.toml')) {
    Copy-Item (Join-Path $repo $file) (Join-Path $bundleRepo $file) -Force
}
Ok 'sources copied'

# ---------------------------------------------------------------- node_modules (linux)
Step 'Installing web dependencies for linux (next/swc, oxide, lightningcss)'
$devWeb = Join-Path $bundleRepo 'web'
Push-Location $devWeb
try {
    & $npm ci --os=linux --cpu=$Arch --libc=glibc --no-audit --no-fund
    # The postinstall copy-pdf-worker runs fine here and is harmless if it fails.
    if ($LASTEXITCODE -ne 0) { Warn 'npm ci reported an error (usually only the postinstall script)' }
}
finally { Pop-Location }

$nm = Join-Path $devWeb 'node_modules'
if (-not (Test-Path (Join-Path $nm 'next'))) { Die 'node_modules was not created' }
$native = @(Get-ChildItem $nm -Recurse -File -Filter *.node -ErrorAction SilentlyContinue)
$bad = @()
foreach ($n in $native) {
    $h = [System.IO.File]::ReadAllBytes($n.FullName)[0..3]
    if ($h[0] -ne 0x7F -or $h[1] -ne 0x45) { $bad += $n.FullName }
}
if ($bad.Count -gt 0) { Die "non-ELF native modules (wrong platform): $($bad -join ', ')" }
Ok ("{0} ELF native modules, {1:N0} MB node_modules" -f $native.Count,
    ((Get-ChildItem $nm -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))

# ---------------------------------------------------------------- python
Step 'Resolving Linux Python dependencies (uv + pytest)'
$vendor = Join-Path $outRoot 'vendor'
New-Item -ItemType Directory $vendor -Force | Out-Null
$requirements = Join-Path $vendor 'requirements.txt'
$lines = @(Get-Content (Join-Path $repo 'api\requirements.txt')) +
         @(Get-Content (Join-Path $repo 'mcp\requirements.txt')) +
         @('pytest', 'pytest-asyncio')
[System.IO.File]::WriteAllLines($requirements, $lines)

& python -m uv --version *> $null
if ($LASTEXITCODE -ne 0) {
    Step 'Installing uv'
    & python -m pip install --user --quiet uv
    if ($LASTEXITCODE -ne 0) { Die 'could not install uv' }
}
$manylinux = if ($Arch -eq 'arm64') { 'aarch64-manylinux_2_28' } else { 'x86_64-manylinux_2_28' }
if (Test-Path $pylib) { Remove-Item $pylib -Recurse -Force }
New-Item -ItemType Directory $pylib -Force | Out-Null
& python -m uv pip install --target $pylib --python-version 3.11 --python-platform $manylinux `
    --only-binary :all: -r $requirements
if ($LASTEXITCODE -ne 0) { Die 'uv could not resolve the Linux dependency set' }

$binDir = Join-Path $pylib 'bin'
if (Test-Path $binDir) { Remove-Item $binDir -Recurse -Force }
if (@(Get-ChildItem $pylib -Recurse -File -Filter *.pyd).Count -gt 0) { Die 'resolved Windows .pyd files' }
Ok ("{0} packages, {1:N0} MB" -f (Get-ChildItem $pylib -Directory -Filter '*.dist-info').Count,
    ((Get-ChildItem $pylib -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Set-Content (Join-Path $pylib '.bundle-complete') "resolved $manylinux cp311 (dev)"

# ---------------------------------------------------------------- node runtime
Step "Downloading Node v$NodeVersion for linux-$Arch"
$runtime = Join-Path $outRoot 'runtime'
if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
New-Item -ItemType Directory $runtime -Force | Out-Null
$tarball = "node-v$NodeVersion-linux-$Arch.tar.gz"
$tmp = Join-Path $outRoot $tarball
$downloaded = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & curl.exe -fsSL --retry 3 --retry-delay 2 "https://nodejs.org/dist/v$NodeVersion/$tarball" -o $tmp
    if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp) -and (Get-Item $tmp).Length -gt 10MB) {
        $downloaded = $true; break
    }
    Warn "download attempt $attempt failed, retrying"
    Start-Sleep -Seconds 3
}
if (-not $downloaded) { Die "cannot download https://nodejs.org/dist/v$NodeVersion/$tarball" }
& tar.exe -xzf $tmp -C $runtime --strip-components=1 "node-v$NodeVersion-linux-$Arch/bin/node"
if ($LASTEXITCODE -ne 0) { Die 'could not extract the Node runtime' }
Remove-Item $tmp -Force
Ok 'node runtime ready'

# ---------------------------------------------------------------- dev.sh
Step 'Writing dev.sh'
@'
#!/usr/bin/env bash
# Offline development shell for LLM Wiki on Debian 12.
#
#   ./dev.sh shell     # bash with the bundled toolchain on PATH (for ad-hoc commands)
#   ./dev.sh web       # next dev  -> http://127.0.0.1:3000
#   ./dev.sh api       # uvicorn --reload -> 127.0.0.1:8000
#   ./dev.sh mcp       # MCP over stdio (for a local MCP client)
#   ./dev.sh test      # python unit tests + web unit tests
#   ./dev.sh check     # tsc --noEmit
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$KIT/runtime/bin:$PATH"
export PYTHONPATH="$KIT/pylib${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONNOUSERSITE=1
export NEXT_TELEMETRY_DISABLED=1
export NEXT_PUBLIC_MODE=local
export NEXT_PUBLIC_API_URL=/api
REPO="$KIT/repo"

case "${1:-shell}" in
  shell) exec bash ;;
  web)
    cd "$REPO/web"
    exec node node_modules/next/dist/bin/next dev --webpack --port "${PORT:-3000}" --hostname 127.0.0.1
    ;;
  api)
    cd "$REPO/api"
    exec python3 -m uvicorn main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
    ;;
  mcp)
    cd "$REPO/mcp"
    exec python3 -m local_server --workspace "${WORKSPACE:-$HOME/wiki}"
    ;;
  test)
    cd "$REPO"
    python3 -m pytest tests/unit -q -p no:cacheprovider || true
    cd "$REPO/web"
    exec node node_modules/vitest/vitest.mjs run
    ;;
  check)
    cd "$REPO/web"
    exec node node_modules/typescript/bin/tsc --noEmit
    ;;
  *) echo "unknown target: $1" >&2; exit 2 ;;
esac
'@ | Set-Content (Join-Path $outRoot 'dev.sh') -Encoding ascii

@"
kit_at=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')
built_with=windows-cross-build
python_minor=3.11
node_version=$NodeVersion
node_arch=$Arch
manylinux=$manylinux
"@ | Set-Content (Join-Path $outRoot 'KIT-INFO') -Encoding ascii

@'
# LLM Wiki — offline development kit (Debian 12)

Everything for editing this app with no network. Requires Python 3.11 on the host.

## Start here

```bash
./dev.sh check      # tsc --noEmit, confirms the toolchain works
./dev.sh web        # next dev on http://127.0.0.1:3000
./dev.sh api        # uvicorn --reload on 127.0.0.1:8000 (second terminal)
```

`next dev` proxies `/api` and `/mcp` to `127.0.0.1:8000` / `:8100`, so with both running
the UI is fully functional.

## Layout

| Path | What |
|---|---|
| `repo/` | full source — edit `web/src`, `api/`, `mcp/` |
| `repo/web/node_modules` | installed for linux; `next/swc`, `@tailwindcss/oxide`, `lightningcss` are ELF |
| `pylib/` | Python deps + pytest, resolved for linux/cp311 |
| `runtime/bin/node` | portable Node, already on PATH via `dev.sh` |

## Notes

- `next build` works offline here: the UI font is self-hosted via `geist/font/*`, so
  nothing is fetched from Google. You can still run a production build locally:
  `./dev.sh shell` then `cd repo/web && node node_modules/next/dist/bin/next build`.
- Do not run `npm install` / `npm ci` here: they need the network and would replace the
  Linux tree with a Windows one. To add a dependency, do it on the connected machine and
  rebuild this kit.
- Git works offline: `cd repo && git init` if you want a local history.
- To rebuild the deployment bundle after changing sources, do it on the connected Windows
  machine with `tools/build-bundle-windows.ps1`.
'@ | Set-Content (Join-Path $outRoot 'README-DEVKIT.md') -Encoding utf8

# ---------------------------------------------------------------- tarball
Step 'Creating the tarball'
if (Test-Path $tarballOut) { Remove-Item $tarballOut -Force }
$parent = Split-Path $outRoot -Parent
$leaf = Split-Path $outRoot -Leaf
$plain = Join-Path $tarballDir ".$leaf.raw.tar"
& tar.exe -C $parent -cf $plain $leaf
if ($LASTEXITCODE -ne 0) { Die "tar failed writing $plain" }

$fixer = Join-Path $tarballDir ".fix-modes.py"
@'
import sys, tarfile
src, dst, root = sys.argv[1], sys.argv[2], sys.argv[3]
executable = {f"{root}/dev.sh", f"{root}/repo/llmwiki",
              f"{root}/runtime/bin/node", f"{root}/repo/tools/deploy-debian.sh",
              f"{root}/repo/tools/build-bundle-linux.sh"}
with tarfile.open(src, "r:") as tin, tarfile.open(dst, "w:gz") as tout:
    for member in tin:
        if member.isdir() or member.name in executable:
            member.mode = 0o755
        elif member.isfile():
            member.mode = 0o644
        tout.addfile(member, tin.extractfile(member) if member.isfile() else None)
'@ | Set-Content $fixer -Encoding ascii
& python $fixer $plain $tarballOut $leaf
$fixExit = $LASTEXITCODE
Remove-Item $fixer, $plain -Force -ErrorAction SilentlyContinue
if ($fixExit -ne 0) { Die 'could not write the archive with executable bits' }

Write-Host ''
Write-Host "Kit:  $tarballOut ($([math]::Round((Get-Item $tarballOut).Length / 1MB, 1)) MB)" -ForegroundColor Green
Write-Host "Next: copy to the offline Debian 12 host, unpack, run ./dev.sh check"
