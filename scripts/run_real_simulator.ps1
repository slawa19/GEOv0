<#
.SYNOPSIS
  Autostart + diagnostics for Real Simulator testing.

.DESCRIPTION
  Brings up everything needed to test the real simulator via simulator-ui/v2:
  - Postgres + Redis + API (Docker Compose)
  - Optional DB seeding (admin fixtures -> rich demo dataset)
  - Simulator UI v2 in Real Mode (Vite dev server with /api/v1 proxy)

  Designed for Windows PowerShell 5.1+ / PowerShell 7+.

.EXAMPLE
  ./scripts/run_real_simulator.ps1

.EXAMPLE
  ./scripts/run_real_simulator.ps1 -Community riverside-town-50 -RegenerateFixtures

.EXAMPLE
  ./scripts/run_real_simulator.ps1 -Action doctor

.EXAMPLE
  ./scripts/run_real_simulator.ps1 -Action stop
#>
[CmdletBinding()]
param(
  [ValidateSet('start', 'doctor', 'seed', 'stop')]
  [string]$Action = 'start',

  [string]$HostName = '127.0.0.1',
  [int]$ApiPort = 8000,
  [int]$SimulatorUiPort = 5176,

  # Community fixture pack to seed into DB for real-mode runs.
  # 'none' disables seeding.
  [ValidateSet('greenfield-village-100', 'riverside-town-50', 'greenfield-village-100-v2', 'riverside-town-50-v2', 'none')]
  [string]$Community = 'greenfield-village-100',

  [switch]$RegenerateFixtures,
  [switch]$NoSimulatorUi,

  # Host port to publish Redis on (Docker Compose uses GEO_REDIS_PORT).
  # If 6379 is already in use, the script will automatically pick an alternative unless explicitly provided.
  [int]$RedisPort = 6379,

  # Force using Docker inside WSL (recommended when Docker Desktop is not available).
  [switch]$UseWsl,

  # Optional WSL distro name (if you have multiple).
  [string]$WslDistro = ''
)

$ErrorActionPreference = 'Stop'

$script:UserSpecifiedApiPort = $PSBoundParameters.ContainsKey('ApiPort')
$script:UserSpecifiedRedisPort = $PSBoundParameters.ContainsKey('RedisPort')

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$script:backendOrigin = "http://${HostName}:$ApiPort"
$script:apiDocsUrl = "${script:backendOrigin}/docs"

function Write-UsefulLinks([switch]$IncludeSimulatorUi) {
  Write-Host ''
  Write-Host '== Links ==' -ForegroundColor DarkGray
  Write-Host "API docs: ${script:apiDocsUrl}" -ForegroundColor Cyan
  Write-Host "Simulator UI (fixtures): http://${HostName}:${SimulatorUiPort}/" -ForegroundColor Cyan
  Write-Host "Simulator UI (real): http://${HostName}:${SimulatorUiPort}/?mode=real" -ForegroundColor Cyan
}

function Write-DockerComposeOutput([string[]]$lines) {
  foreach ($line in ($lines | Where-Object { $_ -ne $null })) {
    $text = $line.ToString()
    if (-not $text.Trim()) { continue }

    if ($text -match '^\s*Container\s+.+\s+Healthy\s*$') {
      Write-Host $text -ForegroundColor Green
      continue
    }
    if ($text -match '^\s*Container\s+.+\s+Running\s*$') {
      Write-Host $text -ForegroundColor Green
      continue
    }
    if ($text -match '^\s*Container\s+.+\s+Started\s*$') {
      Write-Host $text -ForegroundColor Green
      continue
    }
    if ($text -match '^\s*\[\+\]\s+Running\s+\d+/\d+') {
      Write-Host $text -ForegroundColor Green
      continue
    }

    if ($text -match '^\s*Container\s+.+\s+(Recreate|Recreated|Starting|Waiting)\s*$') {
      Write-Host $text -ForegroundColor Yellow
      continue
    }

    Write-Host $text
  }
}

function Warn-MissingDockerAndExit() {
  Write-Host 'Docker is required for this script.' -ForegroundColor Yellow
  Write-Host 'This environment expects Docker to be run inside WSL.' -ForegroundColor Yellow
  Write-Host 'Install docker engine inside your WSL distro and ensure `docker` works there, then re-run.' -ForegroundColor Yellow
  Write-Host 'Tip: pass -UseWsl (or set WslDistro) if auto-detect fails.' -ForegroundColor DarkGray
  exit 2
}

function Convert-ToWslPath([string]$winPath) {
  # Best-effort conversion: D:\a\b -> /mnt/d/a/b
  $p = (Resolve-Path $winPath).Path
  if ($p -match '^([A-Za-z]):\\(.*)$') {
    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2].Replace('\\', '/').Replace('\', '/')
    return "/mnt/${drive}/${rest}"
  }
  # Fallback: replace backslashes.
  return $p.Replace('\\', '/').Replace('\', '/')
}

function Get-WslArgsPrefix() {
  if ([string]::IsNullOrWhiteSpace($WslDistro)) {
    return @()
  }
  return @('-d', $WslDistro)
}

function Invoke-WslBash([string]$bashCommand) {
  $prefix = Get-WslArgsPrefix
  # Use bash -lc so that PATH, aliases etc are loaded predictably.
  $captured = & wsl.exe @prefix -e bash -lc $bashCommand 2>&1
  if ($LASTEXITCODE -ne 0) {
    $outText = ($captured | Out-String)
    throw "WSL command failed (exit=$LASTEXITCODE): $bashCommand`n$outText"
  }

  return ,$captured
}

function Test-WslDockerAvailable() {
  try {
    $prefix = Get-WslArgsPrefix
    & wsl.exe @prefix -e bash -lc 'command -v docker >/dev/null 2>&1'
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Test-NativeDockerAvailable() {
  return [bool](Get-Command docker -ErrorAction SilentlyContinue)
}

function Get-DockerMode() {
  if ($UseWsl) { return 'wsl' }
  if (Test-NativeDockerAvailable) { return 'native' }
  if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    if (Test-WslDockerAvailable) { return 'wsl' }
  }
  return 'missing'
}

function Invoke-DockerCompose([string[]]$composeArgs) {
  $mode = Get-DockerMode
  if ($mode -eq 'missing') { Warn-MissingDockerAndExit }

  # Force stable output without ANSI, so we can colorize ourselves.
  $argsWithAnsi = @('--ansi','never') + $composeArgs

  if ($mode -eq 'native') {
    $out = & docker compose @argsWithAnsi 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("docker compose failed (exit=$LASTEXITCODE)`n" + ($out | Out-String)) }
    Write-DockerComposeOutput $out
    return
  }

  $wslRepoRoot = Convert-ToWslPath $repoRoot
  $joined = ($argsWithAnsi | ForEach-Object { "'" + ($_ -replace "'", "'\\''") + "'" }) -join ' '

  $envPrefixParts = @()
  if (-not [string]::IsNullOrWhiteSpace($env:GEO_REDIS_PORT)) {
    $envPrefixParts += ("GEO_REDIS_PORT='" + ($env:GEO_REDIS_PORT -replace "'", "'\\''") + "'")
  }
  if (-not [string]::IsNullOrWhiteSpace($env:GEO_API_PORT)) {
    $envPrefixParts += ("GEO_API_PORT='" + ($env:GEO_API_PORT -replace "'", "'\\''") + "'")
  }
  $envPrefix = ''
  if ($envPrefixParts.Count -gt 0) {
    $envPrefix = ($envPrefixParts -join ' ') + ' '
  }

  $out = Invoke-WslBash "cd '$wslRepoRoot' && ${envPrefix}docker compose $joined"
  Write-DockerComposeOutput $out
}

function Invoke-Docker([string[]]$dockerArgs) {
  $mode = Get-DockerMode
  if ($mode -eq 'missing') { Warn-MissingDockerAndExit }

  if ($mode -eq 'native') {
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "docker failed (exit=$LASTEXITCODE)" }
    return
  }

  $wslRepoRoot = Convert-ToWslPath $repoRoot
  $joined = ($dockerArgs | ForEach-Object { "'" + ($_ -replace "'", "'\\''") + "'" }) -join ' '
  Invoke-WslBash "cd '$wslRepoRoot' && docker $joined"
}

function Test-HttpOk([string]$url, [int]$timeoutSec = 2) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $timeoutSec $url -ErrorAction Stop
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Wait-HttpOk([string]$url, [int]$timeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk $url 2) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Ensure-ComposeCore() {
  Write-Host 'Starting core services via Docker Compose (db, redis, app)...'

  $redisCandidatePorts = @($RedisPort)
  if (-not $script:UserSpecifiedRedisPort) {
    # Common alternatives; deterministic so teammates can share notes.
    $redisCandidatePorts = @(6379, 16379, 26379)
  }

  $apiCandidatePorts = @($ApiPort)
  if (-not $script:UserSpecifiedApiPort) {
    $apiCandidatePorts = @(8000, 18000, 28000)
  }

  $redisIdx = 0
  $apiIdx = 0
  $attempts = 0
  $maxAttempts = $redisCandidatePorts.Count * $apiCandidatePorts.Count
  $startedOk = $false

  while ($attempts -lt $maxAttempts) {
    $attempts++
    $currentRedisPort = [int]$redisCandidatePorts[$redisIdx]
    $currentApiPort = [int]$apiCandidatePorts[$apiIdx]

    $env:GEO_REDIS_PORT = "$currentRedisPort"
    $env:GEO_API_PORT = "$currentApiPort"

    try {
      # Always rebuild so local backend changes (e.g., simulator routes) are picked up.
      Invoke-DockerCompose @('up','-d','--build','db','redis','app') | Out-Host

      $startedOk = $true

      # Persist any auto-selected ports for subsequent steps.
      $ApiPort = $currentApiPort
      $script:backendOrigin = "http://${HostName}:$ApiPort"
      $script:apiDocsUrl = "${script:backendOrigin}/docs"

      if ($currentRedisPort -ne 6379) {
        Write-Host "Redis host port set to ${currentRedisPort} (via GEO_REDIS_PORT) to avoid conflicts." -ForegroundColor DarkGray
      }
      if ($currentApiPort -ne 8000) {
        Write-Host "API host port set to ${currentApiPort} (via GEO_API_PORT) to avoid conflicts." -ForegroundColor DarkGray
      }

      break
    } catch {
      $msg = "$_"
      if ($msg -match 'Bind for 0\.0\.0\.0:(\d+) failed: port is already allocated') {
        $busyPort = [int]$Matches[1]

        if (($busyPort -eq $currentRedisPort) -and ($redisIdx + 1 -lt $redisCandidatePorts.Count)) {
          if ($script:UserSpecifiedRedisPort) {
            throw "Redis host port ${busyPort} is already in use. Pick another port via -RedisPort (or stop the process/container using it) and retry.\n$msg"
          }
          Write-Host "Redis port ${busyPort} is busy; retrying with another port..." -ForegroundColor Yellow
          $redisIdx++
          continue
        }

        if (($busyPort -eq $currentApiPort) -and ($apiIdx + 1 -lt $apiCandidatePorts.Count)) {
          if ($script:UserSpecifiedApiPort) {
            throw "API host port ${busyPort} is already in use. Pick another port via -ApiPort (or stop the process/container using it) and retry.\n$msg"
          }
          Write-Host "API port ${busyPort} is busy; retrying with another port..." -ForegroundColor Yellow
          $apiIdx++
          continue
        }

        throw "Host port ${busyPort} is already in use. Free it or override ports via -ApiPort / -RedisPort and retry.\n$msg"
      }

      throw
    }
  }

  if (-not $startedOk) {
    throw 'Failed to start docker compose services (could not find free host ports for API/Redis).'
  }

  Write-Host "Waiting for API: ${script:apiDocsUrl} ..."
  if (-not (Wait-HttpOk $script:apiDocsUrl 150)) {
    throw "API did not become ready at ${script:apiDocsUrl}. Check: docker logs geov0-app"
  }
}

function Seed-DbIfRequested() {
  if ($Community -eq 'none') {
    Write-Host 'Skipping DB seeding (Community=none).'
    return
  }

  $args = @('python', 'scripts/seed_db.py', '--source', 'fixtures', '--community', $Community)
  if ($RegenerateFixtures) {
    $args += '--regenerate-fixtures'
  }

  Write-Host "Seeding DB from fixtures: community=${Community} ..."
  Invoke-Docker (@('exec','geov0-app') + $args) | Out-Host
}

function Stop-LocalUvicornServers() {
  # Stop local uvicorn processes (app.main:app) running outside Docker.
  # These are typically started by run_local.ps1.
  
  Write-Host 'Stopping local uvicorn servers...'
  
  $stoppedCount = 0
  $commonPorts = @(8000, 18000, 28000)
  
  try {
    $pythonProcesses = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    
    foreach ($proc in $pythonProcesses) {
      $cmdLine = $proc.CommandLine
      if (-not $cmdLine) { continue }
      
      # Match uvicorn running app.main:app
      if ($cmdLine -match 'uvicorn.*app\.main:app') {
        try {
          Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
          $stoppedCount++
          Write-Host " Stopped PID $($proc.ProcessId)" -ForegroundColor Yellow
        } catch {
          Write-Host " Failed to stop PID $($proc.ProcessId): $_" -ForegroundColor Red
        }
      }
    }
  } catch {
    Write-Host " Could not enumerate Python processes: $_" -ForegroundColor DarkGray
  }
  
  # Also check by port listening (in case command line is not available)
  foreach ($port in $commonPorts) {
    try {
      $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($conn -and $conn.OwningProcess) {
        $pid = $conn.OwningProcess
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -and $proc.CommandLine -match 'uvicorn.*app\.main:app') {
          try {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            $stoppedCount++
            Write-Host " Stopped PID $pid (port $port)" -ForegroundColor Yellow
          } catch {}
        }
      }
    } catch {}
  }
  
  if ($stoppedCount -eq 0) {
    Write-Host ' No local uvicorn servers found running.' -ForegroundColor DarkGray
  } else {
    Write-Host " Stopped $stoppedCount uvicorn process(es)." -ForegroundColor Green
  }
}

function Stop-ViteDevServers() {
  # Stop Node.js processes running Vite for simulator-ui and admin-ui.
  # Identifies processes by command line containing vite.js and project paths.
  
  Write-Host 'Stopping Vite dev servers (simulator-ui, admin-ui)...'
  
  $stoppedCount = 0
  
  try {
    $nodeProcesses = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue
    
    foreach ($proc in $nodeProcesses) {
      $cmdLine = $proc.CommandLine
      if (-not $cmdLine) { continue }
      
      # Match vite processes for simulator-ui or admin-ui
      $isSimulatorUiVite = $cmdLine -match 'simulator-ui[\\/]v2[\\/].*vite' -or $cmdLine -match 'simulator-ui[\\/]v2[\\/]node_modules'
      $isAdminUiVite = $cmdLine -match 'admin-ui[\\/].*vite' -or $cmdLine -match 'admin-ui[\\/]node_modules[\\/].*vite'
      $isNpmDevServer = ($cmdLine -match 'npm.*run\s+dev.*--port\s+(5173|5176|5174|5175)') -or ($cmdLine -match 'npm-cli\.js.*run\s+dev')
      # Also match Playwright test server for admin-ui/simulator-ui
      $isPlaywrightTestServer = $cmdLine -match '(admin-ui|simulator-ui)[\\/].*@playwright[\\/]test[\\/]cli\.js\s+test-server'
      
      if ($isSimulatorUiVite -or $isAdminUiVite -or $isNpmDevServer -or $isPlaywrightTestServer) {
        try {
          Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
          $stoppedCount++
          Write-Host " Stopped PID $($proc.ProcessId)" -ForegroundColor Yellow
        } catch {
          Write-Host " Failed to stop PID $($proc.ProcessId): $_" -ForegroundColor Red
        }
      }
    }
  } catch {
    Write-Host " Could not enumerate Node processes: $_" -ForegroundColor DarkGray
  }
  
  if ($stoppedCount -eq 0) {
    Write-Host ' No Vite dev servers found running.' -ForegroundColor DarkGray
  } else {
    Write-Host " Stopped $stoppedCount Vite dev server process(es)." -ForegroundColor Green
  }
}

function Start-SimulatorUiReal() {
  if ($NoSimulatorUi) {
    Write-Host 'Skipping Simulator UI (NoSimulatorUi=1).'
    return
  }

  $uiScript = Join-Path $repoRoot 'scripts/run_simulator_ui.ps1'
  Write-Host ''
  Write-Host 'Starting Simulator UI v2 (Real Mode)...'
  Write-Host "Backend origin (for proxy): ${script:backendOrigin}"
  Write-Host "Open: http://${HostName}:${SimulatorUiPort}/?mode=real"
  Write-Host 'If port 5176 is unavailable, Vite will pick another and print the actual URL.' -ForegroundColor DarkGray
  Write-Host ''

  # Print links before starting Vite (it runs in foreground).
  Write-UsefulLinks -IncludeSimulatorUi

  # Run in the current terminal (foreground) so logs are visible.
  & $uiScript -Port $SimulatorUiPort -HostName $HostName -Mode real -BackendOrigin $script:backendOrigin
}

if ($Action -eq 'doctor') {
  $mode = Get-DockerMode
  if ($mode -eq 'missing') { Warn-MissingDockerAndExit }
  Write-Host '== Doctor =='
  Write-Host "Repo: ${repoRoot}"
  Write-Host "API expected: ${script:backendOrigin}"
  Write-Host "Docker mode: ${mode}" -ForegroundColor DarkGray

  $coreOk = Test-HttpOk $apiDocsUrl 2
  if ($coreOk) { Write-Host 'API ready: yes' } else { Write-Host 'API ready: no' }

  exit 0
}

if ($Action -eq 'stop') {
  Write-Host 'Stopping all simulator services...'
  
  # 1. Stop Vite dev servers (simulator-ui, admin-ui) - no Docker required
  Stop-ViteDevServers
  
  # 2. Stop local uvicorn servers (started by run_local.ps1)
  Stop-LocalUvicornServers
  
  # 3. Stop Docker containers if Docker is available
  $mode = Get-DockerMode
  if ($mode -ne 'missing') {
    Write-Host 'Stopping Docker containers (app, redis, db)...'
    try { Invoke-DockerCompose @('stop','app','redis','db') | Out-Host } catch {}
  } else {
    Write-Host 'Docker not available, skipping container stop.' -ForegroundColor DarkGray
  }
  
  Write-Host 'All simulator services stopped.' -ForegroundColor Green
  exit 0
}

if ($Action -eq 'seed') {
  Ensure-ComposeCore
  Seed-DbIfRequested
  Write-Host 'OK: seed completed.'
  Write-UsefulLinks
  exit 0
}

# Action=start
Ensure-ComposeCore
Seed-DbIfRequested
Start-SimulatorUiReal

if ($NoSimulatorUi) {
  Write-Host 'OK: services started (UI skipped).'
  Write-UsefulLinks
}
