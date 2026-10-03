[CmdletBinding()]
param(
    [string]$Challenge = "challenges-csaw\ghost-in-the-machine",
    [string[]]$Models = @(
        "codex/gpt-5.5",
        "deepseek/deepseek-flash"
    ),
    [switch]$Submit,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found in PATH."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker was not found in PATH."
}

$challengePath = Join-Path $PSScriptRoot $Challenge
$metadataPath = Join-Path $challengePath "metadata.yml"
$distfilesPath = Join-Path $challengePath "distfiles"

if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
    throw "Challenge metadata was not found: $metadataPath"
}
if (-not (Test-Path -LiteralPath $distfilesPath -PathType Container)) {
    throw "Challenge distfiles were not found: $distfilesPath"
}
if (-not (Get-ChildItem -LiteralPath $distfilesPath -File -ErrorAction Stop | Select-Object -First 1)) {
    throw "Challenge distfiles directory is empty: $distfilesPath"
}

$metadata = Get-Content -LiteralPath $metadataPath
$challengeName = (($metadata | Select-String -Pattern '^name:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value).Trim(" '")
$connectionInfo = (($metadata | Select-String -Pattern '^connection_info:\s*(.*)$' | Select-Object -First 1).Matches.Groups[1].Value).Trim(" '")

if ($connectionInfo) {
    throw "This test script accepts offline challenges only; connection_info is not empty."
}

$arguments = @("run", "ctf-solve", "--challenge", $Challenge)
foreach ($model in $Models) {
    $arguments += @("--models", $model)
}
if (-not $Submit) {
    $arguments += "--no-submit"
}
$arguments += @("--max-challenges", "1", "-v")

$mode = if ($Submit) { "platform submission enabled" } else { "dry run; no platform submission" }
Write-Host "Challenge: $challengeName" -ForegroundColor Cyan
Write-Host "Mode: $mode"
Write-Host "Models: $($Models -join ', ')"
Write-Host "Command: uv $($arguments -join ' ')"
Write-Host "Trace directory: $(Join-Path $PSScriptRoot 'logs')"

if ($Preview) {
    Write-Host "PREVIEW ONLY: preflight passed; solver was not started." -ForegroundColor Yellow
    exit 0
}

& uv @arguments
exit $LASTEXITCODE
