param(
    [string]$EnvFile = ""
)
# Validates required RC environment variables without printing values.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if ($EnvFile -and (Test-Path $EnvFile)) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], ($_ -replace '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*', '').Trim(), "Process")
        }
    }
}
$required = @{
    "DASHSCOPE_API_KEY" = "must be configured"
    "LLM_MODEL" = "must be qwen-plus-2025-07-28"
    "MODEL_FALLBACK_ENABLED" = "must be false"
    "EMBEDDING_MODEL" = "must be text-embedding-v4"
    "EMBEDDING_DIM" = "must be 1024"
    "QDRANT_URL" = "must be configured"
    "QDRANT_COLLECTION_PREFIX" = "must be configured"
    "SERVICE_API_KEY" = "must be configured"
    "ADMIN_API_KEY" = "must be configured and differ from SERVICE_API_KEY"
}
$fail = @()
if (
    [System.Environment]::GetEnvironmentVariable("SERVICE_API_KEY", "Process") -and
    [System.Environment]::GetEnvironmentVariable("ADMIN_API_KEY", "Process") -and
    [System.Environment]::GetEnvironmentVariable("SERVICE_API_KEY", "Process") -ceq
        [System.Environment]::GetEnvironmentVariable("ADMIN_API_KEY", "Process")
) {
    Write-Output "invalid: SERVICE_API_KEY and ADMIN_API_KEY must differ"
    $fail += "SERVICE_API_KEY_ADMIN_API_KEY_EQUAL"
}
foreach ($name in $required.Keys) {
    $value = [System.Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $value) {
        Write-Output ("missing: " + $name)
        $fail += $name
        continue
    }
    $ok = switch ($name) {
        "LLM_MODEL" { $value -eq "qwen-plus-2025-07-28" }
        "MODEL_FALLBACK_ENABLED" { $value.ToLower() -eq "false" }
        "EMBEDDING_MODEL" { $value -eq "text-embedding-v4" }
        "EMBEDDING_DIM" { $value -eq "1024" }
        default { $true }
    }
    if (-not $ok) {
        Write-Output ("invalid: " + $name)
        $fail += $name
    } else {
        Write-Output ("configured: " + $name)
    }
}
if ($fail.Count -gt 0) {
    Write-Output ("RESULT=FAIL missing_or_invalid=" + ($fail -join ","))
    exit 1
}
Write-Output "RESULT=OK"
exit 0
