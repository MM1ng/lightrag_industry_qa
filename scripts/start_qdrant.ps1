# Starts the Qdrant Docker infrastructure only (never business containers).
$ErrorActionPreference = "Stop"
$container = "ira-phase3-qdrant-test"
$port = 16333
$inUse = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    $running = docker ps --filter "name=$container" --format "{{.Names}}" 2>$null
    if ($running -match $container) {
        Write-Output "qdrant already running on $port"
        exit 0
    }
    Write-Output "PORT_CONFLICT: $port is in use by another process"
    exit 1
}
docker start $container 2>$null
if ($LASTEXITCODE -ne 0) {
    docker run -d --name $container -p "16333:6333" -p "16334:6334" qdrant/qdrant:v1.13.6
}
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$port/collections" -TimeoutSec 3
        Write-Output "qdrant ready: version=$($r.version)"
        exit 0
    } catch { }
}
Write-Output "QDRANT_NOT_READY"
exit 1
