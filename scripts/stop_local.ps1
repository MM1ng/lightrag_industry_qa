# Stops API and Streamlit gracefully. Never stops or deletes Qdrant data.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $repo ".run"
foreach ($name in @("ui", "api")) {
    $pidFile = Join-Path $runDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        Write-Output "${name}: no pid file"
        continue
    }
    $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($pidValue) {
        try {
            $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                Write-Output "${name}: stopped pid=$pidValue"
            } else {
                Write-Output "${name}: stale pid (not running)"
            }
        } catch {
            Write-Output "${name}: could not stop pid=$pidValue ($($_.Exception.Message))"
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
Write-Output "stop_local done (Qdrant data untouched)"
exit 0
