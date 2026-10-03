param(
    [int]$Port = 9400
)

$ErrorActionPreference = "Stop"
$uri = "http://127.0.0.1:$Port/status"

try {
    $status = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 5
} catch {
    Write-Host "没有连上 coordinator 状态端口 $Port。"
    Write-Host "先在另一个终端运行："
    Write-Host "  cd D:\AI-WorkSpace\Codex-WorkSpace\CTF\ctf-agent"
    Write-Host "  .\run-questionctf-2026.ps1"
    exit 1
}

Write-Host "=== ?CTF 2026 ctf-agent 状态 ==="
Write-Host ("Active challenges: {0}" -f $status.active_count)
Write-Host ("Cost: `${0}  Tokens: {1}" -f $status.total_cost_usd, $status.total_tokens)
Write-Host ""

if (-not $status.active -or $status.active.Count -eq 0) {
    Write-Host "当前没有正在运行的 solver。"
    Write-Host "如果还没开赛，这是正常的；开赛后题目出现会自动启动。"
} else {
    $status.active |
        Sort-Object challenge, model |
        ForEach-Object {
            $findings = ""
            if ($_.findings) {
                $oneLine = $_.findings -replace "(`r`n|`n|`r)", " "
                $findings = $oneLine.Substring(0, [Math]::Min(80, $oneLine.Length))
            }
            [PSCustomObject]@{
                Model = $_.model
                Challenge = $_.challenge
                Status = $_.status
                Findings = $findings
            }
        } |
        Format-Table -AutoSize
}

Write-Host ""
Write-Host "Queued / running / solved:"
$status.task_registry |
    Sort-Object status, category, name |
    ForEach-Object {
        [PSCustomObject]@{
            Status = $_.status
            Category = $_.category
            Solves = $_.solves
            Name = $_.name
        }
    } |
    Format-Table -AutoSize
