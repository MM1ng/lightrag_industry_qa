$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo ".env.local_staging"
Get-Content -LiteralPath $envFile | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
$env:PYTHONPATH = Join-Path $repo "src"
$runDir = Join-Path $repo "runtime\phase10b3b"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$python = "C:\Users\12189\.conda\envs\industrial-rag\python.exe"
$proc = Start-Process -FilePath $python -ArgumentList @(
    "-m", "uvicorn", "industrial_rag.api:app", "--host", "127.0.0.1", "--port", "8010"
) -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runDir "api8010.out.log") `
    -RedirectStandardError (Join-Path $runDir "api8010.err.log")
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8010/openapi.json" -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
[pscustomobject]@{
    pid = $proc.Id
    ready = $ready
    stderr_tail = ((Get-Content (Join-Path $runDir "api8010.err.log") -Tail 12 -ErrorAction SilentlyContinue) -join " | ")
}
