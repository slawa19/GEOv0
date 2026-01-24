# Runs GEO Simulator UI prototype (standalone Vite app).
# Usage (PowerShell):
#   ./scripts/run_simulator_ui.ps1

param(
  [int]$Port = 5176,
  [string]$HostName = '127.0.0.1',
  [int]$MaxPortTries = 10
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

$selectedPort = $Port

if (Test-TcpOpen $HostName $selectedPort) {
  $url = "http://${HostName}:$selectedPort/"
  if (Test-HttpOk $url) {
    Write-Host ''
    Write-Host 'GEO Simulator UI already running.'
    Write-Host "Open in browser: $url"
    Write-Host ''
    exit 0
  }

  # Port is occupied by something else. Pick the next free one.
  $found = $false
  for ($i = 1; $i -le $MaxPortTries; $i++) {
    $candidate = $Port + $i
    if (-not (Test-TcpOpen $HostName $candidate)) {
      $selectedPort = $candidate
      $found = $true
      break
    }
  }
  if (-not $found) {
    throw "Port $Port is in use and no free port found in range [$Port..$($Port + $MaxPortTries)]."
  }
}

Write-Host ''
Write-Host 'Starting GEO Simulator UI (Vite dev server)...'
Write-Host "Open in browser: http://${HostName}:$selectedPort/"
Write-Host ''

$viteCli = Join-Path $appDir 'node_modules/vite/bin/vite.js'
if (-not (Test-Path $viteCli)) {
  throw "Vite CLI not found: $viteCli (did npm install succeed?)"
}

node $viteCli --host $HostName --port $selectedPort --strictPort


