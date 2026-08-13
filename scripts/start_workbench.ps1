[CmdletBinding()]
param(
    [string]$ApiPort = "8000",
    [string]$FrontendPort = "5173",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Import-IraEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Environment file not found: $Path"
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $iraEnvKey = $Matches[1]
            $iraEnvValue = ($_ -replace '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*', '').Trim()
            [Environment]::SetEnvironmentVariable($iraEnvKey, $iraEnvValue, 'Process')
        }
    }
}

Write-Output "[1/5] Starting Qdrant dependencies..."
& (Join-Path $PSScriptRoot "start_phase9b_qdrant.ps1")

Write-Output "[2/5] Loading local staging environment..."
Import-IraEnvFile (Join-Path $repo ".env.local_staging")

Write-Output "[3/5] Starting FastAPI..."
& (Join-Path $PSScriptRoot "start_api.ps1") -Port $ApiPort
if ($LASTEXITCODE -ne 0) { throw "FastAPI failed to start." }

Write-Output "[4/5] Starting Vue frontend..."
& (Join-Path $PSScriptRoot "start_frontend.ps1") -Port $FrontendPort
if ($LASTEXITCODE -ne 0) { throw "Vue frontend failed to start." }

Write-Output "[5/5] Verifying services..."
$ready = Invoke-RestMethod "http://127.0.0.1:$ApiPort/readyz" -TimeoutSec 10
$page = Invoke-WebRequest "http://127.0.0.1:$FrontendPort/chat" -UseBasicParsing -TimeoutSec 10
if ($ready.status -ne "ready") { throw "API is not ready: $($ready | ConvertTo-Json -Compress)" }
if ($page.StatusCode -ne 200) { throw "Frontend returned HTTP $($page.StatusCode)." }

$url = "http://127.0.0.1:$FrontendPort/chat"
Write-Output "WORKBENCH_READY api=http://127.0.0.1:$ApiPort frontend=$url graph=http://127.0.0.1:$FrontendPort/graph"
if (-not $NoBrowser) {
    Start-Process $url | Out-Null
    Write-Output "BROWSER_OPENED $url"
}
