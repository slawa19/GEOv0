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
$script:DevComposeFiles = @('-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')

$script:backendOrigin = "http://${HostName}:$ApiPort"
$script:apiDocsUrl = "${script:backendOrigin}/docs"
$stateDir = Join-Path $repoRoot '.local-run'
$fullStackOwnershipDir = Join-Path $stateDir 'full-stack'
$runLocalBackendPidPath = Join-Path $stateDir 'backend.pid'
$runLocalAdminUiPidPath = Join-Path $stateDir 'admin-ui.pid'

function Get-LauncherLifecycleLockName {
  param([string]$RepositoryRoot)
  $normalizedRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd([char[]]@('\', '/')).ToUpperInvariant()
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    $digest = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalizedRoot))
  } finally {
    $sha256.Dispose()
  }
  $digestText = -join ($digest | ForEach-Object { $_.ToString('x2') })
  return "Local\GEOv0-LauncherLifecycle-$($digestText.Substring(0, 24))"
}

function Enter-LauncherLifecycleLock {
  param([string]$RepositoryRoot, [string]$LauncherName)
  $lockName = Get-LauncherLifecycleLockName -RepositoryRoot $RepositoryRoot
  $createdNew = $false
  $mutex = New-Object System.Threading.Mutex($false, $lockName, [ref]$createdNew)
  $acquired = $false
  try {
    try {
      $acquired = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
      $acquired = $true
    }
    if (-not $acquired) {
      throw "Launcher lifecycle is busy for this repository; another mutating launcher owns $lockName."
    }
    return [pscustomobject]@{ Mutex = $mutex; Name = $lockName; Launcher = $LauncherName }
  } catch {
    if (-not $acquired) { $mutex.Dispose() }
    throw
  }
}

function Exit-LauncherLifecycleLock {
  param([object]$LockHandle)
  if ($null -eq $LockHandle -or $null -eq $LockHandle.Mutex) { return }
  try {
    $LockHandle.Mutex.ReleaseMutex()
  } finally {
    $LockHandle.Mutex.Dispose()
  }
}

function Get-ProcessIdentityObservation {
  param([int]$Id, [string]$ExpectedStartFingerprint)
  if (-not $Id -or $Id -le 0) {
    return [pscustomobject]@{ Status = 'Missing'; Fingerprint = $null }
  }
  try {
    $process = [System.Diagnostics.Process]::GetProcessById($Id)
  } catch [System.ArgumentException] {
    return [pscustomobject]@{ Status = 'Missing'; Fingerprint = $null }
  } catch {
    return [pscustomobject]@{ Status = 'Unreadable'; Fingerprint = $null }
  }
  try {
    $ticks = $process.StartTime.ToUniversalTime().Ticks.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    $fingerprint = "utc-ticks:$ticks"
  } catch {
    return [pscustomobject]@{ Status = 'Unreadable'; Fingerprint = $null }
  }
  $status = if ([string]::IsNullOrWhiteSpace($ExpectedStartFingerprint)) {
    'Observed'
  } elseif ($fingerprint -eq $ExpectedStartFingerprint) {
    'Exact'
  } else {
    'Mismatch'
  }
  return [pscustomobject]@{ Status = $status; Fingerprint = $fingerprint }
}

function Get-FullStackOwnershipMetadata {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  try {
    $metadata = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop) | ConvertFrom-Json -ErrorAction Stop
    $pidValue = 0
    if (-not [int]::TryParse([string]$metadata.pid, [ref]$pidValue) -or $pidValue -le 0) { throw 'invalid pid' }
    $fingerprint = [string]$metadata.process_start_fingerprint
    if ($fingerprint -notmatch '^utc-ticks:\d+$') { throw 'invalid fingerprint' }
    return [pscustomobject]@{
      Valid = [bool]($metadata.version -eq 1)
      Pid = $pidValue
      ProcessStartFingerprint = $fingerprint
      ServiceName = [string]$metadata.service_name
      Port = [int]$metadata.port
    }
  } catch {
    return [pscustomobject]@{ Valid = $false }
  }
}

function Assert-NoActiveFullStackOwnership {
  foreach ($serviceName in @('Backend', 'Admin UI', 'Simulator UI')) {
    $fileName = switch ($serviceName) {
      'Backend' { 'backend.owner.json' }
      'Admin UI' { 'admin-ui.owner.json' }
      'Simulator UI' { 'simulator-ui.owner.json' }
    }
    $path = Join-Path $fullStackOwnershipDir $fileName
    $metadata = Get-FullStackOwnershipMetadata -Path $path
    if ($null -eq $metadata) { continue }
    if (-not $metadata.Valid -or $metadata.ServiceName -ne $serviceName -or $metadata.Port -le 0) {
      throw "$serviceName full-stack ownership metadata is unreadable or invalid; run_real_simulator refused."
    }
    $identity = Get-ProcessIdentityObservation -Id $metadata.Pid -ExpectedStartFingerprint $metadata.ProcessStartFingerprint
    if ($identity.Status -in @('Exact', 'Unreadable')) {
      throw "$serviceName full-stack ownership is active or unreadable; run_real_simulator refused."
    }
    $listener = Get-ListeningPid -Port $metadata.Port
    if ($listener) {
      throw "$serviceName full-stack metadata is stale but port $($metadata.Port) has listener PID $listener; run_real_simulator refused."
    }
  }
}

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
  $argsWithAnsi = @('--ansi','never') + $script:DevComposeFiles + $composeArgs

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

function Get-ListeningPid {
  param([int]$Port)
  try {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($connection -and $connection.OwningProcess) { return [int]$connection.OwningProcess }
  } catch {}
  return $null
}

function Get-PidFromFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  try {
    $raw = Get-Content -LiteralPath $Path -ErrorAction Stop | Select-Object -First 1
    if ([string]$raw -match '^\s*(\d+)\s*$') { return [int]$Matches[1] }
  } catch {}
  return $null
}

function Get-ProcessCommandLine {
  param([int]$Id)
  try {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction Stop
    if ($process -and $process.CommandLine) { return ([string]$process.CommandLine).Trim() }
  } catch {}
  return ''
}

function Test-IsExpectedRunLocalCommandLine {
  param([string]$CommandLine, [string]$Kind, [string]$ContextDir)
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
  $commandLineLower = $CommandLine.ToLowerInvariant()
  if ($Kind -eq 'backend') {
    return ($commandLineLower -like '*uvicorn*' -and $commandLineLower -like '*app.main:app*')
  }
  $escapedDir = [WildcardPattern]::Escape($ContextDir.ToLowerInvariant())
  return ($commandLineLower -like '*vite*' -and $commandLineLower -like "*$escapedDir*")
}

function Get-RunLocalPidStopPlan {
  param([object[]]$Services)
  $targets = @()
  $pidFilesToRemove = @()
  $conflicts = @()
  foreach ($service in $Services) {
    $pidFilePresent = Test-Path -LiteralPath $service.PidFile
    $procId = Get-PidFromFile -Path $service.PidFile
    if ($pidFilePresent -and -not $procId) {
      $conflicts += "$($service.Name): PID metadata is unreadable and was retained"
      continue
    }
    if (-not $procId) { continue }

    $identity = Get-ProcessIdentityObservation -Id $procId -ExpectedStartFingerprint ''
    if ($identity.Status -eq 'Missing') {
      $pidFilesToRemove += $service.PidFile
      continue
    }
    if ($identity.Status -eq 'Unreadable') {
      $conflicts += "$($service.Name): PID $procId identity is unreadable"
      continue
    }
    $commandLine = Get-ProcessCommandLine -Id $procId
    if (-not (Test-IsExpectedRunLocalCommandLine `
      -CommandLine $commandLine `
      -Kind $service.Kind `
      -ContextDir $service.ContextDir)) {
      $conflicts += "$($service.Name): PID $procId command line does not match; metadata was retained"
      continue
    }
    $targets += [pscustomobject]@{
      Pid = [int]$procId
      ProcessStartFingerprint = $identity.Fingerprint
      CommandLine = $commandLine
      PidFile = $service.PidFile
    }
    $pidFilesToRemove += $service.PidFile
  }
  return [pscustomobject]@{
    Targets = @($targets)
    PidFilesToRemove = @($pidFilesToRemove)
    Conflicts = @($conflicts)
  }
}

function Test-RunLocalPidStopPlansEqual {
  param([object]$InitialPlan, [object]$FinalPlan)
  if ($InitialPlan.Targets.Count -ne $FinalPlan.Targets.Count) { return $false }
  for ($index = 0; $index -lt $InitialPlan.Targets.Count; $index++) {
    foreach ($property in @('Pid', 'ProcessStartFingerprint', 'CommandLine', 'PidFile')) {
      if ($InitialPlan.Targets[$index].$property -ne $FinalPlan.Targets[$index].$property) { return $false }
    }
  }
  return (($InitialPlan.PidFilesToRemove -join "`n") -eq ($FinalPlan.PidFilesToRemove -join "`n"))
}

function Invoke-RunLocalPidStopPlan {
  param([object[]]$Services)
  $initialPlan = Get-RunLocalPidStopPlan -Services $Services
  if ($initialPlan.Conflicts.Count -gt 0) {
    throw "run_local process stop refused before any stop: $($initialPlan.Conflicts -join '; ')"
  }
  $finalPlan = Get-RunLocalPidStopPlan -Services $Services
  if ($finalPlan.Conflicts.Count -gt 0) {
    throw "run_local process stop refused during final preflight: $($finalPlan.Conflicts -join '; ')"
  }
  if (-not (Test-RunLocalPidStopPlansEqual -InitialPlan $initialPlan -FinalPlan $finalPlan)) {
    throw 'run_local process stop refused because ownership changed during final preflight.'
  }

  foreach ($target in $finalPlan.Targets) {
    $identity = Get-ProcessIdentityObservation -Id $target.Pid -ExpectedStartFingerprint $target.ProcessStartFingerprint
    $commandLine = Get-ProcessCommandLine -Id $target.Pid
    if ($identity.Status -ne 'Exact' -or $commandLine -ne $target.CommandLine) {
      throw "run_local process stop refused because PID $($target.Pid) changed before execution."
    }
  }
  foreach ($target in $finalPlan.Targets) {
    Stop-Process -Id $target.Pid -Force -ErrorAction Stop
  }
  foreach ($path in $finalPlan.PidFilesToRemove) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
  }
}

function Stop-RunLocalPidFileProcess {
  param([string]$Path, [string]$Kind, [string]$ContextDir)
  Invoke-RunLocalPidStopPlan -Services @([pscustomobject]@{
    Name = $Kind
    Kind = $Kind
    ContextDir = $ContextDir
    PidFile = $Path
  })
}

function Stop-RunLocalProcesses {
  $services = @(
    [pscustomobject]@{
      Name = 'Admin UI'
      Kind = 'ui'
      ContextDir = (Join-Path $repoRoot 'admin-ui')
      PidFile = $runLocalAdminUiPidPath
    },
    [pscustomobject]@{
      Name = 'Backend'
      Kind = 'backend'
      ContextDir = $null
      PidFile = $runLocalBackendPidPath
    }
  )
  Invoke-RunLocalPidStopPlan -Services $services
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

$LifecycleLock = $null
try {
$LifecycleLock = Enter-LauncherLifecycleLock -RepositoryRoot $repoRoot -LauncherName 'run_real_simulator'
Assert-NoActiveFullStackOwnership

if ($Action -eq 'stop') {
  Write-Host 'Stopping all simulator services...'

  # Only PID files written by run_local are eligible. Each PID is validated
  # against its expected command line before any native stop is attempted.
  Stop-RunLocalProcesses
  
  # Stop this launcher's named Docker Compose services.
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
} finally {
  Exit-LauncherLifecycleLock -LockHandle $LifecycleLock
}
