param([string]$Port = "8501", [string]$ApiUrl = "http://127.0.0.1:8000")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\12189\.conda\envs\industrial-rag\python.exe"
try {
    $h = Invoke-RestMethod "$ApiUrl/health" -TimeoutSec 5
    if ($h.status -ne "ok") { throw "api not ok" }
} catch {
    Write-Output "API_NOT_READY"; exit 1
}
$runDir = Join-Path $repo ".run"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$pidFile = Join-Path $runDir "ui.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Output "UI_ALREADY_RUNNING pid=$old"
        exit 0
    }
}
$env:STREAMLIT_API_URL = $ApiUrl
$proc = Start-Process -FilePath $python -ArgumentList @("-m", "streamlit", "run", (Join-Path $repo "app\streamlit_app.py"), "--server.port", $Port, "--server.headless", "true") -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "ui.out.log") -RedirectStandardError (Join-Path $runDir "ui.err.log")
Set-Content -Path $pidFile -Value $proc.Id
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-Output "UI_READY pid=$($proc.Id)"
            exit 0
        }
    } catch { }
}
Write-Output "UI_NOT_READY"
exit 1
