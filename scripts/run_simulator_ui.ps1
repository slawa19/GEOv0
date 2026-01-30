# Runs GEO Simulator UI prototype (standalone Vite app).
# Usage (PowerShell):
#   ./scripts/run_simulator_ui.ps1

param(
  [int]$Port = 5176,
  [string]$HostName = '127.0.0.1',
  [int]$MaxPortTries = 200,

  # If the port is already serving the Simulator UI, stop that dev server and restart it.
  # Useful when you changed env/proxy defaults but the old Vite server is still running.
  [switch]$RestartIfRunning,

  # UI API mode:
  # - fixtures: demo/fixtures mode (default)
  # - real: real backend integration (fetch-stream SSE + REST)
  [ValidateSet('fixtures', 'real')]
  [string]$Mode = 'fixtures',

  # Backend origin for Vite proxy (/api/v1 -> backend). Used mainly for Mode=real.
  # Example: http://127.0.0.1:8000
  [string]$BackendOrigin = ''
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

function Test-BackendHealthzOk([string]$origin) {
  $url = "$origin/healthz"

  try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url -ErrorAction Stop
    if ($r.StatusCode -lt 200 -or $r.StatusCode -ge 300) {
      return $false
    }

    try {
      $body = $r.Content | ConvertFrom-Json
      return ($null -ne $body -and $body.status -eq 'ok')
    } catch {
      # If JSON parsing fails, treat it as not-our-backend.
      return $false
    }
  } catch {
    return $false
  }
}

function Try-Detect-LocalBackendOrigin([string]$hostForUrl) {
  # Default local dev backend port is scripts/run_local.ps1 -BackendPort (default 18000)
  $origin = "http://${hostForUrl}:18000"
  if (Test-BackendHealthzOk $origin) {
    return $origin
  }
  return $null
}

function Try-Detect-DockerBackendOrigin([string]$hostForUrl) {
  # Detect the published host port for the API container (geov0-app:8000) and return an origin like http://127.0.0.1:18000
  $port = $null

  try {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
      $out = & docker port geov0-app 8000/tcp 2>$null
      $last = ($out | Select-Object -Last 1)
      if ($last -and ($last -match ':(\d+)\s*$')) {
        $port = [int]$Matches[1]
      }
    }
  } catch {}

  if (-not $port) {
    try {
      $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
      if ($wsl) {
        $out = & wsl.exe -e bash -lc "docker port geov0-app 8000/tcp" 2>$null
        $last = ($out | Select-Object -Last 1)
        if ($last -and ($last -match ':(\d+)\s*$')) {
          $port = [int]$Matches[1]
        }
      }
    } catch {}
  }

  if ($port) {
    return "http://${hostForUrl}:$port"
  }

  return $null
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
    if (-not $RestartIfRunning) {
      Write-Host ''
      Write-Host 'GEO Simulator UI already running.'
      Write-Host "Open in browser: $url"
      Write-Host "Tip: pass -RestartIfRunning to restart the dev server (useful after env/proxy changes)." -ForegroundColor DarkGray
      Write-Host ''
      exit 0
    }

    Write-Host ''
    Write-Host "GEO Simulator UI already running on $url; restarting..." -ForegroundColor Yellow

    $listenerPid = $null
    try {
      $conn = Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction Stop | Select-Object -First 1
      if ($conn) { $listenerPid = $conn.OwningProcess }
    } catch {}

    if (-not $listenerPid) {
      throw "Cannot determine PID listening on port $selectedPort."
    }

    $proc = $null
    try { $proc = Get-Process -Id $listenerPid -ErrorAction Stop } catch {}

    $cmdLine = ''
    try {
      $wmi = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerPid" -ErrorAction SilentlyContinue
      if ($wmi) { $cmdLine = [string]$wmi.CommandLine }
    } catch {}

    $isProbablyOurVite = $false
    $appDirWin = $appDir.Replace('/', '\\')
    if ($proc -and $proc.ProcessName -eq 'node') {
      if ($cmdLine -and ($cmdLine -like '*vite*') -and (($cmdLine -like "*$appDir*") -or ($cmdLine -like "*$appDirWin*"))) {
        $isProbablyOurVite = $true
      }
    }

    if (-not $isProbablyOurVite) {
      throw "Refusing to stop PID $listenerPid ($($proc.ProcessName)) on port $selectedPort because it doesn't look like our Vite dev server. CommandLine: $cmdLine"
    }

    Stop-Process -Id $listenerPid -Force
    Start-Sleep -Milliseconds 250
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
if ($Mode -eq 'real') {
  Write-Host "Open in browser (preferred): http://${HostName}:$selectedPort/?mode=real"
} else {
  Write-Host "Open in browser (preferred): http://${HostName}:$selectedPort/"
}
Write-Host "If the port is unavailable, Vite will pick another and print the actual URL." -ForegroundColor DarkGray
Write-Host ''

$viteCli = Join-Path $appDir 'node_modules/vite/bin/vite.js'
if (-not (Test-Path $viteCli)) {
  throw "Vite CLI not found: $viteCli (did npm install succeed?)"
}

if ($Mode -eq 'real') {
  $env:VITE_API_MODE = 'real'
} else {
  Remove-Item Env:VITE_API_MODE -ErrorAction SilentlyContinue
}

if ($Mode -eq 'real' -and [string]::IsNullOrWhiteSpace($BackendOrigin)) {
  $detectedLocal = Try-Detect-LocalBackendOrigin -hostForUrl $HostName
  if ($detectedLocal) {
    $BackendOrigin = $detectedLocal
    Write-Host "Detected backend origin from local dev backend (healthz ok): ${BackendOrigin}" -ForegroundColor DarkGray
  } else {
    $detectedDocker = Try-Detect-DockerBackendOrigin -hostForUrl $HostName
    if ($detectedDocker) {
      $BackendOrigin = $detectedDocker
      Write-Host "Detected backend origin from Docker: ${BackendOrigin}" -ForegroundColor DarkGray
    } else {
      Write-Host 'Backend origin not provided and could not be auto-detected (local 18000 or Docker geov0-app).' -ForegroundColor Yellow
      Write-Host 'Pass -BackendOrigin http://127.0.0.1:<port> to force it.' -ForegroundColor Yellow
      throw 'Cannot start Simulator UI in real mode without a reachable backend. (Refusing to silently proxy to an unrelated service.)'
    }
  }
}

if (-not [string]::IsNullOrWhiteSpace($BackendOrigin)) {
  $env:VITE_GEO_BACKEND_ORIGIN = $BackendOrigin
} else {
  Remove-Item Env:VITE_GEO_BACKEND_ORIGIN -ErrorAction SilentlyContinue
}

node $viteCli --host $HostName --port $selectedPort


