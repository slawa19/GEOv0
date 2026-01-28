# Runs GEO Simulator UI prototype (standalone Vite app).
# Usage (PowerShell):
#   ./scripts/run_simulator_ui.ps1

param(
  [int]$Port = 5176,
  [string]$HostName = '127.0.0.1',
  [int]$MaxPortTries = 200
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$appDir = Join-Path $repoRoot 'simulator-ui/v2'

if (-not (Test-Path $appDir)) {
  throw "simulator-ui/v2 directory not found: $appDir"
}

Set-Location $appDir

if (-not (Test-Path (Join-Path $appDir 'node_modules'))) {
  Write-Host 'Installing dependencies (npm install)...'
  npm install
}

function Test-TcpOpen([string]$h, [int]$p, [int]$timeoutMs = 250) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $ar = $client.BeginConnect($h, $p, $null, $null)
    $ok = $ar.AsyncWaitHandle.WaitOne($timeoutMs, $false)
    if (-not $ok) {
      try { $client.Close() } catch {}
      return $false
    }
    $client.EndConnect($ar)
    try { $client.Close() } catch {}
    return $true
  } catch {
    return $false
  }
}

function Test-HttpOk([string]$url) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url -ErrorAction Stop
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Get-ExcludedTcpPortRanges([ValidateSet('ipv4','ipv6')] [string]$ipVersion) {
  $ranges = @()
  $netsh = Get-Command netsh -ErrorAction SilentlyContinue
  if (-not $netsh) {
    return $ranges
  }

  try {
    $out = & netsh interface $ipVersion show excludedportrange protocol=tcp 2>$null
  } catch {
    return $ranges
  }

  foreach ($line in $out) {
    if ($line -match '^\s*(\d+)\s+(\d+)\s*(?:\*.*)?$') {
      $start = [int]$Matches[1]
      $end = [int]$Matches[2]
      if ($start -gt 0 -and $end -ge $start) {
        $ranges += [pscustomobject]@{ Start = $start; End = $end }
      }
    }
  }

  return $ranges
}

function Test-PortExcluded([int]$p) {
  $v4 = Get-ExcludedTcpPortRanges -ipVersion 'ipv4'
  foreach ($r in $v4) {
    if ($p -ge $r.Start -and $p -le $r.End) { return $true }
  }
  $v6 = Get-ExcludedTcpPortRanges -ipVersion 'ipv6'
  foreach ($r in $v6) {
    if ($p -ge $r.Start -and $p -le $r.End) { return $true }
  }
  return $false
}

function Find-UsablePort([string]$h, [int]$startPort, [int]$maxTries) {
  for ($i = 0; $i -le $maxTries; $i++) {
    $candidate = $startPort + $i
    if ($candidate -gt 65535) {
      return $null
    }
    if (Test-PortExcluded $candidate) {
      continue
    }
    if (-not (Test-TcpOpen $h $candidate)) {
      return $candidate
    }
  }
  return $null
}

$selectedPort = $Port

if (Test-PortExcluded $selectedPort) {
  $alt = Find-UsablePort -h $HostName -startPort $Port -maxTries $MaxPortTries
  if ($alt) {
    $selectedPort = $alt
    Write-Host "Requested port $Port is excluded/reserved on this machine; using $selectedPort instead." -ForegroundColor Yellow
  } else {
    Write-Host "Requested port $Port looks excluded/reserved, but no usable alternative port found quickly; letting Vite choose." -ForegroundColor Yellow
  }
  Write-Host "(See: netsh interface ipv4|ipv6 show excludedportrange protocol=tcp)" -ForegroundColor DarkGray
}

if (Test-TcpOpen $HostName $selectedPort) {
  $url = "http://${HostName}:$selectedPort/"
  if (Test-HttpOk $url) {
    Write-Host ''
    Write-Host 'GEO Simulator UI already running.'
    Write-Host "Open in browser: $url"
    Write-Host ''
    exit 0
  }

  # Port is occupied by something else. Pick the next free one (skipping excluded ranges).
  $alt = Find-UsablePort -h $HostName -startPort ($selectedPort + 1) -maxTries $MaxPortTries
  if (-not $alt) {
    throw "Port $selectedPort is in use and no usable port found in range [$selectedPort..$($selectedPort + $MaxPortTries)]."
  }
  $selectedPort = $alt
}

Write-Host ''
Write-Host 'Starting GEO Simulator UI (Vite dev server)...'
Write-Host "Open in browser (preferred): http://${HostName}:$selectedPort/"
Write-Host "If the port is unavailable, Vite will pick another and print the actual URL." -ForegroundColor DarkGray
Write-Host ''

$viteCli = Join-Path $appDir 'node_modules/vite/bin/vite.js'
if (-not (Test-Path $viteCli)) {
  throw "Vite CLI not found: $viteCli (did npm install succeed?)"
}

node $viteCli --host $HostName --port $selectedPort


