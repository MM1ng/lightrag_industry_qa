$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Get-Content (Join-Path $repo ".env.local_staging") | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
$env:KNOWLEDGE_API_URL = "http://127.0.0.1:8010"
$env:PYTHONPATH = Join-Path $repo "src"
$runDir = Join-Path $repo "runtime\phase10b3b"
$python = "C:\Users\12189\.conda\envs\industrial-rag\python.exe"
$proc = Start-Process -FilePath $python -ArgumentList @(
    "-m", "streamlit", "run", "app/streamlit_app.py", "--server.address", "127.0.0.1", "--server.port", "8510", "--server.headless", "true"
) -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runDir "streamlit8510.out.log") `
    -RedirectStandardError (Join-Path $runDir "streamlit8510.err.log")
[pscustomobject]@{ pid = $proc.Id; port = 8510 }
