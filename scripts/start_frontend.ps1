param([string]$Port = "5173")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $repo "frontend"
if (-not (Test-Path (Join-Path $frontend "package.json"))) { throw "frontend/package.json not found" }
$runDir = Join-Path $repo ".run"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$pidFile = Join-Path $runDir "frontend.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Output "FRONTEND_ALREADY_RUNNING pid=$old"
        exit 0
    }
}
$proc = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", $Port) -WorkingDirectory $frontend -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "frontend.out.log") -RedirectStandardError (Join-Path $runDir "frontend.err.log")
Set-Content -Path $pidFile -Value $proc.Id
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$Port/chat" -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) { Write-Output "FRONTEND_READY pid=$($proc.Id) url=http://127.0.0.1:$Port"; exit 0 }
    } catch { }
}
Write-Output "FRONTEND_NOT_READY"
exit 1
