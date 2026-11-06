# WPS-based stand-in for the LibreOffice conversion this app expects.
#
# `api/domain/local_processor.py` runs:
#   soffice -env:UserInstallation=... --headless --norestore --nofirststartwizard
#           --convert-to pdf --outdir <dir> <file>
# and then looks for a produced .pdf in <dir>. WPS Office is installed here and exposes the
# Word-compatible COM API, so we honour the same contract with ExportAsFixedFormat.
#
# Exit codes match the LibreOffice CLI closely enough: 0 on success, 1 on failure.

$ErrorActionPreference = 'Stop'

$inputFile = $null
$outDir = $null
$i = 0
while ($i -lt $args.Count) {
    $a = [string]$args[$i]
    switch -Regex ($a) {
        '^--outdir$'      { $i++; $outDir = [string]$args[$i] }
        '^--outdir=(.+)$' { $outDir = $Matches[1] }
        '^-'              { }   # --headless, -env:..., --convert-to pdf (value handled below)
        '^pdf$'           { }   # the value of --convert-to
        default           { $inputFile = $a }
    }
    $i++
}

if (-not $inputFile) { [Console]::Error.WriteLine('wps-soffice: no input file'); exit 1 }
if (-not (Test-Path -LiteralPath $inputFile)) {
    [Console]::Error.WriteLine("wps-soffice: input not found: $inputFile"); exit 1
}
if (-not $outDir) { $outDir = Split-Path -Parent $inputFile }
if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }

$outFile = Join-Path $outDir ([System.IO.Path]::GetFileNameWithoutExtension($inputFile) + '.pdf')

$app = $null
$doc = $null
try {
    $app = New-Object -ComObject KWps.Application
    try { $app.Visible = $false } catch { }
    try { $app.DisplayAlerts = 0 } catch { }

    # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles)
    $doc = $app.Documents.Open($inputFile, $false, $true, $false)
    # ExportAsFixedFormat(OutputFileName, ExportFormat=17 (PDF))
    $doc.ExportAsFixedFormat($outFile, 17)
}
catch {
    [Console]::Error.WriteLine("wps-soffice: " + $_.Exception.Message)
    $code = 1
}
finally {
    if ($doc) { try { $doc.Close(0) } catch { } }
    if ($app) { try { $app.Quit() } catch { } }
    foreach ($n in 'doc', 'app') {
        $o = Get-Variable -Name $n -ValueOnly -ErrorAction SilentlyContinue
        if ($o) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($o) }
    }
}

if (-not (Test-Path -LiteralPath $outFile)) {
    [Console]::Error.WriteLine("wps-soffice: conversion produced no PDF")
    exit 1
}
Write-Output "convert $inputFile -> $outFile"
exit 0
