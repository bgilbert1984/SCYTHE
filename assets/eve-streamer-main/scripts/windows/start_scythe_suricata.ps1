param(
    [string]$AdapterName = "Wi-Fi"
)

$ErrorActionPreference = "Stop"
$base = Join-Path $env:USERPROFILE "SCYTHE-Suricata-8.0.6"
$exe = Join-Path $base "app\PFiles\Suricata\suricata.exe"
$config = Join-Path $base "runtime\scythe-suricata.yaml"
$stdout = Join-Path $base "runtime\suricata-stdout.log"
$stderr = Join-Path $base "runtime\suricata-stderr.log"
$pidFile = Join-Path $base "runtime\suricata.pid"
$startupLog = Join-Path $base "runtime\suricata-startup.log"
$boundaryFile = Join-Path $base "runtime\sensor-boundary.json"

Set-Content -Path $startupLog -Value "$(Get-Date -Format o) START // adapter=$AdapterName" -Encoding utf8
trap {
    Add-Content -Path $startupLog -Value "$(Get-Date -Format o) FAILED // $($_.Exception.Message)" -Encoding utf8
    exit 1
}

# The Windows build can combine a correct local wall clock with an incorrect
# DST offset in EVE timestamps.  UTC removes that ambiguity at the sensor
# boundary; SCYTHE retains UTC throughout ingestion.
$env:TZ = "UTC"

if (-not (Test-Path $exe) -or -not (Test-Path $config)) {
    throw "SCYTHE Suricata deployment is incomplete under $base"
}

$expectedExeSha256 = "10F4922E317E8776BC0C8554B03DBD6EFF62A36950EC1DD17B9F1FC27D992623"
$exeSha256 = (Get-FileHash -Algorithm SHA256 $exe).Hash
if ($exeSha256 -ne $expectedExeSha256) {
    throw "Suricata executable SHA-256 does not match the verified OISF MSI payload."
}

& $exe -T -c $config
if ($LASTEXITCODE -ne 0) {
    throw "Suricata configuration validation failed with exit code $LASTEXITCODE"
}

$adapterDeadline = [DateTime]::UtcNow.AddMinutes(2)
do {
    $adapter = Get-NetAdapter -Name $AdapterName -ErrorAction SilentlyContinue
    if ($adapter -and $adapter.Status -eq "Up") { break }
    Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $adapterDeadline)
if (-not $adapter -or $adapter.Status -ne "Up") {
    throw "Capture adapter '$AdapterName' did not become ready within 120 seconds."
}
Add-Content -Path $startupLog -Value "$(Get-Date -Format o) ADAPTER_READY // $($adapter.InterfaceDescription)" -Encoding utf8
$localCidrs = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressState Preferred -ErrorAction Stop |
    Where-Object { $_.IPAddress -and $_.PrefixLength -and $_.IPAddress -notin @("127.0.0.1", "::1") } |
    ForEach-Object { "$($_.IPAddress.Split('%')[0])/$($_.PrefixLength)" })
if (-not $localCidrs.Count) {
    throw "Capture adapter '$AdapterName' has no preferred IP boundary."
}
$boundary = [ordered]@{
    schemaVersion = "scythe.sensor-boundary.v1"
    sensorId = "$env:COMPUTERNAME/$AdapterName"
    adapterName = $AdapterName
    interfaceDescription = $adapter.InterfaceDescription
    capturedAt = (Get-Date).ToUniversalTime().ToString("o")
    authority = "DISCOVERED_SENSOR_INTERFACE"
    localCidrs = $localCidrs
}
$boundary | ConvertTo-Json -Depth 3 | Set-Content -Path $boundaryFile -Encoding utf8
Add-Content -Path $startupLog -Value "$(Get-Date -Format o) SENSOR_BOUNDARY // $($localCidrs -join ',')" -Encoding utf8

$existing = Get-Process suricata -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $exe } |
    Select-Object -First 1
if ($existing) {
    Set-Content -Path $pidFile -Value $existing.Id -Encoding ascii
    Write-Output "SCYTHE Suricata is already running as PID $($existing.Id); sensor boundary refreshed."
    exit 0
}

$adapterGuid = $adapter.InterfaceGuid.ToString().Trim("{} ").ToUpperInvariant()
$device = "\Device\NPF_{$adapterGuid}"
$process = Start-Process -FilePath $exe `
    -ArgumentList @("-c", $config, "-i", $device, "-vv") `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
    -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $process.Id -Encoding ascii
Start-Sleep -Seconds 2
if ($process.HasExited) {
    throw "Suricata exited during startup with code $($process.ExitCode)."
}
Add-Content -Path $startupLog -Value "$(Get-Date -Format o) RUNNING // pid=$($process.Id) device=$device" -Encoding utf8
Write-Output "Started SCYTHE Suricata PID $($process.Id) on $AdapterName ($device)."
