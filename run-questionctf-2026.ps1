param(
    [int]$MaxChallenges = 4,
    [switch]$DryRun,
    [string[]]$Models = @("codex/gpt-5.5", "deepseek/deepseek-flash"),
    [switch]$RaceModels,
    [int]$PrefetchConcurrency = 6,
    [int]$StatusPort = 9400
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ChallengeDir = Join-Path $Root "challenges-questionctf-2026"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtualenv not found: $Python"
}

$argsList = @(
    "-m", "backend.cli",
    "--platform", "ctfplus",
    "--ctfd-url", "https://www.ctfplus.cn",
    "--competition-id", "2105658865197518848",
    "--challenges-dir", $ChallengeDir,
    "--coordinator", "codex",
    "--coordinator-model", "gpt-5.5",
    "--max-challenges", "$MaxChallenges",
    "--msg-port", "$StatusPort",
    "--prefetch-concurrency", "$PrefetchConcurrency",
    "-v"
)

foreach ($model in $Models) {
    $argsList += @("--models", $model)
}

if ($DryRun) {
    $argsList += "--no-submit"
}

if ($RaceModels) {
    $argsList += "--race-models"
}

Write-Host "Starting ? CTF 2026 automation with ctf-agent..."
Write-Host "DryRun: $DryRun"
Write-Host "ModelAssignment: $(if ($RaceModels) { 'race' } else { 'split' })"
Write-Host "PrefetchConcurrency: $PrefetchConcurrency"
Write-Host "MaxChallenges: $MaxChallenges"
Write-Host "Status: .\status-questionctf-2026.ps1 -Port $StatusPort"
Write-Host "ChallengeDir: $ChallengeDir"
& $Python @argsList
