[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 9621,

    [string]$WorkingDirectory = "",

    [string]$InputDirectory = "",

    [string]$CondaExe = "D:\anaconda\Scripts\conda.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    $WorkingDirectory = Join-Path $projectRoot "data\processed\lightrag\storage"
}
if ([string]::IsNullOrWhiteSpace($InputDirectory)) {
    $InputDirectory = Join-Path $projectRoot "data\processed\lightrag\inputs"
}

$portOwner = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $portOwner) {
    throw "Port $Port is already in use by PID $($portOwner.OwningProcess); refusing to start LightRAG."
}

$serviceKey = $env:LIGHTRAG_API_KEY
$modelKey = if (-not [string]::IsNullOrWhiteSpace($env:LLM_API_KEY)) {
    $env:LLM_API_KEY
} else {
    $env:DASHSCOPE_API_KEY
}

if ([string]::IsNullOrWhiteSpace($serviceKey)) {
    throw "LIGHTRAG_API_KEY must be set in the current process."
}
if ([string]::IsNullOrWhiteSpace($modelKey)) {
    throw "LLM_API_KEY or DASHSCOPE_API_KEY must be set in the current process."
}
if ($serviceKey -ceq $modelKey) {
    throw "LIGHTRAG_API_KEY must be different from the BaiLian model key."
}

$modelBaseUrl = if (-not [string]::IsNullOrWhiteSpace($env:LLM_BASE_URL)) {
    $env:LLM_BASE_URL.TrimEnd("/")
} else {
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
}

$env:LLM_BINDING_HOST = $modelBaseUrl
$env:EMBEDDING_BINDING_HOST = $modelBaseUrl
$env:LLM_BINDING_API_KEY = $modelKey
$env:EMBEDDING_BINDING_API_KEY = $modelKey
$env:LLM_MODEL = "qwen3.7-plus"
$env:EMBEDDING_MODEL = "text-embedding-v4"
$env:EMBEDDING_DIM = "1024"
$env:EMBEDDING_SEND_DIM = "true"

$pythonExecutable = (
    & $CondaExe run -n energyops-lightrag python -c "import sys; print(sys.executable)" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
).Trim()
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Unable to resolve the energyops-lightrag Python executable."
}
$serverExecutable = Join-Path (Split-Path -Parent $pythonExecutable) "Scripts\lightrag-server.exe"
if (-not (Test-Path -LiteralPath $serverExecutable -PathType Leaf)) {
    throw "lightrag-server is not installed in energyops-lightrag."
}

$logDirectory = Join-Path $projectRoot "data\processed\lightrag\logs"
New-Item -ItemType Directory -Force -Path $WorkingDirectory, $InputDirectory, $logDirectory |
    Out-Null

$stdinFile = Join-Path $logDirectory "server.stdin.empty"
$stdoutLog = Join-Path $logDirectory "server.$Port.stdout.log"
$stderrLog = Join-Path $logDirectory "server.$Port.stderr.log"
if (-not (Test-Path -LiteralPath $stdinFile -PathType Leaf)) {
    New-Item -ItemType File -Path $stdinFile | Out-Null
}
$arguments = @(
    "--host", "127.0.0.1",
    "--port", $Port.ToString(),
    "--working-dir", $WorkingDirectory,
    "--input-dir", $InputDirectory,
    "--llm-binding", "openai",
    "--embedding-binding", "openai",
    "--log-level", "INFO"
)

$previousPythonUtf8 = [Environment]::GetEnvironmentVariable("PYTHONUTF8", "Process")
$previousPythonIoEncoding = [Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "Process")
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $server = Start-Process `
        -FilePath $serverExecutable `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -RedirectStandardInput $stdinFile `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
} finally {
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", $previousPythonUtf8, "Process")
    [Environment]::SetEnvironmentVariable(
        "PYTHONIOENCODING",
        $previousPythonIoEncoding,
        "Process"
    )
}

try {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $server.Refresh()
        if ($server.HasExited) {
            throw "LightRAG server exited during startup. Inspect data/processed/lightrag/logs."
        }
        $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -First 1
    } until ($null -ne $listener -or (Get-Date) -gt $deadline)

    if ($null -eq $listener) {
        throw "LightRAG server did not bind 127.0.0.1:$Port within 30 seconds."
    }
} catch {
    if ($null -ne $listener -and $listener.OwningProcess -ne $server.Id) {
        $listenerProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($listener.OwningProcess)" `
            -ErrorAction SilentlyContinue
        if (
            $null -ne $listenerProcess -and
            $listenerProcess.CommandLine -like "*lightrag-server.exe*" -and
            $listenerProcess.CommandLine -like "*--port $Port*"
        ) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    throw
}

Write-Output "LightRAG server started on 127.0.0.1:$Port"
Write-Output "PID=$($listener.OwningProcess)"
Write-Output "API key: SET (value not printed)"
