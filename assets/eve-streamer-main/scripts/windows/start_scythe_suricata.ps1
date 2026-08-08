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

# The Windows build can combine a correct local wall clock with an incorrect
# DST offset in EVE timestamps.  UTC removes that ambiguity at the sensor
# boundary; SCYTHE retains UTC throughout ingestion.
$env:TZ = "UTC"

if (-not (Test-Path $exe) -or -not (Test-Path $config)) {
    throw "SCYTHE Suricata deployment is incomplete under $base"
}

$existing = Get-Process suricata -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $exe } |
    Select-Object -First 1
if ($existing) {
    Set-Content -Path $pidFile -Value $existing.Id -Encoding ascii
    Write-Output "SCYTHE Suricata is already running as PID $($existing.Id)."
    exit 0
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

$adapter = Get-NetAdapter -Name $AdapterName -ErrorAction Stop
if ($adapter.Status -ne "Up") {
    throw "Capture adapter '$AdapterName' is not up."
}
$adapterGuid = $adapter.InterfaceGuid.ToString().Trim("{} ").ToUpperInvariant()
$device = "\Device\NPF_{$adapterGuid}"
$process = Start-Process -FilePath $exe `
    -ArgumentList @("-c", $config, "-i", $device, "-vv") `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
    -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $process.Id -Encoding ascii
Write-Output "Started SCYTHE Suricata PID $($process.Id) on $AdapterName ($device)."
