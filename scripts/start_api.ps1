param([string]$Port = "8000")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\12189\.conda\envs\industrial-rag\python.exe"
if (-not (Test-Path $python)) { Write-Output "PYTHON_MISSING: $python"; exit 1 }
& (Join-Path $PSScriptRoot "check_env.ps1")
if ($LASTEXITCODE -ne 0) { exit 1 }
$runDir = Join-Path $repo ".run"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$pidFile = Join-Path $runDir "api.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Output "API_ALREADY_RUNNING pid=$old"
        exit 0
    }
}
$env:PYTHONPATH = Join-Path $repo "src"
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    # DB may have been created by create_all without an alembic_version row.
    & $python -m alembic stamp head
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Write-Output "MIGRATION_FAILED"; exit 1 }
}
try {
    $r = Invoke-RestMethod "http://127.0.0.1:16333/collections" -TimeoutSec 5
    Write-Output "qdrant ok"
} catch {
    Write-Output "QDRANT_NOT_READY"; exit 1
}
$proc = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "industrial_rag.api:app", "--host", "127.0.0.1", "--port", $Port) -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "api.out.log") -RedirectStandardError (Join-Path $runDir "api.err.log")
Set-Content -Path $pidFile -Value $proc.Id
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($h.status -eq "ok") {
            Write-Output "API_READY pid=$($proc.Id)"
            exit 0
        }
    } catch { }
}
Write-Output "API_NOT_READY"
exit 1
