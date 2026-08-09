<#
.SYNOPSIS
    Локальный запуск Backend (FastAPI) и Admin UI (Vite) для разработки.

.DESCRIPTION
    Скрипт управляет жизненным циклом процессов разработки.
    Автоматически находит свободные порты, управляет PID-файлами и переменными окружения.
    
    Поддерживает PowerShell 5.1+ и PowerShell Core 7+.

.EXAMPLE
    .\run_local.ps1 -Action start
    Запуск всех сервисов.

.EXAMPLE
    .\run_local.ps1 -Action restart-backend
    Перезапуск только бэкенда. Для exact process ownership launcher не использует --reload.

.EXAMPLE
    .\run_local.ps1 -Action reset-db -SeedSource fixtures -FixturesCommunity riverside-town-50 -RegenerateFixtures
    Пересоздать SQLite DB и наполнить данными Riverside (50 участников).

.EXAMPLE
    .\run_local.ps1 -Action reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100 -RegenerateFixtures
    Пересоздать SQLite DB и наполнить данными Greenfield (100 участников).

.EXAMPLE
    .\run_local.ps1 -Action check-db
    Быстро проверить целостность текущей SQLite DB (.local-run/geov0.db).

.EXAMPLE
    .\run_local.ps1 -Action status -Verbose
    Показать статус сервисов с детальной информацией.

.EXAMPLE
    .\run_local.ps1 -Action cleanup-simulator -SimulatorRetentionDays 30 -DryRun
    Показать, какие simulator runs (DB + .local-run artifacts) будут удалены по retention.

.EXAMPLE
    .\run_local.ps1 -Action cleanup-simulator -SimulatorRetentionDays 30
    Удалить старые simulator runs (DB + .local-run artifacts) по retention.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'restart-backend', 'status', 'reset-db', 'check-db', 'cleanup-simulator')]
    [string]$Action = 'start',

    [int]$BackendPort = 18000,
    [int]$UiPort = 5173,

    [switch]$AutoPorts,
    [switch]$ShowWindows,
    [switch]$NoInstall,
    [switch]$ReloadBackend,

    # Seed source for SQLite dev DB: 'fixtures' uses admin-fixtures datasets (recommended for UI testing),
    # 'seeds' uses legacy seeds/*.json.
    [ValidateSet('fixtures', 'seeds')]
    [string]$SeedSource = 'fixtures',

    # When SeedSource=fixtures, optionally generate and seed a specific community pack into .local-run (no git changes).
    [ValidateSet('repo', 'greenfield-village-100', 'riverside-town-50', 'greenfield-village-100-v2', 'riverside-town-50-v2')]
    [string]$FixturesCommunity = 'repo',

    [switch]$RegenerateFixtures,

    # Simulator retention (maintenance)
    [int]$SimulatorRetentionDays = 30,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# Uvicorn reload delegates the listener to a child process, so it cannot satisfy
# this launcher's exact launched-PID/listener ownership contract. Reject before
# acquiring the lifecycle lock or stopping any healthy service.
if ($ReloadBackend) {
    throw '-ReloadBackend is not supported by exact single-process ownership. Run without this switch.'
}

# This entrypoint is explicitly for the permissive local-development profile.
# Status is observational and must not mutate even the caller's environment.
if ($Action -ne 'status') {
    $env:ENV = 'dev'
    Remove-Item Env:ENVIRONMENT -ErrorAction SilentlyContinue
}

# --- Configuration & Paths ---
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $RepoRoot '.local-run'
$FullStackOwnershipDir = Join-Path $StateDir 'full-stack'
$RunLocalOwnershipDir = Join-Path $StateDir 'run-local'
$UiDir = Join-Path $RepoRoot 'admin-ui'
$EnvLocalPath = Join-Path $UiDir '.env.local'
$DefaultDatabaseUrl = 'sqlite+aiosqlite:///./.local-run/geov0.db'
$LegacyRootDatabaseUrl = 'sqlite+aiosqlite:///./geov0.db'
$DefaultDatabasePath = Join-Path $StateDir 'geov0.db'
$LegacyRootDatabasePath = Join-Path $RepoRoot 'geov0.db'

# Versioned run_local ownership is isolated from both full-stack ownership and
# the historical bare PID files. Bare files remain read-only conflict evidence.
$BackendOwnershipPath = Join-Path $RunLocalOwnershipDir 'backend.owner.json'
$UiOwnershipPath = Join-Path $RunLocalOwnershipDir 'admin-ui.owner.json'
$LegacyBackendPidPath = Join-Path $StateDir 'backend.pid'
$LegacyUiPidPath = Join-Path $StateDir 'admin-ui.pid'
$FullStackBackendOwnershipPath = Join-Path $FullStackOwnershipDir 'backend.owner.json'
$FullStackAdminUiOwnershipPath = Join-Path $FullStackOwnershipDir 'admin-ui.owner.json'
$FullStackSimulatorUiOwnershipPath = Join-Path $FullStackOwnershipDir 'simulator-ui.owner.json'
$BackendOutLog = Join-Path $StateDir 'backend.out.log'
$BackendErrLog = Join-Path $StateDir 'backend.err.log'
$UiOutLog = Join-Path $StateDir 'admin-ui.out.log'
$UiErrLog = Join-Path $StateDir 'admin-ui.err.log'

# --- Helper Functions ---

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
        if ($acquired) {
            try { $mutex.ReleaseMutex() } catch {}
        }
        $mutex.Dispose()
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

function Get-NullCoalesce {
    <#
    .SYNOPSIS
        Замена оператора ?? для совместимости с PowerShell 5.1
    #>
    param($Value, $Default)
    if ($null -eq $Value) { return $Default }
    return $Value
}

function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if (-not $Id -or $Id -eq 0) { return }

    if (-not [string]::IsNullOrWhiteSpace($ExpectedStartFingerprint)) {
        $identity = Get-ProcessIdentityObservation -Id $Id -ExpectedStartFingerprint $ExpectedStartFingerprint
        if ($identity.Status -ne 'Exact') {
            throw "Refusing to stop PID $Id because its process identity changed after collective preflight."
        }
    }

    Write-Verbose "Stopping process with ID $Id..."
    Stop-Process -Id $Id -Force -ErrorAction Stop
}

function Get-ListeningPid {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn -and $conn.OwningProcess) { return [int]$conn.OwningProcess }
    } catch {
        # Порт свободен или нет прав
        Write-Verbose "Port $Port is not in LISTEN state or access denied."
    }
    return $null
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

function Get-RunLocalRepositoryIdentity {
    return Get-LauncherLifecycleLockName -RepositoryRoot $RepoRoot
}

function Get-RunLocalOwnershipMetadata {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $metadata = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop) |
            ConvertFrom-Json -ErrorAction Stop
        $pidValue = 0
        if (-not [int]::TryParse([string]$metadata.pid, [ref]$pidValue) -or $pidValue -le 0) {
            throw 'invalid pid'
        }
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

function Write-RunLocalOwnershipMetadata {
    param([object]$Service, [int]$Id, [string]$ProcessStartFingerprint)
    if ($ProcessStartFingerprint -notmatch '^utc-ticks:\d+$') {
        throw 'Unable to persist run_local ownership because the process fingerprint is unavailable.'
    }
    $metadata = [ordered]@{
        version = 1
        repository_identity = Get-RunLocalRepositoryIdentity
        service_name = [string]$Service.Name
        port = [int]$Service.Port
        pid = $Id
        process_start_fingerprint = $ProcessStartFingerprint
        recorded_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $json = $metadata | ConvertTo-Json -Compress
    $tempPath = "$($Service.OwnershipFile).tmp.$([Guid]::NewGuid().ToString('N'))"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($tempPath, $json, $utf8NoBom)
        Move-Item -LiteralPath $tempPath -Destination $Service.OwnershipFile -Force
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-RunLocalOwnershipState {
    param([object]$Service)
    $legacyPresent = [bool](Test-Path -LiteralPath $Service.LegacyPidFile)
    $metadata = Get-RunLocalOwnershipMetadata -Path $Service.OwnershipFile
    $metadataPresent = $null -ne $metadata
    $metadataMatches = [bool](
        $metadataPresent -and $metadata.Valid -and
        $metadata.RepositoryIdentity -eq (Get-RunLocalRepositoryIdentity) -and
        $metadata.ServiceName -eq $Service.Name -and
        $metadata.Port -gt 0
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

    if ($legacyPresent) {
        $conflict = $true
        $reason = "legacy PID evidence exists at $($Service.LegacyPidFile); it is read-only and requires explicit cleanup"
    } elseif ($listener -and -not $metadataPresent) {
        $conflict = $true
        $reason = "port $effectivePort has listener PID $listener without exact run_local ownership metadata"
    } elseif ($metadataPresent -and -not $metadataMatches) {
        $conflict = $true
        $reason = 'run_local ownership metadata is invalid or belongs to another repository/service'
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
        MetadataMatches = $metadataMatches
        SavedPid = if ($metadataMatches) { $metadata.Pid } else { $null }
        SavedProcessStartFingerprint = if ($metadataMatches) { $metadata.ProcessStartFingerprint } else { $null }
        ProcessIdentityStatus = $identity.Status
        ListenerPid = $listener
        EffectivePort = $effectivePort
        Owned = $owned
        Conflict = $conflict
        Reason = $reason
        StaleOwnershipFile = $staleOwnershipFile
        LegacyPidFilePresent = $legacyPresent
    }
}

function Get-RunLocalOwnershipPlan {
    param([object[]]$Services)
    return @($Services | ForEach-Object { Get-RunLocalOwnershipState -Service $_ })
}

function Test-RunLocalOwnershipPlansEqual {
    param([object[]]$InitialPlan, [object[]]$FinalPlan)
    if ($InitialPlan.Count -ne $FinalPlan.Count) { return $false }
    for ($index = 0; $index -lt $InitialPlan.Count; $index++) {
        foreach ($property in @(
            'MetadataPresent', 'MetadataMatches', 'SavedPid', 'SavedProcessStartFingerprint',
            'ProcessIdentityStatus', 'ListenerPid', 'EffectivePort', 'Owned', 'Conflict',
            'StaleOwnershipFile', 'LegacyPidFilePresent'
        )) {
            if ($InitialPlan[$index].$property -ne $FinalPlan[$index].$property) { return $false }
        }
    }
    return $true
}

function Invoke-RunLocalOwnershipStopPlan {
    param([object[]]$Services)
    $initialPlan = @(Get-RunLocalOwnershipPlan -Services $Services)
    $conflicts = @($initialPlan | Where-Object { $_.Conflict })
    if ($conflicts.Count -gt 0) {
        throw "run_local stop refused before any mutation: $($conflicts.Reason -join '; ')"
    }
    $finalPlan = @(Get-RunLocalOwnershipPlan -Services $Services)
    $finalConflicts = @($finalPlan | Where-Object { $_.Conflict })
    if ($finalConflicts.Count -gt 0) {
        throw "run_local stop refused during final preflight: $($finalConflicts.Reason -join '; ')"
    }
    if (-not (Test-RunLocalOwnershipPlansEqual -InitialPlan $initialPlan -FinalPlan $finalPlan)) {
        throw 'run_local stop refused because ownership changed during final preflight.'
    }

    $ownedStates = @($finalPlan | Where-Object { $_.Owned })
    foreach ($state in $ownedStates) {
        $current = Get-RunLocalOwnershipState -Service $state.Service
        if (-not $current.Owned -or $current.SavedPid -ne $state.SavedPid -or
            $current.SavedProcessStartFingerprint -ne $state.SavedProcessStartFingerprint) {
            throw "run_local stop refused because $($state.Service.Name) changed before execution."
        }
    }
    foreach ($state in $ownedStates) {
        Stop-ProcessById -Id $state.SavedPid -ExpectedStartFingerprint $state.SavedProcessStartFingerprint
    }
    foreach ($state in $ownedStates) {
        Remove-Item -LiteralPath $state.Service.OwnershipFile -Force -ErrorAction Stop
    }
    foreach ($state in @($finalPlan | Where-Object { $_.StaleOwnershipFile })) {
        Remove-Item -LiteralPath $state.Service.OwnershipFile -Force -ErrorAction Stop
    }
}

function Wait-ForRunLocalServiceOwnership {
    param([object]$Service, [object]$Process, [int]$TimeoutSec = 60)
    if ($null -eq $Process -or -not $Process.Id) {
        throw "Unable to start $($Service.Name): Start-Process did not return a process."
    }
    $launchedPid = [int]$Process.Id
    $fingerprint = $null
    $persisted = $false
    try {
        $fingerprint = Get-ProcessStartTimeFingerprint -Id $launchedPid
        if ([string]::IsNullOrWhiteSpace($fingerprint)) {
            throw "Unable to start $($Service.Name): process identity is unavailable."
        }
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $identity = Get-ProcessIdentityObservation -Id $launchedPid -ExpectedStartFingerprint $fingerprint
            if ($identity.Status -ne 'Exact') {
                throw "Unable to start $($Service.Name): launched process exited or changed."
            }
            $listener = Get-ListeningPid -Port $Service.Port
            if ($listener) {
                if ($listener -ne $launchedPid) {
                    throw "Unable to start $($Service.Name): listener PID $listener is not launched PID $launchedPid."
                }
                Write-RunLocalOwnershipMetadata -Service $Service -Id $launchedPid -ProcessStartFingerprint $fingerprint
                $persisted = $true
                return [pscustomobject]@{ Pid = $launchedPid; ProcessStartFingerprint = $fingerprint }
            }
            Start-Sleep -Milliseconds 500
        }
        throw "Unable to start $($Service.Name): listener did not appear on port $($Service.Port)."
    } finally {
        if (-not $persisted) {
            if ([string]::IsNullOrWhiteSpace($fingerprint)) {
                try {
                    if (-not $Process.HasExited) { $Process.Kill() }
                } catch {
                    Write-Warning "Unable to clean up directly launched $($Service.Name) process: $($_.Exception.Message)"
                }
            } else {
                $identity = Get-ProcessIdentityObservation -Id $launchedPid -ExpectedStartFingerprint $fingerprint
                if ($identity.Status -eq 'Exact') {
                    Stop-ProcessById -Id $launchedPid -ExpectedStartFingerprint $fingerprint
                }
            }
        }
    }
}

function Undo-RunLocalStartedServices {
    param([object[]]$StartedServices)
    $cleanupFailures = @()
    for ($index = $StartedServices.Count - 1; $index -ge 0; $index--) {
        $started = $StartedServices[$index]
        try {
            $identity = Get-ProcessIdentityObservation `
                -Id $started.Pid `
                -ExpectedStartFingerprint $started.ProcessStartFingerprint
            if ($identity.Status -eq 'Exact') {
                Stop-ProcessById `
                    -Id $started.Pid `
                    -ExpectedStartFingerprint $started.ProcessStartFingerprint
            } elseif ($identity.Status -eq 'Unreadable') {
                throw "Unable to roll back $($started.Service.Name): process identity is unreadable."
            }

            if (Test-Path -LiteralPath $started.Service.OwnershipFile) {
                $metadata = Get-RunLocalOwnershipMetadata -Path $started.Service.OwnershipFile
                $metadataMatchesAttempt = [bool](
                    $metadata -and $metadata.Valid -and
                    $metadata.RepositoryIdentity -eq (Get-RunLocalRepositoryIdentity) -and
                    $metadata.ServiceName -eq $started.Service.Name -and
                    $metadata.Port -eq $started.Service.Port -and
                    $metadata.Pid -eq $started.Pid -and
                    $metadata.ProcessStartFingerprint -eq $started.ProcessStartFingerprint
                )
                if (-not $metadataMatchesAttempt) {
                    throw "Unable to roll back $($started.Service.Name): ownership metadata changed."
                }
                Remove-Item -LiteralPath $started.Service.OwnershipFile -Force -ErrorAction Stop
            }
        } catch {
            $cleanupFailures += "$($started.Service.Name): $($_.Exception.Message)"
        }
    }
    if ($cleanupFailures.Count -gt 0) {
        throw "run_local startup rollback failed: $($cleanupFailures -join '; ')"
    }
}

function Invoke-RunLocalStartupRollback {
    param([System.Exception]$PrimaryFailure, [object[]]$StartedServices)
    $rollbackFailure = $null
    if ($StartedServices.Count -gt 0) {
        try {
            Undo-RunLocalStartedServices -StartedServices $StartedServices
        } catch {
            $rollbackFailure = $_.Exception
        }
    }
    if ($null -ne $rollbackFailure) {
        $combined = New-Object System.Exception("$($PrimaryFailure.Message) $($rollbackFailure.Message)", $PrimaryFailure)
        $combined.Data['RollbackFailure'] = $rollbackFailure.ToString()
        throw $combined
    }
    throw $PrimaryFailure
}

function Get-RunLocalProcessServices {
    param(
        [switch]$BackendOnly,
        [int]$SelectedBackendPort = $BackendPort,
        [int]$SelectedUiPort = $UiPort
    )
    $services = @()
    if (-not $BackendOnly) {
        $services += [pscustomobject]@{
            Name = 'Admin UI'
            OwnershipFile = $UiOwnershipPath
            LegacyPidFile = $LegacyUiPidPath
            Port = $SelectedUiPort
        }
    }
    $services += [pscustomobject]@{
        Name = 'Backend'
        OwnershipFile = $BackendOwnershipPath
        LegacyPidFile = $LegacyBackendPidPath
        Port = $SelectedBackendPort
    }
    return $services
}

function Get-FullStackOwnershipMetadata {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $metadata = (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop) |
            ConvertFrom-Json -ErrorAction Stop
        $pidValue = 0
        if (-not [int]::TryParse([string]$metadata.pid, [ref]$pidValue) -or $pidValue -le 0) {
            throw 'invalid pid'
        }
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

function Get-FullStackOwnershipServices {
    return @(
        [pscustomobject]@{ Name = 'Backend'; OwnershipFile = $FullStackBackendOwnershipPath },
        [pscustomobject]@{ Name = 'Admin UI'; OwnershipFile = $FullStackAdminUiOwnershipPath },
        [pscustomobject]@{ Name = 'Simulator UI'; OwnershipFile = $FullStackSimulatorUiOwnershipPath }
    )
}

function Get-FullStackOwnershipState {
    param([object]$Service)
    $metadata = Get-FullStackOwnershipMetadata -Path $Service.OwnershipFile
    if ($null -eq $metadata) {
        return [pscustomobject]@{
            Service = $Service
            Conflict = $false
            Reason = $null
            StaleOwnershipFile = $false
        }
    }
    if (-not $metadata.Valid -or
        $metadata.RepositoryIdentity -ne (Get-RunLocalRepositoryIdentity) -or
        $metadata.ServiceName -ne $Service.Name -or $metadata.Port -le 0) {
        return [pscustomobject]@{
            Service = $Service
            Conflict = $true
            Reason = 'full-stack ownership metadata is unreadable or invalid and was retained'
            StaleOwnershipFile = $false
        }
    }

    $identity = Get-ProcessIdentityObservation `
        -Id $metadata.Pid `
        -ExpectedStartFingerprint $metadata.ProcessStartFingerprint
    $listener = Get-ListeningPid -Port $metadata.Port
    $conflict = $false
    $reason = $null
    if ($identity.Status -eq 'Exact' -and $listener -eq $metadata.Pid) {
        $conflict = $true
        $reason = "full-stack owns exact PID $($metadata.Pid) on port $($metadata.Port)"
    } elseif ($identity.Status -eq 'Exact') {
        $conflict = $true
        $reason = "full-stack exact PID $($metadata.Pid) is alive but not its expected listener"
    } elseif ($identity.Status -eq 'Unreadable') {
        $conflict = $true
        $reason = 'full-stack process identity is unreadable; metadata was retained'
    } elseif ($listener) {
        $conflict = $true
        $reason = "full-stack metadata is stale but port $($metadata.Port) has listener PID $listener"
    }
    $staleOwnershipFile = [bool](
        -not $conflict -and -not $listener -and
        $identity.Status -in @('Missing', 'Mismatch')
    )
    return [pscustomobject]@{
        Service = $Service
        Conflict = $conflict
        Reason = $reason
        StaleOwnershipFile = $staleOwnershipFile
    }
}

function Assert-NoActiveFullStackOwnership {
    $states = @(Get-FullStackOwnershipServices | ForEach-Object {
        Get-FullStackOwnershipState -Service $_
    })
    $conflicts = @($states | Where-Object { $_.Conflict })
    foreach ($state in $conflicts) {
        Write-Warning "$($state.Service.Name): $($state.Reason). run_local did not stop or adopt it."
    }
    if ($conflicts.Count -gt 0) {
        throw 'run_local refused because full-stack ownership is active or cannot be safely disproven.'
    }

    foreach ($state in @($states | Where-Object { $_.StaleOwnershipFile })) {
        # Re-observe immediately before cleanup. A newly active, replaced, or
        # unreadable owner must win over the earlier stale observation.
        $finalState = Get-FullStackOwnershipState -Service $state.Service
        if ($finalState.Conflict -or -not $finalState.StaleOwnershipFile) {
            throw "run_local refused because full-stack ownership changed during stale cleanup for $($state.Service.Name)."
        }
        Remove-Item -LiteralPath $state.Service.OwnershipFile -Force -ErrorAction Stop
        Write-Warning "$($state.Service.Name): removed full-stack metadata only after its process identity was proven stale."
    }
}

function Wait-ForPortToBeFree {
    param(
        [int]$Port,
        [int]$TimeoutSec = 8
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-ListeningPid -Port $Port)) {
            return $true
        }
        Start-Sleep -Milliseconds 300
    }
    return (-not (Get-ListeningPid -Port $Port))
}

function Get-FreePort {
    param(
        [int]$StartPort,
        [int]$MaxPort,
        [string]$Kind,
        [string]$ContextDir
    )

    for ($p = $StartPort; $p -le $MaxPort; $p++) {
        $listener = Get-ListeningPid -Port $p
        if (-not $listener) { return $p }
    }
    throw "No free port found for $Kind in range $StartPort..$MaxPort"
}

function Test-HttpEndpoint {
    param([string]$Url, [int]$TimeoutSec = 45)
    
    Write-Host "Waiting for $Url..." -NoNewline
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    
    while ((Get-Date) -lt $deadline) {
        $client = $null
        try {
            # Попытка быстрого TCP соединения (обход прокси и быстрее чем WebRequest)
            $uri = [Uri]$Url
            $client = New-Object System.Net.Sockets.TcpClient
            $connect = $client.BeginConnect($uri.Host, $uri.Port, $null, $null)
            $waitResult = $connect.AsyncWaitHandle.WaitOne(500)
            
            if ($waitResult) {
                try {
                    $client.EndConnect($connect)
                    Write-Host " OK"
                    return $true
                } catch {
                    # Соединение было отклонено - сервер ещё не готов
                    Write-Verbose "Connection rejected: $_"
                }
            }
        } catch {
            # Игнорируем ошибки соединения
            Write-Verbose "Connection attempt failed: $_"
        } finally {
            # Гарантированно закрываем клиент
            if ($null -ne $client) {
                try { $client.Close() } catch { }
                try { $client.Dispose() } catch { }
            }
        }
        
        Start-Sleep -Milliseconds 500
        Write-Host "." -NoNewline
    }
    Write-Host " Timeout!"
    return $false
}

function Update-EnvLocal {
    param([string]$Path, [string]$BaseUrl)
    
    $content = @()
    if (Test-Path $Path) {
        $content = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    }

    $newContent = @()
    $hasMode = $false
    $hasBase = $false

    foreach ($line in $content) {
        if ($line -match '^\s*VITE_API_MODE\s*=') {
            $newContent += 'VITE_API_MODE=real'
            $hasMode = $true
        } elseif ($line -match '^\s*VITE_API_BASE_URL\s*=') {
            $newContent += "VITE_API_BASE_URL=$BaseUrl"
            $hasBase = $true
        } else {
            $newContent += $line
        }
    }

    if (-not $hasMode) { $newContent += 'VITE_API_MODE=real' }
    if (-not $hasBase) { $newContent += "VITE_API_BASE_URL=$BaseUrl" }

    # Используем UTF8 без BOM для совместимости с .env парсерами
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $newContent, $utf8NoBom)
    Write-Verbose "Updated $Path with API base URL: $BaseUrl"
}

function Get-ProjectTools {
    # Ищем python в venv
    $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        # Fallback для Linux/Mac (если скрипт будет портирован через pwsh)
        $venvPython = Join-Path $RepoRoot '.venv/bin/python' 
    }
    
    if (-not (Test-Path $venvPython)) {
        throw "Python virtual environment not found. Expected at: $venvPython`nRun: python -m venv .venv && .\.venv\Scripts\activate && pip install -r requirements.txt"
    }

    # Проверяем Node/NPM
    $node = Get-Command node -ErrorAction SilentlyContinue
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    
    if (-not $node -or -not $npm) {
        throw "Node.js or npm not found in PATH. Please install Node.js 18+ from https://nodejs.org/"
    }

    return @{
        Python = $venvPython
        Node = $node.Source
        Npm = $npm.Source
    }
}

function Invoke-PythonScript {
    <#
    .SYNOPSIS
        Безопасный запуск Python скрипта с проверкой результата
    #>
    param(
        [string]$PythonExe,
        [string]$ScriptPath,
        [string[]]$Arguments = @(),
        [string]$Description = "Python script"
    )
    
    if (-not (Test-Path $ScriptPath)) {
        throw "Script not found: $ScriptPath"
    }
    
    Write-Verbose "Running: $Description"
    Push-Location $RepoRoot
    try {
        if ($Arguments.Count -gt 0) {
            & $PythonExe $ScriptPath @Arguments
        } else {
            & $PythonExe $ScriptPath
        }
    } finally {
        Pop-Location
    }
    
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Get-EffectiveDatabaseUrl {
    param([string]$PythonExe)

    Push-Location $RepoRoot
    try {
        $output = @(& $PythonExe -c 'from app.config import Settings; print(Settings().DATABASE_URL)')
        if ($LASTEXITCODE -ne 0 -or $output.Count -ne 1) {
            throw 'Unable to resolve DATABASE_URL from the application settings.'
        }
        return ([string]$output[0]).Trim()
    } finally {
        Pop-Location
    }
}

function Get-SeedDbArgs {
    param(
        [string]$SeedSource,
        [string]$FixturesCommunity,
        [switch]$RegenerateFixtures
    )

    $cmdArgs = @('--source', $SeedSource)
    if ($SeedSource -eq 'fixtures' -and $FixturesCommunity -and $FixturesCommunity -ne 'repo') {
        $cmdArgs += @('--community', $FixturesCommunity)
        if ($RegenerateFixtures) { $cmdArgs += '--regenerate-fixtures' }
    }
    return $cmdArgs
}

function Test-DbLooksLikeTinyTestSeed {
    param(
        [string]$PythonExe,
        [string]$DbPath
    )

    if (-not (Test-Path $DbPath)) { return $false }

    $code = @"
import sqlite3
db = r'''$DbPath'''
con = sqlite3.connect(db)
cur = con.cursor()
try:
    cur.execute('select count(*) from participants')
    total = int(cur.fetchone()[0])
    cur.execute("select count(*) from participants where display_name like '%(Test)%'")
    test_named = int(cur.fetchone()[0])
    print(f"{total}|{test_named}")
except Exception:
    print("0|0")
finally:
    con.close()
"@

    try {
        $out = & $PythonExe -c $code
        $text = ($out | Out-String).Trim()
        if ($text -match '^(\d+)\|(\d+)$') {
            $total = [int]$Matches[1]
            $testNamed = [int]$Matches[2]
            return (($total -gt 0 -and $total -le 10) -or ($testNamed -gt 0))
        }
    } catch {
        return $false
    }
    return $false
}

function Invoke-NpmCommand {
    <#
    .SYNOPSIS
        Запуск npm команды с проверкой результата
    #>
    param(
        [string]$NpmExe,
        [string]$WorkingDir,
        [string[]]$Arguments,
        [string]$Description = "npm command"
    )
    
    Write-Verbose "Running npm in ${WorkingDir}: $($Arguments -join ' ')"
    Push-Location $WorkingDir
    try {
        & $NpmExe @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

# --- Main Logic ---

Write-Host ""
Write-Host "=== GEO Local Development Environment ===" -ForegroundColor Cyan
Write-Host "Action: $Action" -ForegroundColor Gray
Write-Host ""

$Tools = Get-ProjectTools
Write-Host "[OK] Python: $($Tools.Python)" -ForegroundColor Green
Write-Host "[OK] node: $($Tools.Node)" -ForegroundColor Green
Write-Host "[OK] npm: $($Tools.Npm)" -ForegroundColor Green
$Python = $Tools.Python
$WindowStyle = if ($ShowWindows) { 'Normal' } else { 'Hidden' }

$LifecycleLock = $null
$RequiresLifecycleLock = [bool](
    $Action -in @('start', 'stop', 'restart', 'restart-backend', 'reset-db') -or
    ($Action -eq 'cleanup-simulator' -and -not $DryRun)
)
try {
if ($RequiresLifecycleLock) {
    $LifecycleLock = Enter-LauncherLifecycleLock `
        -RepositoryRoot $RepoRoot `
        -LauncherName 'run_local'
}

if ($RequiresLifecycleLock) {
    Assert-NoActiveFullStackOwnership
    foreach ($directory in @($StateDir, $RunLocalOwnershipDir)) {
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }
}

if ($Action -eq 'restart') {
    # Restart stays in this process and under this mutex. No environment marker
    # can authorize a second launcher to bypass repository lifecycle exclusion.
    $null = Get-EffectiveDatabaseUrl -PythonExe $Python
    Invoke-RunLocalOwnershipStopPlan -Services (Get-RunLocalProcessServices)
    Start-Sleep -Milliseconds 1000
    $Action = 'start'
}

switch ($Action) {
    'status' {
        Write-Host "--- Status ---" -ForegroundColor Cyan
        $statusPlan = @(Get-RunLocalOwnershipPlan -Services (Get-RunLocalProcessServices))
        foreach ($state in $statusPlan) {
            $status = if ($state.Owned) { 'Owned' } elseif ($state.Conflict) { 'Conflict' } elseif ($state.StaleOwnershipFile) { 'Stale metadata' } else { 'Stopped' }
            Write-Host "$($state.Service.Name): $status | Port $($state.EffectivePort) Listener=$(Get-NullCoalesce $state.ListenerPid 'None') | Owned PID=$(Get-NullCoalesce $state.SavedPid 'None')"
            if ($state.Reason) { Write-Host "  $($state.Reason)" -ForegroundColor Yellow }
        }
        exit 0
    }

    'stop' {
        Write-Host "Stopping services..." -ForegroundColor Yellow
        $stopServices = Get-RunLocalProcessServices
        Invoke-RunLocalOwnershipStopPlan -Services $stopServices

        Write-Host "Services stopped." -ForegroundColor Green
        exit 0
    }

    'reset-db' {
        $EffectiveDatabaseUrl = Get-EffectiveDatabaseUrl -PythonExe $Python
        if ($EffectiveDatabaseUrl -ne $DefaultDatabaseUrl) {
            throw 'reset-db is restricted to the default .local-run SQLite DB; legacy or custom DATABASE_URL values are never deleted.'
        }
        if (Test-Path $DefaultDatabasePath) {
            Remove-Item -Force $DefaultDatabasePath
            Write-Host "Deleted existing DB: $DefaultDatabasePath"
        }

        Write-Host "Initializing DB..."
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
        $seedArgs = Get-SeedDbArgs -SeedSource $SeedSource -FixturesCommunity $FixturesCommunity -RegenerateFixtures:$RegenerateFixtures
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
        Write-Host "DB reset complete." -ForegroundColor Green
        exit 0
    }

    'check-db' {
        $EffectiveDatabaseUrl = Get-EffectiveDatabaseUrl -PythonExe $Python
        $LocalDatabasePath = if ($EffectiveDatabaseUrl -eq $DefaultDatabaseUrl) {
            $DefaultDatabasePath
        } elseif ($EffectiveDatabaseUrl -eq $LegacyRootDatabaseUrl) {
            $LegacyRootDatabasePath
        } else {
            $null
        }
        if (-not $LocalDatabasePath) {
            throw 'check-db supports only the default local SQLite DB or the documented legacy root override.'
        }
        if (-not (Test-Path $LocalDatabasePath)) {
            throw "DB not found: $LocalDatabasePath. Run: .\scripts\run_local.ps1 reset-db"
        }

        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\check_sqlite_db.py') -Arguments @('--db', $LocalDatabasePath) -Description "check_sqlite_db.py"
        exit 0
    }

    'cleanup-simulator' {
        $cmdArgs = @('--retention-days', [string]$SimulatorRetentionDays)
        if ($DryRun) { $cmdArgs += '--dry-run' }

        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\cleanup_simulator_runs.py') -Arguments $cmdArgs -Description "cleanup_simulator_runs.py"
        exit 0
    }

    'restart-backend' {
        $StartedThisAttempt = @()
        try {
            # Validate application settings before stopping a healthy backend.
            $null = Get-EffectiveDatabaseUrl -PythonExe $Python
            # Stop backend
            $backendStopServices = Get-RunLocalProcessServices -BackendOnly
            Invoke-RunLocalOwnershipStopPlan -Services $backendStopServices

        # Start Backend
        $backendPortUsed = $BackendPort
        if ($AutoPorts) {
            $backendPortUsed = Get-FreePort -StartPort $BackendPort -MaxPort ($BackendPort + 50) -Kind 'backend'
        } else {
            $currentListener = Get-ListeningPid -Port $backendPortUsed
            if ($currentListener) {
                throw "Port $backendPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port."
            }
        }

        $backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$backendPortUsed")
        Write-Host "Restarting Backend on port $backendPortUsed..." -ForegroundColor Cyan
        $backendProc = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $RepoRoot -PassThru -WindowStyle $WindowStyle -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
        $backendService = @(Get-RunLocalProcessServices -BackendOnly -SelectedBackendPort $backendPortUsed)[0]
            $backendOwnership = Wait-ForRunLocalServiceOwnership -Service $backendService -Process $backendProc
            $StartedThisAttempt += [pscustomobject]@{
                Service = $backendService
                Pid = $backendOwnership.Pid
                ProcessStartFingerprint = $backendOwnership.ProcessStartFingerprint
            }
            Write-Host "Backend PID: $($backendOwnership.Pid)" -ForegroundColor Gray

            if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$backendPortUsed/api/v1/health" -TimeoutSec 60)) {
                throw "Backend health check failed. Check logs: $BackendErrLog"
            }

            # Update UI config to point to new backend port
            Update-EnvLocal -Path $EnvLocalPath -BaseUrl "http://127.0.0.1:$backendPortUsed"

            Write-Host "Backend restarted." -ForegroundColor Green
            Write-Host "Docs: http://127.0.0.1:$backendPortUsed/docs"
            exit 0
        } catch {
            Invoke-RunLocalStartupRollback -PrimaryFailure $_.Exception -StartedServices $StartedThisAttempt
        }
    }

    'start' {
        $StartedThisAttempt = @()
        try {
        $EffectiveDatabaseUrl = Get-EffectiveDatabaseUrl -PythonExe $Python
        $LocalDatabasePath = if ($EffectiveDatabaseUrl -eq $DefaultDatabaseUrl) {
            $DefaultDatabasePath
        } elseif ($EffectiveDatabaseUrl -eq $LegacyRootDatabaseUrl) {
            $LegacyRootDatabasePath
        } else {
            $null
        }
        Write-Host "[1/7] Cleaning up old processes..." -ForegroundColor Yellow
        $startCleanupServices = Get-RunLocalProcessServices
        Invoke-RunLocalOwnershipStopPlan -Services $startCleanupServices
        Write-Host "     Done." -ForegroundColor Gray

        Write-Host "[2/7] Checking database..." -ForegroundColor Yellow
        if (-not $LocalDatabasePath) {
            Write-Host "     Using explicit DATABASE_URL; automatic SQLite initialization is skipped." -ForegroundColor Gray
        } elseif (-not (Test-Path $LocalDatabasePath)) {
            Write-Host "     DB not found, initializing..." -ForegroundColor Gray
            Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
            $seedArgs = Get-SeedDbArgs -SeedSource $SeedSource -FixturesCommunity $FixturesCommunity -RegenerateFixtures:$RegenerateFixtures
            Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
            Write-Host "     DB initialized with seed source: $SeedSource" -ForegroundColor Gray
        } else {
            Write-Host "     DB exists: $LocalDatabasePath" -ForegroundColor Gray
            if ($SeedSource -eq 'fixtures' -and (Test-DbLooksLikeTinyTestSeed -PythonExe $Python -DbPath $LocalDatabasePath)) {
                Write-Warning "DB looks like a tiny '(Test)' seed set. For full Greenfield/Riverside data run: .\scripts\run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100"
            }
        }

        Write-Host "[3/7] Selecting backend port..." -ForegroundColor Yellow
        $backendPortUsed = $BackendPort
        if ($AutoPorts) {
            $backendPortUsed = Get-FreePort -StartPort $BackendPort -MaxPort ($BackendPort + 50) -Kind 'backend'
            Write-Host "     AutoPorts: selected port $backendPortUsed" -ForegroundColor Gray
        } else {
            if (-not (Wait-ForPortToBeFree -Port $backendPortUsed -TimeoutSec 10)) {
                $currentListener = Get-ListeningPid -Port $backendPortUsed
                throw "Port $backendPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port."
            }
            $currentListener = Get-ListeningPid -Port $backendPortUsed
            if ($currentListener) { throw "Port $backendPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port." }
            Write-Host "     Using port $backendPortUsed" -ForegroundColor Gray
        }

        Write-Host "[4/7] Starting Backend (uvicorn)..." -ForegroundColor Yellow
        
        $backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$backendPortUsed")
        $backendProc = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $RepoRoot -PassThru -WindowStyle $WindowStyle -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
        Write-Host "     Waiting for uvicorn to start..." -ForegroundColor Gray
        $backendService = @(Get-RunLocalProcessServices -BackendOnly -SelectedBackendPort $backendPortUsed)[0]
        $backendOwnership = Wait-ForRunLocalServiceOwnership -Service $backendService -Process $backendProc
        $StartedThisAttempt += [pscustomobject]@{
            Service = $backendService
            Pid = $backendOwnership.Pid
            ProcessStartFingerprint = $backendOwnership.ProcessStartFingerprint
        }
        if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$backendPortUsed/api/v1/health" -TimeoutSec 60)) { throw 'Backend health check failed.' }
        Write-Host "     Backend PID: $($backendOwnership.Pid)" -ForegroundColor Gray
        Write-Host "     Backend is healthy!" -ForegroundColor Green

        Write-Host "[5/7] Configuring UI environment..." -ForegroundColor Yellow
        Update-EnvLocal -Path $EnvLocalPath -BaseUrl "http://127.0.0.1:$backendPortUsed"
        Write-Host "     Updated .env.local with API URL" -ForegroundColor Gray

        Write-Host "[6/7] Checking UI dependencies..." -ForegroundColor Yellow
        if (-not $NoInstall) {
            if (-not (Test-Path (Join-Path $UiDir 'node_modules'))) {
                Write-Host "     Installing npm dependencies..." -ForegroundColor Gray
                Invoke-NpmCommand -NpmExe $Tools.Npm -WorkingDir $UiDir -Arguments @('install') -Description "npm install"
            } else {
                Write-Host "     node_modules exists, skipping install" -ForegroundColor Gray
            }
        } else {
            Write-Host "     Skipped (-NoInstall)" -ForegroundColor Gray
        }

        Write-Host "[7/7] Starting Admin UI (Vite)..." -ForegroundColor Yellow
        $uiPortUsed = $UiPort
        if ($AutoPorts) {
            $uiPortUsed = Get-FreePort -StartPort $UiPort -MaxPort ($UiPort + 20) -Kind 'ui' -ContextDir $UiDir
            Write-Host "     AutoPorts: selected port $uiPortUsed" -ForegroundColor Gray
        } else {
            $currentListener = Get-ListeningPid -Port $uiPortUsed
            if ($currentListener) {
                throw "Port $uiPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port."
            }
            Write-Host "     Using port $uiPortUsed" -ForegroundColor Gray
        }

        $viteCli = Join-Path $UiDir 'node_modules\vite\bin\vite.js'
        if (-not (Test-Path -LiteralPath $viteCli -PathType Leaf)) {
            throw "Vite executable not found: $viteCli"
        }
        $uiArgs = @("`"$viteCli`"", '--port', "$uiPortUsed", '--strictPort')
        $uiProc = Start-Process -FilePath $Tools.Node -ArgumentList $uiArgs -WorkingDirectory $UiDir -PassThru -WindowStyle $WindowStyle -RedirectStandardOutput $UiOutLog -RedirectStandardError $UiErrLog
        Write-Host "     Waiting for Vite to start listening on port $uiPortUsed..." -NoNewline
        $uiService = @(Get-RunLocalProcessServices -SelectedBackendPort $backendPortUsed -SelectedUiPort $uiPortUsed | Where-Object { $_.Name -eq 'Admin UI' })[0]
        $uiOwnership = Wait-ForRunLocalServiceOwnership -Service $uiService -Process $uiProc
        $StartedThisAttempt += [pscustomobject]@{
            Service = $uiService
            Pid = $uiOwnership.Pid
            ProcessStartFingerprint = $uiOwnership.ProcessStartFingerprint
        }
        Write-Host " OK" -ForegroundColor Green
        Write-Host "     UI PID: $($uiOwnership.Pid)" -ForegroundColor Gray

        # Summary
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  Environment Ready!" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Backend:  " -NoNewline; Write-Host "http://127.0.0.1:$backendPortUsed/docs" -ForegroundColor Cyan
        Write-Host "  Admin UI: " -NoNewline; Write-Host "http://localhost:$uiPortUsed/" -ForegroundColor Cyan
        Write-Host "  Logs:     $StateDir" -ForegroundColor Gray
        Write-Host ""
        Write-Host "To stop: .\scripts\run_local.ps1 stop" -ForegroundColor Gray
        Write-Host ""
        # Используем [Environment]::Exit для принудительного завершения,
        # т.к. обычный exit может ждать завершения child-процессов
        exit 0
        } catch {
            Invoke-RunLocalStartupRollback -PrimaryFailure $_.Exception -StartedServices $StartedThisAttempt
        }
    }
}
} finally {
    Exit-LauncherLifecycleLock -LockHandle $LifecycleLock
}
