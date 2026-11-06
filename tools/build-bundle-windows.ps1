<#
.SYNOPSIS
    Build an offline deployment bundle for Debian 12 (bookworm) entirely from Windows.

.DESCRIPTION
    Produces <Out>\ containing everything an air-gapped Debian 12 host needs:

        repo\                source for the API/MCP plus a prebuilt standalone web app
        pylib\               Python dependencies, resolved for linux + cp311 (ELF, not .pyd)
        runtime\             portable Node.js for linux
        deploy.sh            install / start / systemd unit
        BUNDLE-INFO          build metadata the deploy script validates

    Two things make this possible without a Linux machine:

    * `output: standalone` traces the web app into a self-contained directory. Its only
      native artifact is Windows `sharp`, and this app never uses `next/image`, so that
      folder is dropped and the rest is portable JavaScript.
    * `uv pip install --python-platform ...` resolves and downloads Linux wheels with the
      target's environment markers — plain `pip download --platform` still evaluates
      markers against the build machine and pulls Windows-only packages.

    Note: the cross-platform Python step requires uv. It is installed from PyPI if absent.

.PARAMETER Out
    Destination directory for the bundle.

.PARAMETER Arch
    Target CPU: x64 (default) or arm64.

.PARAMETER NodeVersion
    Node.js version to bundle. Defaults to the version on this machine.

.PARAMETER SkipWebBuild
    Reuse web\.next instead of running `npm run build`.

.EXAMPLE
    pwsh tools/build-bundle-windows.ps1 -Out D:\llmwiki-bundle
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [ValidateSet('x64', 'arm64')][string]$Arch = 'x64',
    [string]$NodeVersion = '',
    [string]$Tarball = '',
    # A self-contained pi install tree (`npm install --prefix <dir> ...`).
    # The chat drawer cannot work offline without it.
    [string]$PiDir = '',
    [switch]$SkipWebBuild
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
if ($outRoot.StartsWith('\\?\') -or $outRoot.Length -gt 120) {
    Warn 'a long or UNC output path can break npm/tar; prefer a short path like D:\bundle'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Die 'node not found' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die 'python not found' }

# Fail before a ten-minute build, not after: the tarball lands beside the bundle, and a
# drive root (C:\) needs elevation to write to.
$tarballOut = if ($Tarball) { [System.IO.Path]::GetFullPath($Tarball) } else { "$outRoot.tar.gz" }
$tarballDir = Split-Path $tarballOut -Parent
if (-not (Test-Path $tarballDir)) { Die "tarball directory does not exist: $tarballDir" }
$probe = Join-Path $tarballDir ".llmwiki-write-probe"
try { Set-Content -Path $probe -Value 'x' -ErrorAction Stop; Remove-Item $probe -Force }
catch {
    Die ("cannot write to $tarballDir (a drive root needs admin). " +
         "Pass -Out D:\something or -Tarball D:\llmwiki-bundle.tar.gz")
}

# npm ships as npm.cmd on Windows; PowerShell may refuse npm.ps1 under an execution policy.
$npm = if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { 'npm.cmd' } else { 'npm' }
$NodeVersion = if ($NodeVersion) { $NodeVersion.TrimStart('v') } else { (& node --version).TrimStart('v') }
$nodeArch = $Arch

Write-Host "repo:   $repo"
Write-Host "bundle: $outRoot"
Write-Host "node:   v$NodeVersion (linux-$nodeArch)"
Write-Host "python: 3.11 target (linux)"
Write-Host ''

# ---------------------------------------------------------------- 1. web build
$standalone = Join-Path $web '.next\standalone'
if ($SkipWebBuild -and (Test-Path (Join-Path $standalone 'server.js'))) {
    Step 'Reusing the existing standalone build'
} else {
    if (-not (Test-Path (Join-Path $web 'node_modules\next'))) {
        Step 'Installing web dependencies'
        Push-Location $web
        try { & $npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { Die 'npm ci failed' } }
        finally { Pop-Location }
    }
    Step 'Building the web app (local mode, same-origin /api)'
    $env:NEXT_PUBLIC_MODE = 'local'
    $env:NEXT_PUBLIC_API_URL = '/api'
    $env:NEXT_TELEMETRY_DISABLED = '1'
    Push-Location $web
    try { & $npm run build; if ($LASTEXITCODE -ne 0) { Die 'next build failed' } }
    finally { Pop-Location }
}

if (-not (Test-Path (Join-Path $standalone 'server.js'))) {
    Die "output: standalone was not produced — check next.config.ts"
}

# ---------------------------------------------------------------- 2. assemble web
Step 'Assembling the standalone web app'
$target = Join-Path $bundleRepo 'web\.next'
if (Test-Path $bundleRepo) { Remove-Item $bundleRepo -Recurse -Force }
New-Item -ItemType Directory -Path $target -Force | Out-Null

Copy-Item $standalone (Join-Path $target 'standalone') -Recurse
$sa = Join-Path $target 'standalone'
Copy-Item (Join-Path $web '.next\static') (Join-Path $sa '.next\static') -Recurse
Copy-Item (Join-Path $web 'public') (Join-Path $sa 'public') -Recurse

# sharp is Next's optional image optimizer; this app never calls next/image, and a
# Windows .node binary would make the bundle platform-locked. Drop it entirely.
$imgDir = Join-Path $sa 'node_modules\@img'
if (Test-Path $imgDir) {
    Get-ChildItem $imgDir -Directory -Filter 'sharp-*' | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force
        Ok "dropped native package $($_.Name)"
    }
}
$native = @(Get-ChildItem $sa -Recurse -File -Filter *.node -ErrorAction SilentlyContinue)
if ($native.Count -gt 0) {
    Die ("standalone still contains native binaries, it would not run on linux: " + ($native.Name -join ', '))
}
Ok 'no native binaries in the web app'

if (-not (Test-Path (Join-Path $sa 'public\pdf.worker.min.mjs'))) {
    Warn 'pdf.worker.min.mjs missing — PDF previews would need the network'
}
$stat = Get-ChildItem $sa -Recurse -File | Measure-Object Length -Sum
Ok ("web app: {0:N1} MB / {1} files" -f ($stat.Sum / 1MB), $stat.Count)

# ---------------------------------------------------------------- 3. source
Step 'Copying API/MCP sources'
foreach ($dir in @('api', 'mcp', 'shared', 'tools')) {
    Copy-Item (Join-Path $repo $dir) (Join-Path $bundleRepo $dir) -Recurse -Force
}
Get-ChildItem $bundleRepo -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
foreach ($file in @('llmwiki', 'README.md', 'DEPLOYMENT.md', 'LICENSE')) {
    Copy-Item (Join-Path $repo $file) (Join-Path $bundleRepo $file) -Force
}

# ---------------------------------------------------------------- 4. python deps
Step 'Resolving Linux Python dependencies (uv)'
if (-not (Test-Path (Join-Path $outRoot 'vendor'))) {
    New-Item -ItemType Directory (Join-Path $outRoot 'vendor') -Force | Out-Null
}
$requirements = Join-Path $outRoot 'vendor\requirements.txt'
$lines = @(Get-Content (Join-Path $repo 'api\requirements.txt')) +
         @(Get-Content (Join-Path $repo 'mcp\requirements.txt'))
# WriteAllLines emits UTF-8 without a BOM; pip/uv would choke on a BOM-prefixed first line.
[System.IO.File]::WriteAllLines($requirements, $lines)

& python -m uv --version *> $null
if ($LASTEXITCODE -ne 0) {
    Step 'Installing uv (pinned pip cannot resolve cross-platform markers)'
    & python -m pip install --user --quiet uv
    if ($LASTEXITCODE -ne 0) { Die 'could not install uv; run: python -m pip install --user uv' }
}

$manylinux = if ($Arch -eq 'arm64') { 'aarch64-manylinux_2_28' } else { 'x86_64-manylinux_2_28' }
if (Test-Path $pylib) { Remove-Item $pylib -Recurse -Force }
New-Item -ItemType Directory $pylib -Force | Out-Null
& python -m uv pip install --target $pylib --python-version 3.11 --python-platform $manylinux `
    --only-binary :all: -r $requirements
if ($LASTEXITCODE -ne 0) { Die 'uv could not resolve the Linux dependency set' }

# Windows console-script shims are useless on the target and misleading in a review.
$binDir = Join-Path $pylib 'bin'
if (Test-Path $binDir) { Remove-Item $binDir -Recurse -Force }
Get-ChildItem $pylib -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

$soFiles = @(Get-ChildItem $pylib -Recurse -File -Filter *.so)
$pydFiles = @(Get-ChildItem $pylib -Recurse -File -Filter *.pyd)
if ($pydFiles.Count -gt 0) { Die "resolved Windows binaries ($($pydFiles.Count) .pyd) — target was not linux" }
if ($soFiles.Count -eq 0) { Warn 'no compiled extensions found; expected asyncpg/lxml/cryptography' } else {
    $head = [System.IO.File]::ReadAllBytes($soFiles[0].FullName)[0..3]
    if ($head[0] -ne 0x7F -or $head[1] -ne 0x45 -or $head[2] -ne 0x4C -or $head[3] -ne 0x46) {
        Die "compiled extensions are not ELF: $($soFiles[0].Name)"
    }
    Ok "$($soFiles.Count) ELF extensions, 0 Windows .pyd"
}
$stat = Get-ChildItem $pylib -Recurse -File | Measure-Object Length -Sum
Ok ("python deps: {0:N1} MB / {1} files, {2} packages" -f ($stat.Sum / 1MB), $stat.Count,
    (Get-ChildItem $pylib -Directory -Filter '*.dist-info').Count)
Set-Content (Join-Path $pylib '.bundle-complete') "resolved $manylinux cp311"

# ---------------------------------------------------------------- 5. node runtime
Step "Downloading Node v$NodeVersion for linux-$nodeArch"
$runtime = Join-Path $outRoot 'runtime'
if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
New-Item -ItemType Directory $runtime -Force | Out-Null
$tarball = "node-v$NodeVersion-linux-$nodeArch.tar.gz"
$url = "https://nodejs.org/dist/v$NodeVersion/$tarball"
$tmp = Join-Path $outRoot $tarball
$downloaded = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & curl.exe -fsSL --retry 3 --retry-delay 2 $url -o $tmp
    if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp) -and (Get-Item $tmp).Length -gt 10MB) {
        $downloaded = $true; break
    }
    Warn "download attempt $attempt failed, retrying"
    Start-Sleep -Seconds 3
}
if (-not $downloaded) { Die "cannot download $url" }
# Extract only the real ELF binary. bin/npm, bin/npx and bin/corepack are symlinks, which
# Windows' bsdtar cannot create ("Invalid argument"), and the runtime does not need them.
& tar.exe -xzf $tmp -C $runtime --strip-components=1 "node-v$NodeVersion-linux-$nodeArch/bin/node"
if ($LASTEXITCODE -ne 0) { Die 'could not extract the Node runtime' }
Remove-Item $tmp -Force

$nodeBin = Join-Path $runtime 'bin\node'
$head = [System.IO.File]::ReadAllBytes($nodeBin)[0..3]
if ($head[0] -ne 0x7F -or $head[1] -ne 0x45) { Die 'bundled node is not an ELF binary' }
Ok "node runtime: $( [math]::Round((Get-ChildItem $runtime -Recurse -File | Measure-Object Length -Sum).Sum / 1MB) ) MB (cannot be executed on Windows)"

# ---------------------------------------------------------------- 6. deploy payload
Step 'Writing the deploy payload'
Copy-Item (Join-Path $repo 'tools\deploy-debian.sh') (Join-Path $outRoot 'deploy.sh') -Force

$builtOn = "$([System.Environment]::OSVersion.VersionString) (host arch $env:PROCESSOR_ARCHITECTURE)"
@"
built_at=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')
built_on="$builtOn"
built_with=windows-cross-build
python_minor=3.11
node_version=$NodeVersion
node_arch=$nodeArch
manylinux=$manylinux
"@ | Set-Content (Join-Path $outRoot 'BUNDLE-INFO') -Encoding utf8

@'
# LLM Wiki — offline bundle (Debian 12 bookworm)

Built on Windows, targeted at Linux. Nothing here needs the network on the target.

## 1. Copy this directory to the offline host

## 2. Run the deploy script

```bash
sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0 --port 3000
```

If your unzip tool dropped the executable bits, use `sudo bash ./deploy.sh ...` instead.

The first LAN start prints a generated workspace password and saves it to
`/srv/llmwiki/.llmwiki/lan-password.txt`. Re-run with `--password '...'` to set your own.

## 3. Install it as a service (optional)

```bash
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0
```

Then `systemctl status llmwiki` / `journalctl -u llmwiki -f`.

## Notes

- `deploy.sh --check` verifies prerequisites without starting anything.
- Python dependencies are pre-installed for linux/cp311 in `pylib/`: the host needs no
  pip, no venv and no network. Requires Python 3.11 (Debian 12's default).
- The web app is a prebuilt `output: standalone` server; Node is bundled in `runtime/`.
- Optional extras from the Debian ISO: `libreoffice-writer libreoffice-impress`
  (Word/PowerPoint), `default-jre-headless` (PDF parsing).
- To change `repo/web/src` you must rebuild this bundle on Windows.
'@ | Set-Content (Join-Path $outRoot 'README-OFFLINE.md') -Encoding utf8

# ---------------------------------------------------------------- 6b. pi agent
$piVersion = ''
if ($PiDir) {
    Step 'Bundling the pi agent'
    $piSource = [System.IO.Path]::GetFullPath($PiDir)
    $piManifest = Join-Path $piSource 'node_modules\@earendil-works\pi-coding-agent\package.json'
    if (-not (Test-Path $piManifest)) { Die "not a pi install tree (no pi-coding-agent package.json): $PiDir" }
    $piVersion = (Get-Content $piManifest -Raw | ConvertFrom-Json).version
    Copy-Item $piSource (Join-Path $outRoot 'pi') -Recurse -Force
    # BUNDLE-INFO was already written; append rather than rewrite so a failed rebuild
    # never leaves half a metadata file behind.
    Add-Content -Path (Join-Path $outRoot 'BUNDLE-INFO') -Value "pi_version=$piVersion"
    Ok "pi $piVersion bundled"

    # models.json / mcp.json / APPEND_SYSTEM.md steer the agent; they must ship with it.
    $agentConfig = Join-Path $repo '.pi-agent'
    if (Test-Path $agentConfig) {
        Copy-Item $agentConfig (Join-Path $bundleRepo '.pi-agent') -Recurse -Force
        Ok '.pi-agent config copied'
    } else {
        Warn 'no .pi-agent config found; pi will start without a model or MCP server'
    }
} else {
    Warn 'no -PiDir given: the chat drawer will not work on the target'
}

# ---------------------------------------------------------------- 7. tarball
Step 'Creating the tarball'
if (Test-Path $tarballOut) { Remove-Item $tarballOut -Force }
$parent = Split-Path $outRoot -Parent
$leaf = Split-Path $outRoot -Leaf
$plain = Join-Path $tarballDir ".$leaf.raw.tar"

# bsdtar on Windows has no POSIX modes to record, so every entry lands as 0666 and the
# target cannot execute deploy.sh or node. Build the archive uncompressed first, then
# re-emit it with gzip while restoring the modes that must be executable.
& tar.exe -C $parent -cf $plain $leaf
if ($LASTEXITCODE -ne 0) { Die "tar failed writing $plain" }

$fixer = Join-Path $tarballDir ".fix-modes.py"
@'
import sys, tarfile

src, dst, root = sys.argv[1], sys.argv[2], sys.argv[3]
executable = {
    f"{root}/deploy.sh",
    f"{root}/repo/llmwiki",
    f"{root}/runtime/bin/node",
    f"{root}/repo/tools/build-bundle-linux.sh",
    f"{root}/repo/tools/deploy-debian.sh",
}

with tarfile.open(src, "r:") as tin, tarfile.open(dst, "w:gz") as tout:
    for member in tin:
        if member.isdir():
            member.mode = 0o755
        elif member.name in executable:
            member.mode = 0o755
        elif member.isfile():
            member.mode = 0o644
        payload = tin.extractfile(member) if member.isfile() else None
        tout.addfile(member, payload)
'@ | Set-Content $fixer -Encoding ascii

& python $fixer $plain $tarballOut $leaf
$fixExit = $LASTEXITCODE
Remove-Item $fixer -Force -ErrorAction SilentlyContinue
Remove-Item $plain -Force -ErrorAction SilentlyContinue
if ($fixExit -ne 0) { Die 'could not write the archive with executable bits' }

$mb = [math]::Round((Get-Item $tarballOut).Length / 1MB, 1)
Write-Host ''
Write-Host "Bundle: $tarballOut ($mb MB)" -ForegroundColor Green
Write-Host "Next:   copy it to the offline Debian 12 host, unpack, then run ./deploy.sh --check"
