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
$runRealOwnershipDir = Join-Path $stateDir 'run-real-simulator'
$runRealSimulatorOwnershipPath = Join-Path $runRealOwnershipDir 'simulator-ui.owner.json'
$runRealSimulatorOutLog = Join-Path $runRealOwnershipDir 'simulator-ui.out.log'
$runRealSimulatorErrLog = Join-Path $runRealOwnershipDir 'simulator-ui.err.log'

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

function Get-ProcessStartTimeFingerprint {
  param([int]$Id)
  $identity = Get-ProcessIdentityObservation -Id $Id -ExpectedStartFingerprint ''
  if ($identity.Status -in @('Missing', 'Unreadable')) { return $null }
  return $identity.Fingerprint
}

function Stop-ProcessById {
  param([int]$Id, [string]$ExpectedStartFingerprint)
  $identity = Get-ProcessIdentityObservation -Id $Id -ExpectedStartFingerprint $ExpectedStartFingerprint
  if ($identity.Status -eq 'Missing') { return }
  if ($identity.Status -ne 'Exact') {
    throw "Refusing to stop PID $Id because its exact process identity cannot be confirmed."
  }
  Stop-Process -Id $Id -Force -ErrorAction Stop
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
      RepositoryIdentity = [string]$metadata.repository_identity
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
    if (-not $metadata.Valid -or
      $metadata.RepositoryIdentity -ne (Get-LauncherLifecycleLockName -RepositoryRoot $repoRoot) -or
      $metadata.ServiceName -ne $serviceName -or $metadata.Port -le 0) {
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

function Get-RunningComposeServices() {
  $mode = Get-DockerMode
  if ($mode -eq 'missing') { Warn-MissingDockerAndExit }
  $composeArgs = @('--ansi','never') + $script:DevComposeFiles + @('ps','--status','running','--services')
  if ($mode -eq 'native') {
    $out = & docker compose @composeArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("docker compose ps failed (exit=$LASTEXITCODE)`n" + ($out | Out-String)) }
    return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  }

  $wslRepoRoot = Convert-ToWslPath $repoRoot
  $joined = ($composeArgs | ForEach-Object { "'" + ($_ -replace "'", "'\\''") + "'" }) -join ' '
  $out = Invoke-WslBash "cd '$wslRepoRoot' && docker compose $joined"
  return @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

function Stop-ComposeServicesStartedThisAttempt {
  param([string[]]$BaselineServices)
  $baseline = @{}
  foreach ($serviceName in @($BaselineServices)) { $baseline[[string]$serviceName] = $true }
  $started = @(Get-RunningComposeServices | Where-Object {
    $_ -in @('db','redis','app') -and -not $baseline.ContainsKey([string]$_)
  })
  if ($started.Count -gt 0) {
    Invoke-DockerCompose (@('stop') + $started) | Out-Host
  }
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

function Get-RunRealRepositoryIdentity {
  return Get-LauncherLifecycleLockName -RepositoryRoot $repoRoot
}

function Get-RunRealSimulatorService {
  return [pscustomobject]@{
    Name = 'Simulator UI'
    OwnershipFile = $runRealSimulatorOwnershipPath
    Port = $SimulatorUiPort
  }
}

function Get-RunRealOwnershipMetadata {
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
      RepositoryIdentity = [string]$metadata.repository_identity
      ServiceName = [string]$metadata.service_name
      Port = [int]$metadata.port
      Pid = $pidValue
      ProcessStartFingerprint = $fingerprint
    }
  } catch {
    return [pscustomobject]@{ Valid = $false }
  }
}

function Write-RunRealOwnershipMetadata {
  param([object]$Service, [int]$Id, [string]$ProcessStartFingerprint)
  if ($ProcessStartFingerprint -notmatch '^utc-ticks:\d+$') { throw 'Simulator UI process fingerprint is unavailable.' }
  $metadata = [ordered]@{
    version = 1
    repository_identity = Get-RunRealRepositoryIdentity
    service_name = [string]$Service.Name
    port = [int]$Service.Port
    pid = $Id
    process_start_fingerprint = $ProcessStartFingerprint
    recorded_at_utc = (Get-Date).ToUniversalTime().ToString('o')
  }
  $tempPath = "$($Service.OwnershipFile).tmp.$([Guid]::NewGuid().ToString('N'))"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  try {
    [System.IO.File]::WriteAllText($tempPath, ($metadata | ConvertTo-Json -Compress), $utf8NoBom)
    Move-Item -LiteralPath $tempPath -Destination $Service.OwnershipFile -Force
  } finally {
    if (Test-Path -LiteralPath $tempPath) { Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue }
  }
}

function Get-RunRealOwnershipState {
  param([object]$Service)
  $metadata = Get-RunRealOwnershipMetadata -Path $Service.OwnershipFile
  $metadataPresent = $null -ne $metadata
  $metadataMatches = [bool](
    $metadataPresent -and $metadata.Valid -and
    $metadata.RepositoryIdentity -eq (Get-RunRealRepositoryIdentity) -and
    $metadata.ServiceName -eq $Service.Name -and $metadata.Port -gt 0
  )
  $effectivePort = if ($metadataMatches) { $metadata.Port } else { $Service.Port }
  $listener = Get-ListeningPid -Port $effectivePort
  $identity = if ($metadataMatches) {
    Get-ProcessIdentityObservation -Id $metadata.Pid -ExpectedStartFingerprint $metadata.ProcessStartFingerprint
  } else {
    [pscustomobject]@{ Status = $(if ($metadataPresent) { 'Unreadable' } else { 'Missing' }); Fingerprint = $null }
  }
  $owned = [bool]($metadataMatches -and $identity.Status -eq 'Exact' -and $listener -eq $metadata.Pid)
  $conflict = $false
  $reason = $null
  $staleOwnershipFile = $false
  if ($listener -and -not $metadataPresent) {
    $conflict = $true
    $reason = "port $effectivePort has listener PID $listener without exact run_real ownership metadata"
  } elseif ($metadataPresent -and -not $metadataMatches) {
    $conflict = $true
    $reason = 'run_real ownership metadata is invalid or belongs to another repository/service'
  } elseif ($listener -and -not $owned) {
    $conflict = $true
    $reason = "listener PID $listener does not match the exact owned process instance"
  } elseif (-not $listener -and $identity.Status -in @('Exact', 'Unreadable')) {
    $conflict = $true
    $reason = "owned PID $($metadata.Pid) is alive or unreadable but is not the expected listener"
  } elseif (-not $listener -and $metadataPresent -and $identity.Status -in @('Missing', 'Mismatch')) {
    $staleOwnershipFile = $true
  }
  return [pscustomobject]@{
    Service = $Service
    MetadataPresent = $metadataPresent
    SavedPid = if ($metadataMatches) { $metadata.Pid } else { $null }
    SavedProcessStartFingerprint = if ($metadataMatches) { $metadata.ProcessStartFingerprint } else { $null }
    ListenerPid = $listener
    EffectivePort = $effectivePort
    Owned = $owned
    Conflict = $conflict
    Reason = $reason
    StaleOwnershipFile = $staleOwnershipFile
  }
}

function Invoke-RunRealOwnershipStopPlan {
  param([object]$Service)
  $initial = Get-RunRealOwnershipState -Service $Service
  if ($initial.Conflict) { throw "run_real stop refused before mutation: $($initial.Reason)" }
  $final = Get-RunRealOwnershipState -Service $Service
  if ($final.Conflict) { throw "run_real stop refused during final preflight: $($final.Reason)" }
  foreach ($property in @('SavedPid','SavedProcessStartFingerprint','ListenerPid','EffectivePort','Owned','StaleOwnershipFile')) {
    if ($initial.$property -ne $final.$property) { throw 'run_real stop refused because ownership changed during final preflight.' }
  }
  if ($final.Owned) {
    $current = Get-RunRealOwnershipState -Service $Service
    if (-not $current.Owned -or $current.SavedPid -ne $final.SavedPid -or
      $current.SavedProcessStartFingerprint -ne $final.SavedProcessStartFingerprint) {
      throw 'run_real stop refused because Simulator UI ownership changed before execution.'
    }
    Stop-ProcessById -Id $final.SavedPid -ExpectedStartFingerprint $final.SavedProcessStartFingerprint
    Remove-Item -LiteralPath $Service.OwnershipFile -Force -ErrorAction Stop
  } elseif ($final.StaleOwnershipFile) {
    Remove-Item -LiteralPath $Service.OwnershipFile -Force -ErrorAction Stop
  }
}

function Wait-ForPortToBeFree {
  param([int]$Port, [int]$TimeoutSec = 10)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-ListeningPid -Port $Port)) { return $true }
    Start-Sleep -Milliseconds 300
  }
  return (-not (Get-ListeningPid -Port $Port))
}

function Assert-RunRealOwnershipForReplacement {
  param([object]$Service)
  $state = Get-RunRealOwnershipState -Service $Service
  if ($state.Conflict) { throw "run_real start refused before mutation: $($state.Reason)" }
  return $state
}

function Wait-ForRunRealSimulatorOwnership {
  param([object]$Service, [object]$Process, [int]$TimeoutSec = 60)
  if ($null -eq $Process -or -not $Process.Id) { throw 'Start-Process did not return the Simulator UI process.' }
  $launchedPid = [int]$Process.Id
  $fingerprint = $null
  $persisted = $false
  $ownershipResult = $null
  $primaryFailure = $null
  $cleanupFailure = $null
  try {
    $fingerprint = Get-ProcessStartTimeFingerprint -Id $launchedPid
    if ([string]::IsNullOrWhiteSpace($fingerprint)) { throw 'Simulator UI process identity is unavailable.' }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
      $identity = Get-ProcessIdentityObservation -Id $launchedPid -ExpectedStartFingerprint $fingerprint
      if ($identity.Status -ne 'Exact') { throw 'Simulator UI process exited or changed during startup.' }
      $listener = Get-ListeningPid -Port $Service.Port
      if ($listener) {
        if ($listener -ne $launchedPid) { throw "Simulator UI listener PID $listener is not launched PID $launchedPid." }
        Write-RunRealOwnershipMetadata -Service $Service -Id $launchedPid -ProcessStartFingerprint $fingerprint
        $persisted = $true
        $ownershipResult = [pscustomobject]@{ Pid = $launchedPid; ProcessStartFingerprint = $fingerprint }
        break
      }
      Start-Sleep -Milliseconds 500
    }
    if (-not $persisted) {
      throw "Simulator UI did not listen on port $($Service.Port) within $TimeoutSec seconds."
    }
  } catch {
    $primaryFailure = $_.Exception
  } finally {
    if (-not $persisted) {
      try {
        if ([string]::IsNullOrWhiteSpace($fingerprint)) {
          if (-not $Process.HasExited) { $Process.Kill() }
        } else {
          $identity = Get-ProcessIdentityObservation -Id $launchedPid -ExpectedStartFingerprint $fingerprint
          if ($identity.Status -eq 'Exact') { Stop-ProcessById -Id $launchedPid -ExpectedStartFingerprint $fingerprint }
        }
      } catch {
        $cleanupFailure = $_.Exception
        if ($null -eq $primaryFailure) {
          Write-Warning "Unable to clean up directly launched Simulator UI process: $($cleanupFailure.Message)"
        }
      }
    }
  }

  if ($null -ne $primaryFailure -and $null -ne $cleanupFailure) {
    $combinedMessage = "$($primaryFailure.Message) Startup cleanup also failed: $($cleanupFailure.Message)"
    $combinedFailure = New-Object System.InvalidOperationException($combinedMessage, $primaryFailure)
    $combinedFailure.Data['CleanupFailure'] = $cleanupFailure.ToString()
    throw $combinedFailure
  }
  if ($null -ne $primaryFailure) { throw $primaryFailure }
  if ($null -ne $cleanupFailure) { throw $cleanupFailure }
  return $ownershipResult
}

function Get-SimulatorUiRealTools() {
  $appDir = Join-Path $repoRoot 'simulator-ui/v2'
  $viteCli = Join-Path $appDir 'node_modules/vite/bin/vite.js'
  if (-not (Test-Path -LiteralPath (Join-Path $appDir 'node_modules'))) {
    Write-Host 'Installing Simulator UI dependencies...'
    & npm --prefix $appDir install
    if ($LASTEXITCODE -ne 0) { throw "Simulator UI npm install failed (exit=$LASTEXITCODE)." }
  }
  if (-not (Test-Path -LiteralPath $viteCli -PathType Leaf)) { throw "Vite executable not found: $viteCli" }
  $node = Get-Command node -ErrorAction Stop
  return [pscustomobject]@{ AppDir = $appDir; ViteCli = $viteCli; Node = $node.Source }
}

function Start-SimulatorUiReal {
  param([object]$UiTools)
  Write-Host ''
  Write-Host 'Starting Simulator UI v2 (Real Mode)...'
  Write-Host "Backend origin (for proxy): ${script:backendOrigin}"
  Write-Host "Open: http://${HostName}:${SimulatorUiPort}/?mode=real"
  Write-Host ''
  $env:VITE_API_MODE = 'real'
  $env:VITE_GEO_BACKEND_ORIGIN = $script:backendOrigin
  $arguments = @("`"$($UiTools.ViteCli)`"", '--host', $HostName, '--port', "$SimulatorUiPort", '--strictPort')
  $process = Start-Process -FilePath $UiTools.Node -ArgumentList $arguments -WorkingDirectory $UiTools.AppDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $runRealSimulatorOutLog -RedirectStandardError $runRealSimulatorErrLog
  $service = Get-RunRealSimulatorService
  $ownership = Wait-ForRunRealSimulatorOwnership -Service $service -Process $process
  return [pscustomobject]@{
    Service = $service
    Pid = $ownership.Pid
    ProcessStartFingerprint = $ownership.ProcessStartFingerprint
  }
}

function Undo-RunRealStartedUi {
  param([object[]]$StartedUi)
  $failures = @()
  for ($index = $StartedUi.Count - 1; $index -ge 0; $index--) {
    $started = $StartedUi[$index]
    try {
      $identity = Get-ProcessIdentityObservation -Id $started.Pid -ExpectedStartFingerprint $started.ProcessStartFingerprint
      if ($identity.Status -eq 'Exact') {
        Stop-ProcessById -Id $started.Pid -ExpectedStartFingerprint $started.ProcessStartFingerprint
      } elseif ($identity.Status -eq 'Unreadable') {
        throw 'process identity is unreadable'
      }
      if (Test-Path -LiteralPath $started.Service.OwnershipFile) {
        $metadata = Get-RunRealOwnershipMetadata -Path $started.Service.OwnershipFile
        $matches = [bool]($metadata -and $metadata.Valid -and
          $metadata.RepositoryIdentity -eq (Get-RunRealRepositoryIdentity) -and
          $metadata.ServiceName -eq $started.Service.Name -and
          $metadata.Port -eq $started.Service.Port -and
          $metadata.Pid -eq $started.Pid -and
          $metadata.ProcessStartFingerprint -eq $started.ProcessStartFingerprint)
        if (-not $matches) { throw 'ownership metadata changed' }
        Remove-Item -LiteralPath $started.Service.OwnershipFile -Force -ErrorAction Stop
      }
    } catch {
      $failures += "$($started.Service.Name): $($_.Exception.Message)"
    }
  }
  if ($failures.Count -gt 0) { throw "run_real UI rollback failed: $($failures -join '; ')" }
}

function Invoke-RunRealStartupRollback {
  param(
    [System.Exception]$PrimaryFailure,
    [object[]]$StartedUi,
    [string[]]$BaselineComposeServices
  )
  $rollbackFailures = @()
  try { Undo-RunRealStartedUi -StartedUi $StartedUi } catch { $rollbackFailures += $_.Exception.Message }
  try { Stop-ComposeServicesStartedThisAttempt -BaselineServices $BaselineComposeServices } catch { $rollbackFailures += $_.Exception.Message }
  if ($rollbackFailures.Count -gt 0) {
    $rollbackFailure = $rollbackFailures -join '; '
    $combined = New-Object System.Exception("$($PrimaryFailure.Message) Rollback failed: $rollbackFailure", $PrimaryFailure)
    $combined.Data['RollbackFailure'] = $rollbackFailure
    throw $combined
  }
  throw $PrimaryFailure
}

function Stop-RunRealServices {
  Invoke-RunRealOwnershipStopPlan -Service (Get-RunRealSimulatorService)
  Write-Host 'Stopping Docker containers (app, redis, db)...'
  Invoke-DockerCompose @('stop','app','redis','db') | Out-Host
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
foreach ($directory in @($stateDir, $runRealOwnershipDir)) {
  if (-not (Test-Path -LiteralPath $directory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
  }
}

if ($Action -eq 'stop') {
  Write-Host 'Stopping all simulator services...'
  Stop-RunRealServices
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

# Action=start. Complete all non-mutating/UI dependency preflight before replacing
# a healthy same-port UI. Compose additions and the new UI are rolled back on failure.
$simulatorService = Get-RunRealSimulatorService
$null = Assert-RunRealOwnershipForReplacement -Service $simulatorService
$uiTools = if ($NoSimulatorUi) { $null } else { Get-SimulatorUiRealTools }
$baselineComposeServices = @(Get-RunningComposeServices)
$startedUi = @()
try {
  Ensure-ComposeCore
  Seed-DbIfRequested
  Invoke-RunRealOwnershipStopPlan -Service $simulatorService
  if ($NoSimulatorUi) {
    Write-Host 'Skipping Simulator UI (NoSimulatorUi=1).'
    Write-Host 'OK: services started (UI skipped).'
    Write-UsefulLinks
  } else {
    if (-not (Wait-ForPortToBeFree -Port $SimulatorUiPort -TimeoutSec 10)) {
      $currentListener = Get-ListeningPid -Port $SimulatorUiPort
      throw "Simulator UI port $SimulatorUiPort did not become free after stopping the owned process (PID: $currentListener)."
    }
    $startedUi += Start-SimulatorUiReal -UiTools $uiTools
    Write-Host "Simulator UI started in background (PID $($startedUi[-1].Pid))." -ForegroundColor Green
    Write-UsefulLinks -IncludeSimulatorUi
    Write-Host 'OK: services and background Simulator UI started.'
  }
} catch {
  Invoke-RunRealStartupRollback -PrimaryFailure $_.Exception -StartedUi $startedUi -BaselineComposeServices $baselineComposeServices
}
} finally {
  Exit-LauncherLifecycleLock -LockHandle $LifecycleLock
}
