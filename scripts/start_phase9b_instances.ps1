param(
    [string]$RuntimeRoot = "D:\industrial_energy_agent_phase9b_staging",
    [int[]]$Ports = @(8111, 8112),
    [switch]$DisableLlmCache,
    [switch]$EnableQueryNormalization,
    [ValidateSet("mix", "naive", "hybrid", "local", "global")]
    [string]$Phase10bQueryMode,
    [ValidateRange(1, 1000)]
    [int]$Phase10bTopK = 0,
    [ValidateRange(1, 1000)]
    [int]$Phase10bChunkTopK = 0,
    [ValidateRange(60, 604800)]
    [int]$RetrievalTraceTtlSeconds = 86400
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\12189\.conda\envs\industrial-rag\python.exe"
$envFile = Join-Path $RuntimeRoot "runtime\staging.env"
$runtime = Join-Path $RuntimeRoot "runtime"
$logs = Join-Path $RuntimeRoot "logs"

if (-not (Test-Path -LiteralPath $python)) { throw "industrial-rag Python is missing" }
if (-not (Test-Path -LiteralPath $envFile)) { throw "staging.env is missing" }

Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], "Process")
    }
}
if ($DisableLlmCache) {
    $env:ENABLE_LLM_CACHE = "false"
}
if ($EnableQueryNormalization) {
    $env:QA_QUERY_NORMALIZATION_ENABLED = "true"
}
if ($Phase10bQueryMode) {
    $env:PHASE10B_QUERY_MODE = $Phase10bQueryMode
}
if ($Phase10bTopK -gt 0) {
    $env:PHASE10B_TOP_K = [string]$Phase10bTopK
}
if ($Phase10bChunkTopK -gt 0) {
    $env:PHASE10B_CHUNK_TOP_K = [string]$Phase10bChunkTopK
}
$env:RETRIEVAL_TRACE_TTL_SECONDS = [string]$RetrievalTraceTtlSeconds
$env:APP_GIT_COMMIT = (& git -C $repo rev-parse HEAD).Trim()
$env:PYTHONPATH = Join-Path $repo "src"

for ($index = 0; $index -lt $Ports.Count; $index++) {
    $name = "api-$([char]([int][char]'a' + $index))"
    $pidFile = Join-Path $runtime "$name.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $oldPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
        if ($oldPid) {
            Stop-Process -Id $oldPid -ErrorAction SilentlyContinue
        }
    }
    $env:INSTANCE_ID = $name
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-m", "uvicorn", "industrial_rag.api:app", "--host", "127.0.0.1",
            "--port", [string]$Ports[$index]
        ) `
        -WorkingDirectory $repo `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$name.out.log") `
        -RedirectStandardError (Join-Path $logs "$name.err.log")
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$($Ports[$index])/health" -TimeoutSec 2
            if ($health.status -eq "ok") { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { throw "API port $($Ports[$index]) did not become ready" }
    Write-Output "API_READY port=$($Ports[$index])"
}
