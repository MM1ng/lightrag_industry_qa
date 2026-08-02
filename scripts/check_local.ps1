param([string]$Port = "8000", [string]$UiPort = "8501")
$ErrorActionPreference = "Continue"
& (Join-Path $PSScriptRoot "check_env.ps1")
try {
    $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    Write-Output "api /health: $($h.status)"
} catch { Write-Output "api /health: unreachable" }
try {
    $r = Invoke-RestMethod "http://127.0.0.1:$Port/ready" -TimeoutSec 5
    Write-Output "api /ready: $($r.status) components=$($r.components | ConvertTo-Json -Compress)"
} catch { Write-Output "api /ready: unreachable" }
try {
    $v = Invoke-RestMethod "http://127.0.0.1:$Port/version" -TimeoutSec 5
    Write-Output "api /version: $($v.app_version) commit=$($v.git_commit.Substring(0, [Math]::Min(8, $v.git_commit.Length)))"
} catch { Write-Output "api /version: unreachable" }
try {
    $q = Invoke-RestMethod "http://127.0.0.1:16333/collections" -TimeoutSec 5
    Write-Output "qdrant: ok collections=$($q.result.collections.Count)"
} catch { Write-Output "qdrant: unreachable" }
$ui = Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue
Write-Output ("streamlit port {0}: {1}" -f $UiPort, $(if ($ui) { "listening" } else { "closed" }))
Write-Output "check_local done (no secrets printed)"
