<#
.SYNOPSIS
    Запуск полного стека GEO: Backend + Admin UI + Simulator UI в real mode.

.DESCRIPTION
    Скрипт запускает все три компонента системы с единой базой данных SQLite.
    - Backend (FastAPI/uvicorn) на порту 18000
    - Admin UI (Vite) на порту 5173
    - Simulator UI (Vite) на порту 5176
    
    Все UI работают с одним бекендом и одной БД, данные синхронизированы.

.EXAMPLE
    .\run_full_stack.ps1
    Запуск всего стека.

.EXAMPLE
    .\run_full_stack.ps1 -Action stop
    Остановка всех сервисов.

.EXAMPLE
    .\run_full_stack.ps1 -Action restart
    Перезапуск всех сервисов.

.EXAMPLE
    .\run_full_stack.ps1 -Action status
    Показать статус всех сервисов.

.EXAMPLE
    .\run_full_stack.ps1 -ResetDb -FixturesCommunity greenfield-village-100
    Пересоздать БД с данными Greenfield и запустить полный стек.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'start',

    [int]$BackendPort = 18000,
    [int]$AdminUiPort = 5173,
    [int]$SimulatorUiPort = 5176,

    [switch]$ResetDb,
    
    [ValidateSet('greenfield-village-100', 'riverside-town-50', 'greenfield-village-100-v2', 'riverside-town-50-v2')]
    [string]$FixturesCommunity = 'greenfield-village-100',
    
    [switch]$ShowWindows,
    [switch]$NoInstall,

    # Optional backend env overrides, e.g.:
    #   -BackendEnv 'SIMULATOR_CLEARING_POLICY=adaptive','SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS=250'
    [string[]]$BackendEnv = @()
)

$ErrorActionPreference = 'Stop'

# This entrypoint is explicitly for the permissive local-development profile.
$env:ENV = 'dev'
Remove-Item Env:ENVIRONMENT -ErrorAction SilentlyContinue

function Set-EnvOverrides {
    param([string[]]$Pairs)
    if (-not $Pairs -or $Pairs.Count -eq 0) { return }

    for ($pairIndex = 0; $pairIndex -lt $Pairs.Count; $pairIndex++) {
        $pair = $Pairs[$pairIndex]
        $p = [string]$pair
        if ([string]::IsNullOrWhiteSpace($p)) { continue }

        $idx = $p.IndexOf('=')
        if ($idx -lt 1) {
            throw "Invalid -BackendEnv entry at index $pairIndex (expected KEY=VALUE)."
        }

        $key = $p.Substring(0, $idx).Trim()
        $val = $p.Substring($idx + 1)
        if ([string]::IsNullOrWhiteSpace($key)) {
            throw "Invalid -BackendEnv entry at index $pairIndex (empty key)."
        }

        Set-Item -Path ("Env:$key") -Value $val
        Write-Host "     Env override applied: $key" -ForegroundColor Gray
    }
}

# --- Configuration & Paths ---
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $RepoRoot '.local-run'
$FullStackOwnershipDir = Join-Path $StateDir 'full-stack'
$AdminUiDir = Join-Path $RepoRoot 'admin-ui'
$SimulatorUiDir = Join-Path $RepoRoot 'simulator-ui/v2'
$AdminEnvLocalPath = Join-Path $AdminUiDir '.env.local'
$SimulatorEnvLocalPath = Join-Path $SimulatorUiDir '.env.local'
$DefaultDatabaseUrl = 'sqlite+aiosqlite:///./.local-run/geov0.db'
$LegacyRootDatabaseUrl = 'sqlite+aiosqlite:///./geov0.db'
$DefaultDatabasePath = Join-Path $StateDir 'geov0.db'
$LegacyRootDatabasePath = Join-Path $RepoRoot 'geov0.db'

# Create state directory
if (-not (Test-Path $StateDir)) {
    $null = New-Item -ItemType Directory -Force -Path $StateDir
}
if (-not (Test-Path $FullStackOwnershipDir)) {
    $null = New-Item -ItemType Directory -Force -Path $FullStackOwnershipDir
}

# Dedicated full-stack ownership paths must not overlap run_local's legacy bare
# PID files. This makes cross-entrypoint conflicts observable instead of letting
# one launcher reinterpret or overwrite the other launcher's state.
$BackendPidPath = Join-Path $FullStackOwnershipDir 'backend.owner.json'
$AdminUiPidPath = Join-Path $FullStackOwnershipDir 'admin-ui.owner.json'
$SimulatorUiPidPath = Join-Path $FullStackOwnershipDir 'simulator-ui.owner.json'

$AdminUiOutLogPath = Join-Path $StateDir 'admin-ui.out.log'
$AdminUiErrLogPath = Join-Path $StateDir 'admin-ui.err.log'
$BackendOutLogPath = Join-Path $StateDir 'backend.out.log'
$BackendErrLogPath = Join-Path $StateDir 'backend.err.log'
$SimulatorUiOutLogPath = Join-Path $StateDir 'simulator-ui.out.log'
$SimulatorUiErrLogPath = Join-Path $StateDir 'simulator-ui.err.log'

$WindowStyle = if ($ShowWindows) { 'Normal' } else { 'Hidden' }

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
            # The previous launcher crashed. The OS transferred the mutex to us.
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

function Get-ServiceOwnershipMetadata {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
        $metadata = $raw | ConvertFrom-Json -ErrorAction Stop
        $pidValue = 0
        if (-not [int]::TryParse([string]$metadata.pid, [ref]$pidValue) -or $pidValue -le 0) {
            throw 'invalid pid'
        }
        $fingerprint = [string]$metadata.process_start_fingerprint
        if ($fingerprint -notmatch '^utc-ticks:\d+$') { throw 'invalid process fingerprint' }
        return [pscustomobject]@{
            Valid = [bool]($metadata.version -eq 1)
            Pid = $pidValue
            ProcessStartFingerprint = $fingerprint
            ServiceName = [string]$metadata.service_name
            Port = [int]$metadata.port
        }
    } catch {
        return [pscustomobject]@{
            Valid = $false
            Pid = $null
            ProcessStartFingerprint = $null
            ServiceName = $null
            Port = 0
        }
    }
}

function Get-ProcessStartTimeFingerprint {
    param([int]$Id)
    if (-not $Id -or $Id -le 0) { return $null }
    try {
        $process = Get-Process -Id $Id -ErrorAction Stop
        $ticks = $process.StartTime.ToUniversalTime().Ticks.ToString([System.Globalization.CultureInfo]::InvariantCulture)
        return "utc-ticks:$ticks"
    } catch {
        return $null
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

function Write-ServiceOwnershipMetadata {
    param([object]$Service, [int]$Id, [string]$ProcessStartFingerprint)
    if ($ProcessStartFingerprint -notmatch '^utc-ticks:\d+$') {
        throw 'Unable to persist service ownership because the process fingerprint is unavailable.'
    }

    $metadata = [ordered]@{
        version = 1
        service_name = [string]$Service.Name
        port = [int]$Service.Port
        pid = $Id
        process_start_fingerprint = $ProcessStartFingerprint
        launcher_pid = $PID
        recorded_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $json = $metadata | ConvertTo-Json -Compress
    $tempPath = "$($Service.PidFile).tmp.$([Guid]::NewGuid().ToString('N'))"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($tempPath, $json, $utf8NoBom)
        Move-Item -LiteralPath $tempPath -Destination $Service.PidFile -Force
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if (-not $Id -or $Id -le 0 -or [string]::IsNullOrWhiteSpace($ExpectedStartFingerprint)) {
        throw 'Unable to stop service because its exact process identity is unavailable.'
    }

    $identity = Get-ProcessIdentityObservation -Id $Id -ExpectedStartFingerprint $ExpectedStartFingerprint
    if ($identity.Status -ne 'Exact') {
        throw 'Unable to stop service because its process identity changed.'
    }

    Stop-Process -Id $Id -Force -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        $identity = Get-ProcessIdentityObservation -Id $Id -ExpectedStartFingerprint $ExpectedStartFingerprint
        if ($identity.Status -in @('Missing', 'Mismatch')) {
            Write-Host "     Stopped PID $Id" -ForegroundColor Gray
            return
        }
        # Unreadable does not prove termination. Keep polling so a temporary
        # access/inspection failure cannot be reported as a successful stop.
        Start-Sleep -Milliseconds 100
    }
    throw "Owned process PID $Id did not stop."
}

function Get-ListeningPid {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn -and $conn.OwningProcess) { return [int]$conn.OwningProcess }
    } catch {}
    return $null
}

function Remove-ServicePidFile {
    param([string]$Path)
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-LogFilePath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }

    $dir = Split-Path -Parent $Path
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $ext = [System.IO.Path]::GetExtension($Path)

    # If a previous process still holds the log file handle, trying to create/truncate it can fail on Windows.
    # To keep startup robust, never touch an existing file: just pick a fresh unique path.
    if (-not (Test-Path $Path)) { return $Path }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    for ($i = 1; $i -le 50; $i++) {
        $candidate = Join-Path $dir "$base.$stamp.$i$ext"
        if (-not (Test-Path $candidate)) { return $candidate }
    }

    return $Path
}

function Test-HttpEndpoint {
    param([string]$Url, [int]$TimeoutSec = 60)
    
    Write-Host "     Waiting for $Url..." -NoNewline
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    
    while ((Get-Date) -lt $deadline) {
        $client = $null
        try {
            $uri = [Uri]$Url
            $client = New-Object System.Net.Sockets.TcpClient
            $connect = $client.BeginConnect($uri.Host, $uri.Port, $null, $null)
            $waitResult = $connect.AsyncWaitHandle.WaitOne(500)
            
            if ($waitResult) {
                try {
                    $client.EndConnect($connect)
                    Write-Host " OK" -ForegroundColor Green
                    return $true
                } catch {}
            }
        } catch {} finally {
            if ($null -ne $client) {
                try { $client.Close() } catch {}
                try { $client.Dispose() } catch {}
            }
        }
        Start-Sleep -Milliseconds 500
        Write-Host "." -NoNewline
    }
    Write-Host " Timeout!" -ForegroundColor Yellow
    return $false
}

function Show-RecentLog {
    param(
        [string]$Path,
        [int]$Tail = 60,
        [string]$Title = $null
    )

    if (-not $Path) { return }
    if (-not (Test-Path $Path)) { return }

    $label = if ($Title) { $Title } else { $Path }
    Write-Host ("     --- Last {0} lines: {1} ---" -f $Tail, $label) -ForegroundColor DarkGray
    try {
        $lines = Get-Content -Path $Path -Tail $Tail -ErrorAction Stop
        if ($null -eq $lines -or $lines.Count -eq 0) {
            Write-Host "     (log is empty)" -ForegroundColor DarkGray
        } else {
            foreach ($line in $lines) {
                Write-Host ("     {0}" -f $line)
            }
        }
    } catch {
        Write-Host "     (failed to read log)" -ForegroundColor DarkGray
    }
 }

function Update-EnvLocal {
    param([string]$Path, [string]$BaseUrl, [bool]$IsSimulator = $false)
    
    $content = @()
    if (Test-Path $Path) {
        $content = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    }

    $newContent = @()
    $hasMode = $false
    $hasBase = $false
    $hasBackendOrigin = $false

    foreach ($line in $content) {
        if ($line -match '^\s*VITE_API_MODE\s*=') {
            $newContent += 'VITE_API_MODE=real'
            $hasMode = $true
        } elseif ($line -match '^\s*VITE_API_BASE_URL\s*=') {
            $newContent += "VITE_API_BASE_URL=$BaseUrl"
            $hasBase = $true
        } elseif ($line -match '^\s*VITE_GEO_BACKEND_ORIGIN\s*=') {
            $newContent += "VITE_GEO_BACKEND_ORIGIN=$BaseUrl"
            $hasBackendOrigin = $true
        } else {
            $newContent += $line
        }
    }

    if (-not $hasMode) { $newContent += 'VITE_API_MODE=real' }
    if (-not $hasBase) { $newContent += "VITE_API_BASE_URL=$BaseUrl" }
    if ($IsSimulator -and -not $hasBackendOrigin) { 
        $newContent += "VITE_GEO_BACKEND_ORIGIN=$BaseUrl" 
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $newContent, $utf8NoBom)
}

function Get-ProjectTools {
    $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        throw "Python venv not found at $venvPython"
    }
    
    # NOTE: In PowerShell, `Get-Command npm` can resolve to `npm.ps1` (a PowerShell shim).
    # `Start-Process` cannot execute a .ps1 directly and fails with: "%1 is not a valid Win32 application".
    # Prefer Win32 shims (`npm.cmd`/`npm.exe`) when present.
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npm = Get-Command npm.exe -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        throw "npm not found in PATH"
    }

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) {
        $node = Get-Command node -ErrorAction SilentlyContinue
    }
    if (-not $node) {
        throw "Node.js not found in PATH"
    }

    $npmPath = $npm.Source
    if ($npmPath -and $npmPath.ToLowerInvariant().EndsWith('.ps1')) {
        $npmDir = Split-Path -Parent $npmPath
        $npmCmdCandidate = Join-Path $npmDir 'npm.cmd'
        if (Test-Path $npmCmdCandidate) {
            $npmPath = $npmCmdCandidate
        } else {
            $npmExeCandidate = Join-Path $npmDir 'npm.exe'
            if (Test-Path $npmExeCandidate) {
                $npmPath = $npmExeCandidate
            }
        }
    }

    return @{
        Python = $venvPython
        Npm = $npmPath
        Node = $node.Source
    }
}

function Get-ViteScriptPath {
    param([string]$ProjectDir)
    $viteScript = Join-Path $ProjectDir 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $viteScript -PathType Leaf)) {
        throw "Vite entrypoint not found in $ProjectDir. Run npm install first."
    }
    return $viteScript
}

function Invoke-PythonScript {
    param(
        [string]$PythonExe,
        [string]$ScriptPath,
        [string[]]$Arguments = @(),
        [string]$Description = "Python script"
    )
    
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

function Get-SafeDatabaseDisplayUrl {
    param([string]$DatabaseUrl)

    # Keep credentials and query parameters out of command arguments as well as
    # output. This parser intentionally uses only PowerShell/.NET string
    # operations, avoiding the native `python -c` quoting differences between
    # Windows PowerShell 5.1 and PowerShell 7.
    if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
        throw 'Unable to render a safe DATABASE_URL summary.'
    }

    $schemeDelimiter = $DatabaseUrl.IndexOf('://')
    if ($schemeDelimiter -lt 1) {
        throw 'Unable to render a safe DATABASE_URL summary.'
    }

    $scheme = $DatabaseUrl.Substring(0, $schemeDelimiter)
    if ($scheme -notmatch '^[A-Za-z][A-Za-z0-9+.-]*$') {
        throw 'Unable to render a safe DATABASE_URL summary.'
    }

    $remainder = $DatabaseUrl.Substring($schemeDelimiter + 3)
    if ([string]::IsNullOrWhiteSpace($remainder) -or $remainder -match '[\r\n\t]') {
        throw 'Unable to render a safe DATABASE_URL summary.'
    }

    # Raw '/', '?' and '#' terminate URI userinfo. If an '@' appears after one
    # of them, the input is ambiguous (usually an unescaped credential). Never
    # risk echoing that prefix as host/path; require percent-encoding instead.
    $userinfoIndex = $remainder.LastIndexOf('@')
    if ($userinfoIndex -ge 0) {
        foreach ($delimiter in @('/', '?', '#')) {
            $delimiterIndex = $remainder.IndexOf($delimiter)
            if ($delimiterIndex -ge 0 -and $delimiterIndex -lt $userinfoIndex) {
                throw 'Unable to render a safe DATABASE_URL summary.'
            }
        }
    }

    $parsedUri = $null
    if (-not [Uri]::TryCreate($DatabaseUrl, [UriKind]::Absolute, [ref]$parsedUri) -or
        $parsedUri.Scheme -ne $scheme) {
        throw 'Unable to render a safe DATABASE_URL summary.'
    }

    $queryIndex = $remainder.IndexOf('?')
    $fragmentIndex = $remainder.IndexOf('#')
    $suffixIndex = -1
    if ($queryIndex -ge 0) { $suffixIndex = $queryIndex }
    if ($fragmentIndex -ge 0 -and ($suffixIndex -lt 0 -or $fragmentIndex -lt $suffixIndex)) {
        $suffixIndex = $fragmentIndex
    }
    if ($suffixIndex -ge 0) {
        $remainder = $remainder.Substring(0, $suffixIndex)
    }

    # Three slashes (for example SQLite) mean there is no authority/userinfo.
    if (-not $remainder.StartsWith('/')) {
        $pathIndex = $remainder.IndexOf('/')
        $authority = if ($pathIndex -ge 0) {
            $remainder.Substring(0, $pathIndex)
        } else {
            $remainder
        }
        $path = if ($pathIndex -ge 0) { $remainder.Substring($pathIndex) } else { '' }
        $userinfoIndex = $authority.LastIndexOf('@')
        if ($userinfoIndex -ge 0) {
            $authority = $authority.Substring($userinfoIndex + 1)
        }
        if ([string]::IsNullOrWhiteSpace($authority) -or $authority -match '\s') {
            throw 'Unable to render a safe DATABASE_URL summary.'
        }
        $remainder = "$authority$path"
    }

    return "${scheme}://$remainder"
}

function Get-FullStackServices {
    return @(
        [pscustomobject]@{ Name = 'Backend'; Port = $BackendPort; PidFile = $BackendPidPath },
        [pscustomobject]@{ Name = 'Admin UI'; Port = $AdminUiPort; PidFile = $AdminUiPidPath },
        [pscustomobject]@{ Name = 'Simulator UI'; Port = $SimulatorUiPort; PidFile = $SimulatorUiPidPath }
    )
}

function Get-ServiceStopState {
    param([object]$Service)

    $metadata = Get-ServiceOwnershipMetadata -Path $Service.PidFile
    $listener = Get-ListeningPid -Port $Service.Port
    $metadataPresent = $null -ne $metadata
    $metadataMatchesService = [bool](
        $metadataPresent -and $metadata.Valid -and
        $metadata.ServiceName -eq $Service.Name -and
        $metadata.Port -eq $Service.Port
    )
    $identity = if ($metadataMatchesService) {
        Get-ProcessIdentityObservation -Id $metadata.Pid -ExpectedStartFingerprint $metadata.ProcessStartFingerprint
    } else {
        $unboundStatus = if ($metadataPresent) { 'Unreadable' } else { 'Missing' }
        [pscustomobject]@{ Status = $unboundStatus; Fingerprint = $null }
    }
    $exactProcess = [bool]($metadataMatchesService -and $identity.Status -eq 'Exact')
    $owned = [bool]($exactProcess -and $listener -and $metadata.Pid -eq $listener)
    $conflict = $false
    $reason = $null
    $staleOwnershipFile = $false

    if ($listener -and -not $metadataPresent) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but no ownership metadata exists"
    } elseif ($listener -and -not $metadataMatchesService) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but ownership metadata is invalid or belongs to another service"
    } elseif ($listener -and $identity.Status -eq 'Unreadable') {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but owned PID $($metadata.Pid) identity is unreadable"
    } elseif ($listener -and $identity.Status -eq 'Missing') {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but saved PID $($metadata.Pid) is missing"
    } elseif ($listener -and -not $exactProcess) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but the saved process fingerprint does not match"
    } elseif ($listener -and $metadata.Pid -ne $listener) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but owned PID $($metadata.Pid) was expected"
    } elseif (-not $listener -and $identity.Status -eq 'Unreadable') {
        $conflict = $true
        $reason = "owned PID $($metadata.Pid) identity is unreadable; metadata was retained"
    } elseif (-not $listener -and $exactProcess) {
        $conflict = $true
        $reason = "owned PID $($metadata.Pid) is alive but is not listening on port $($Service.Port)"
    } elseif (-not $listener -and $metadataPresent -and
        $identity.Status -in @('Missing', 'Mismatch')) {
        # It is safe to remove metadata only when it cannot identify the exact
        # currently-live process instance. PID reuse is never a reason to kill.
        $staleOwnershipFile = $true
        $reason = "stale ownership metadata does not identify a listener or the exact live process"
    }

    return [pscustomobject]@{
        Service = $Service
        MetadataPresent = $metadataPresent
        MetadataValid = [bool]($metadataPresent -and $metadata.Valid)
        MetadataMatchesService = $metadataMatchesService
        SavedPid = if ($metadataMatchesService) { $metadata.Pid } else { $null }
        SavedProcessStartFingerprint = if ($metadataMatchesService) { $metadata.ProcessStartFingerprint } else { $null }
        CurrentProcessStartFingerprint = $identity.Fingerprint
        ProcessIdentityStatus = $identity.Status
        ListenerPid = $listener
        Owned = [bool]$owned
        Conflict = [bool]$conflict
        Reason = $reason
        StaleOwnershipFile = [bool]$staleOwnershipFile
    }
}

function Get-ServiceStopPlan {
    param([object[]]$Services)
    return @($Services | ForEach-Object { Get-ServiceStopState -Service $_ })
}

function Assert-ServiceOwnershipForReplacement {
    param([object[]]$Services)

    $plan = @(Get-ServiceStopPlan -Services $Services)
    $conflicts = @($plan | Where-Object { $_.Conflict })
    foreach ($state in $conflicts) {
        Write-Warning "$($state.Service.Name): $($state.Reason). No process was stopped."
    }
    if ($conflicts.Count -gt 0) {
        throw 'Full-stack start/restart refused because service ownership could not be proven.'
    }
}

function Test-ServiceStopPlansEqual {
    param([object[]]$InitialPlan, [object[]]$FinalPlan)
    if ($InitialPlan.Count -ne $FinalPlan.Count) { return $false }
    for ($i = 0; $i -lt $InitialPlan.Count; $i++) {
        $before = $InitialPlan[$i]
        $after = $FinalPlan[$i]
        foreach ($property in @(
            'MetadataPresent', 'MetadataValid', 'MetadataMatchesService',
            'SavedPid', 'SavedProcessStartFingerprint', 'CurrentProcessStartFingerprint', 'ProcessIdentityStatus',
            'ListenerPid', 'Owned', 'Conflict', 'StaleOwnershipFile'
        )) {
            if ($before.$property -ne $after.$property) { return $false }
        }
    }
    return $true
}

function Remove-StaleServiceOwnershipMetadata {
    param([object]$State)
    if (-not $State.StaleOwnershipFile -or $State.ListenerPid) {
        throw 'Refusing to remove ownership metadata that is not proven stale.'
    }
    Remove-ServicePidFile -Path $State.Service.PidFile
}

function Wait-ForLaunchedServiceOwnership {
    param([object]$Service, [object]$Process, [int]$TimeoutSec = 60)

    $launchedPid = 0
    $launchedFingerprint = $null
    $ownershipPersisted = $false
    $ownershipResult = $null
    $primaryFailure = $null
    $cleanupFailure = $null
    try {
        if ($null -eq $Process -or -not $Process.Id) {
            throw "Unable to start $($Service.Name): Start-Process did not return a process."
        }
        $launchedPid = [int]$Process.Id
        $launchedFingerprint = Get-ProcessStartTimeFingerprint -Id $launchedPid
        if ([string]::IsNullOrWhiteSpace($launchedFingerprint)) {
            throw "Unable to start $($Service.Name): launched process identity is unavailable."
        }

        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ((Get-Date) -lt $deadline) {
            $currentFingerprint = Get-ProcessStartTimeFingerprint -Id $launchedPid
            if ($currentFingerprint -ne $launchedFingerprint) {
                throw "Unable to start $($Service.Name): launched process exited before ownership was established."
            }
            $listener = Get-ListeningPid -Port $Service.Port
            if ($listener) {
                if ($listener -ne $launchedPid) {
                    throw "Unable to start $($Service.Name): port $($Service.Port) is owned by a different process."
                }
                $confirmedFingerprint = Get-ProcessStartTimeFingerprint -Id $launchedPid
                if ($confirmedFingerprint -ne $launchedFingerprint) {
                    throw "Unable to start $($Service.Name): process identity changed before ownership was persisted."
                }
                Write-ServiceOwnershipMetadata -Service $Service -Id $launchedPid -ProcessStartFingerprint $launchedFingerprint
                $ownershipPersisted = $true
                $ownershipResult = [pscustomobject]@{ Pid = $launchedPid; ProcessStartFingerprint = $launchedFingerprint }
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ownershipPersisted) {
            throw "Unable to start $($Service.Name): launched process did not listen on port $($Service.Port)."
        }
    } catch {
        $primaryFailure = $_.Exception
    } finally {
        # finally also runs for pipeline interruption/Ctrl-C. Until ownership is
        # durably persisted, clean up only the exact launched process instance.
        # Re-read the file to close the interruption window between its atomic
        # rename and the in-memory ownershipPersisted assignment.
        if (-not $ownershipPersisted -and $launchedPid -gt 0 -and
            (Test-Path -LiteralPath $Service.PidFile)) {
            $persistedMetadata = Get-ServiceOwnershipMetadata -Path $Service.PidFile
            $ownershipPersisted = [bool](
                $persistedMetadata -and $persistedMetadata.Valid -and
                $persistedMetadata.ServiceName -eq $Service.Name -and
                $persistedMetadata.Port -eq $Service.Port -and
                $persistedMetadata.Pid -eq $launchedPid -and
                $persistedMetadata.ProcessStartFingerprint -eq $launchedFingerprint
            )
        }
        if (-not $ownershipPersisted -and $launchedPid -gt 0) {
            try {
                if ([string]::IsNullOrWhiteSpace($launchedFingerprint)) {
                    $cleanupIdentity = Get-ProcessIdentityObservation -Id $launchedPid -ExpectedStartFingerprint ''
                    if ($cleanupIdentity.Status -ne 'Missing') {
                        throw 'Exact launched process identity was unavailable for startup cleanup.'
                    }
                } else {
                    $cleanupIdentity = Get-ProcessIdentityObservation `
                        -Id $launchedPid `
                        -ExpectedStartFingerprint $launchedFingerprint
                    if ($cleanupIdentity.Status -eq 'Exact') {
                        Stop-ProcessById -Id $launchedPid -ExpectedStartFingerprint $launchedFingerprint
                    } elseif ($cleanupIdentity.Status -eq 'Unreadable') {
                        throw 'Exact launched process identity was unreadable during startup cleanup.'
                    }
                }
                # Missing means the child already exited; Mismatch proves PID
                # reuse. Neither case is a cleanup failure or a reason to kill.
            } catch {
                $cleanupFailure = $_.Exception
                if ($null -eq $primaryFailure) {
                    Write-Warning "Startup cleanup failed: $($cleanupFailure.Message)"
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
    if ($null -ne $primaryFailure) {
        throw $primaryFailure
    }
    if ($null -ne $cleanupFailure) {
        throw $cleanupFailure
    }
    return $ownershipResult
}

function Invoke-NpmInstall {
    param([string]$Dir, [string]$NpmExe)
    
    if (-not (Test-Path (Join-Path $Dir 'node_modules'))) {
        Write-Host "     Installing npm dependencies in $Dir..." -ForegroundColor Gray
        Push-Location $Dir
        try {
            & $NpmExe install
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed in $Dir"
            }
        } finally {
            Pop-Location
        }
    }
}

function Stop-AllServices {
    param(
        [object[]]$Services = @(Get-FullStackServices),
        [switch]$FailOnConflict
    )

    Write-Host "[STOP] Stopping all services..." -ForegroundColor Yellow

    # Build an initial plan, then repeat every ownership check immediately before
    # the destructive loop. A late conflict in any service aborts replacement
    # before the first process is stopped.
    $plan = @(Get-ServiceStopPlan -Services $Services)
    $conflicts = @($plan | Where-Object { $_.Conflict })
    foreach ($state in $conflicts) {
        Write-Warning "$($state.Service.Name): $($state.Reason). The listener was not stopped."
    }
    if ($FailOnConflict -and $conflicts.Count -gt 0) {
        throw 'Full-stack replacement refused because service ownership could not be proven.'
    }

    $finalPlan = @(Get-ServiceStopPlan -Services $Services)
    if (-not (Test-ServiceStopPlansEqual -InitialPlan $plan -FinalPlan $finalPlan)) {
        Write-Warning 'Service ownership changed during final preflight. No process was stopped.'
        if ($FailOnConflict) {
            throw 'Full-stack replacement refused because service ownership changed during final preflight.'
        }
        return $false
    }
    $finalConflicts = @($finalPlan | Where-Object { $_.Conflict })
    if ($FailOnConflict -and $finalConflicts.Count -gt 0) {
        throw 'Full-stack replacement refused because service ownership could not be proven.'
    }

    $ownedStates = @($finalPlan | Where-Object { $_.Owned })
    foreach ($state in $ownedStates) {
        # Stop-ProcessById performs one last exact-instance fingerprint check.
        # A change after the collective preflight is an unavoidable OS race; it
        # fails closed, but an earlier service may already have stopped.
        Stop-ProcessById -Id $state.SavedPid -ExpectedStartFingerprint $state.SavedProcessStartFingerprint
    }

    # Retain every ownership file until every owned stop succeeds. This leaves
    # recoverable evidence when a later stop fails.
    foreach ($state in $ownedStates) {
        Remove-ServicePidFile -Path $state.Service.PidFile
    }
    foreach ($state in @($finalPlan | Where-Object { $_.StaleOwnershipFile -and -not $_.ListenerPid })) {
        Remove-StaleServiceOwnershipMetadata -State $state
    }

    if ($finalConflicts.Count -eq 0) {
        Write-Host "     Owned services stopped." -ForegroundColor Green
        return $true
    }
    Write-Warning 'One or more services were not stopped because ownership was not proven.'
    return $false
}

function Undo-StartedServices {
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
            # Missing means the child already exited; Mismatch proves PID reuse.
            # Both allow removal of metadata written by this startup attempt.
            if (Test-Path -LiteralPath $started.Service.PidFile) {
                Remove-Item -LiteralPath $started.Service.PidFile -Force -ErrorAction Stop
            }
        } catch {
            $cleanupFailures += "$($started.Service.Name): $($_.Exception.Message)"
        }
    }
    if ($cleanupFailures.Count -gt 0) {
        throw "Startup rollback failed: $($cleanupFailures -join '; ')"
    }
}

# --- Main Logic ---

Write-Host ""
Write-Host "=== GEO Full Stack (Backend + Admin UI + Simulator UI) ===" -ForegroundColor Cyan
Write-Host "Action: $Action" -ForegroundColor Gray
Write-Host ""

$Services = @(Get-FullStackServices)
$BackendService = $Services | Where-Object { $_.Name -eq 'Backend' } | Select-Object -First 1
$AdminUiService = $Services | Where-Object { $_.Name -eq 'Admin UI' } | Select-Object -First 1
$SimulatorUiService = $Services | Where-Object { $_.Name -eq 'Simulator UI' } | Select-Object -First 1

$LifecycleLock = $null
try {
if ($Action -in @('start', 'stop', 'restart')) {
    $LifecycleLock = Enter-LauncherLifecycleLock -RepositoryRoot $RepoRoot -LauncherName 'run_full_stack'
}

switch ($Action) {
    'status' {
        Write-Host ""
        Write-Host "--- Service Status ---" -ForegroundColor Cyan
        
        foreach ($svc in $Services) {
            $state = Get-ServiceStopState -Service $svc

            $status = $null
            $color = $null

            if ($state.Owned) {
                $status = "Running (owned PID $($state.ListenerPid))"
                $color = "Green"
            } elseif ($state.Conflict) {
                $status = "Conflict: $($state.Reason)"
                $color = "Yellow"
            } elseif ($state.StaleOwnershipFile) {
                $status = "Stale ownership metadata (run stop to clean safely)"
                $color = "Yellow"
            } elseif ($state.SavedPid) {
                $status = "Saved PID not listening ($($state.SavedPid))"
                $color = "Yellow"
            } else {
                $status = "Stopped"
                $color = "Red"
            }
            
            Write-Host "$($svc.Name.PadRight(15)): " -NoNewline
            Write-Host $status -ForegroundColor $color
            Write-Host "                Port: $($svc.Port)" -ForegroundColor Gray
        }
        
        Write-Host ""
        exit 0
    }

    'stop' {
        $allStopped = Stop-AllServices -Services $Services -FailOnConflict
        if ($allStopped) { exit 0 }
        exit 1
    }
}

# --- START action ---

$StartedThisAttempt = @()
$StartupComplete = $false
$StartupFailure = $null
$RollbackFailure = $null
try {

# Every fallible settings/summary/reset/ownership check completes before the
# first process is stopped. This preserves a running stack when replacement
# cannot safely proceed.
$Tools = Get-ProjectTools
Write-Host "[OK] Python: $($Tools.Python)" -ForegroundColor Green
Write-Host "[OK] npm: $($Tools.Npm)" -ForegroundColor Green
Write-Host "[OK] Node.js: $($Tools.Node)" -ForegroundColor Green
$Python = $Tools.Python

Set-EnvOverrides -Pairs $BackendEnv
$EffectiveDatabaseUrl = Get-EffectiveDatabaseUrl -PythonExe $Python
$SafeDatabaseDisplayUrl = Get-SafeDatabaseDisplayUrl -DatabaseUrl $EffectiveDatabaseUrl

$LocalDatabasePath = if ($EffectiveDatabaseUrl -eq $DefaultDatabaseUrl) {
    $DefaultDatabasePath
} elseif ($EffectiveDatabaseUrl -eq $LegacyRootDatabaseUrl) {
    $LegacyRootDatabasePath
} else {
    $null
}

if ($ResetDb -and $EffectiveDatabaseUrl -ne $DefaultDatabaseUrl) {
    throw '-ResetDb is restricted to the default .local-run SQLite DB; legacy or custom DATABASE_URL values are never deleted.'
}
if ($NoInstall -and -not (Test-Path (Join-Path $AdminUiDir 'node_modules'))) {
    throw '-NoInstall used but admin-ui/node_modules not found. Run without -NoInstall once (or run npm install in admin-ui).'
}
if ($NoInstall -and -not (Test-Path (Join-Path $SimulatorUiDir 'node_modules'))) {
    throw '-NoInstall used but simulator-ui/v2/node_modules not found. Run without -NoInstall once (or run npm install in simulator-ui/v2).'
}
Assert-ServiceOwnershipForReplacement -Services $Services

Write-Host "[1/8] Cleaning up old processes..." -ForegroundColor Yellow
$null = Stop-AllServices -Services $Services -FailOnConflict
if ($Action -eq 'restart') {
    Start-Sleep -Milliseconds 1000
}

if ($ResetDb) {
    Write-Host "[2/8] Resetting database..." -ForegroundColor Yellow
    if (Test-Path $DefaultDatabasePath) {
        Remove-Item -Force $DefaultDatabasePath
        Write-Host "     Deleted existing DB" -ForegroundColor Gray
    }
    Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
    $seedArgs = @('--source', 'fixtures', '--community', $FixturesCommunity, '--regenerate-fixtures')
    Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
    Write-Host "     DB initialized with $FixturesCommunity" -ForegroundColor Green
} else {
    Write-Host "[2/8] Checking database..." -ForegroundColor Yellow
    if (-not $LocalDatabasePath) {
        Write-Host "     Using explicit DATABASE_URL; automatic SQLite initialization is skipped." -ForegroundColor Gray
    } elseif (-not (Test-Path $LocalDatabasePath)) {
        Write-Host "     DB not found, initializing..." -ForegroundColor Gray
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
        $seedArgs = @('--source', 'fixtures', '--community', $FixturesCommunity, '--regenerate-fixtures')
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
        Write-Host "     DB initialized with $FixturesCommunity" -ForegroundColor Green
    } else {
        Write-Host "     DB exists: $LocalDatabasePath" -ForegroundColor Gray
    }
}

Write-Host "[3/8] Starting Backend (uvicorn on port $BackendPort)..." -ForegroundColor Yellow
$env:SIMULATOR_ACTIONS_ENABLE = '1'

# Defaults for real-mode runner knobs (do not override if the user already set them).
# This prevents VS Code tasks from prompting for inputs.
if ([string]::IsNullOrWhiteSpace($env:SIMULATOR_REAL_AMOUNT_CAP)) {
    $env:SIMULATOR_REAL_AMOUNT_CAP = '500'
}
if ([string]::IsNullOrWhiteSpace($env:SIMULATOR_REAL_ENABLE_INJECT)) {
    $env:SIMULATOR_REAL_ENABLE_INJECT = '0'
}

$backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort")
$backendProcess = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $RepoRoot -WindowStyle $WindowStyle -RedirectStandardOutput $BackendOutLogPath -RedirectStandardError $BackendErrLogPath -PassThru
$backendOwnership = Wait-ForLaunchedServiceOwnership -Service $BackendService -Process $backendProcess -TimeoutSec 60
$StartedThisAttempt += [pscustomobject]@{
    Service = $BackendService
    Pid = $backendOwnership.Pid
    ProcessStartFingerprint = $backendOwnership.ProcessStartFingerprint
}

if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$BackendPort/api/v1/health" -TimeoutSec 60)) {
    throw "Backend failed to start on port $BackendPort"
}

Write-Host "     Backend PID: $($backendOwnership.Pid)" -ForegroundColor Gray

$backendUrl = "http://127.0.0.1:$BackendPort"

Write-Host "[4/8] Configuring Admin UI environment..." -ForegroundColor Yellow
Update-EnvLocal -Path $AdminEnvLocalPath -BaseUrl $backendUrl
Write-Host "     Updated $AdminEnvLocalPath" -ForegroundColor Gray

Write-Host "[5/8] Configuring Simulator UI environment..." -ForegroundColor Yellow
Update-EnvLocal -Path $SimulatorEnvLocalPath -BaseUrl $backendUrl -IsSimulator $true
Write-Host "     Updated $SimulatorEnvLocalPath" -ForegroundColor Gray

Write-Host "[6/8] Checking npm dependencies..." -ForegroundColor Yellow
if (-not $NoInstall) {
    Invoke-NpmInstall -Dir $AdminUiDir -NpmExe $Tools.Npm
    Invoke-NpmInstall -Dir $SimulatorUiDir -NpmExe $Tools.Npm
    Write-Host "     Dependencies ready" -ForegroundColor Gray
} else {
    Write-Host "     Skipped (-NoInstall)" -ForegroundColor Gray
}

Write-Host "[7/8] Starting Admin UI (Vite on port $AdminUiPort)..." -ForegroundColor Yellow
$AdminUiOutLogPath = Ensure-LogFilePath -Path $AdminUiOutLogPath
$AdminUiErrLogPath = Ensure-LogFilePath -Path $AdminUiErrLogPath

$adminUiViteScript = Get-ViteScriptPath -ProjectDir $AdminUiDir
$adminUiDirectArgs = @(('"{0}"' -f $adminUiViteScript), '--port', "$AdminUiPort", '--strictPort')
$adminUiProcess = Start-Process -FilePath $Tools.Node -ArgumentList $adminUiDirectArgs -WorkingDirectory $AdminUiDir -WindowStyle $WindowStyle -RedirectStandardOutput $AdminUiOutLogPath -RedirectStandardError $AdminUiErrLogPath -PassThru
$adminUiOwnership = Wait-ForLaunchedServiceOwnership -Service $AdminUiService -Process $adminUiProcess -TimeoutSec 60
$StartedThisAttempt += [pscustomobject]@{
    Service = $AdminUiService
    Pid = $adminUiOwnership.Pid
    ProcessStartFingerprint = $adminUiOwnership.ProcessStartFingerprint
}
Write-Host "     Admin UI PID: $($adminUiOwnership.Pid)" -ForegroundColor Gray

Write-Host "[8/8] Starting Simulator UI (Vite on port $SimulatorUiPort)..." -ForegroundColor Yellow

# Set environment variables for Simulator UI to run in real mode
$env:VITE_API_MODE = 'real'
$env:VITE_GEO_BACKEND_ORIGIN = $backendUrl

$SimulatorUiOutLogPath = Ensure-LogFilePath -Path $SimulatorUiOutLogPath
$SimulatorUiErrLogPath = Ensure-LogFilePath -Path $SimulatorUiErrLogPath

$simulatorUiViteScript = Get-ViteScriptPath -ProjectDir $SimulatorUiDir
$simulatorUiDirectArgs = @(('"{0}"' -f $simulatorUiViteScript), '--port', "$SimulatorUiPort", '--strictPort')
$simulatorUiProcess = Start-Process -FilePath $Tools.Node -ArgumentList $simulatorUiDirectArgs -WorkingDirectory $SimulatorUiDir -WindowStyle $WindowStyle -RedirectStandardOutput $SimulatorUiOutLogPath -RedirectStandardError $SimulatorUiErrLogPath -PassThru
$simulatorUiOwnership = Wait-ForLaunchedServiceOwnership -Service $SimulatorUiService -Process $simulatorUiProcess -TimeoutSec 60
$StartedThisAttempt += [pscustomobject]@{
    Service = $SimulatorUiService
    Pid = $simulatorUiOwnership.Pid
    ProcessStartFingerprint = $simulatorUiOwnership.ProcessStartFingerprint
}
Write-Host "     Simulator UI PID: $($simulatorUiOwnership.Pid)" -ForegroundColor Gray
$StartupComplete = $true

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Full Stack Ready!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Backend API:   " -NoNewline; Write-Host "http://127.0.0.1:$BackendPort/docs" -ForegroundColor Cyan
Write-Host "  Admin UI:      " -NoNewline; Write-Host "http://localhost:$AdminUiPort/" -ForegroundColor Cyan
Write-Host "  Simulator UI:  " -NoNewline; Write-Host "http://localhost:$SimulatorUiPort/?mode=real" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Database URL:  " -NoNewline; Write-Host "$SafeDatabaseDisplayUrl" -ForegroundColor Gray
Write-Host "  Logs:          " -NoNewline; Write-Host "$StateDir" -ForegroundColor Gray
Write-Host ""
Write-Host "All UIs share the same backend and database." -ForegroundColor DarkGray
Write-Host "Changes made in Admin UI will be visible in Simulator and vice versa." -ForegroundColor DarkGray
Write-Host ""
Write-Host "To stop all services: .\scripts\run_full_stack.ps1 stop" -ForegroundColor Gray
Write-Host ""
} catch {
    $StartupFailure = $_.Exception
} finally {
    if (-not $StartupComplete -and $StartedThisAttempt.Count -gt 0) {
        try {
            Undo-StartedServices -StartedServices $StartedThisAttempt
        } catch {
            $RollbackFailure = $_.Exception
        }
    }
}

if ($null -ne $StartupFailure -and $null -ne $RollbackFailure) {
    $combinedMessage = "$($StartupFailure.Message) $($RollbackFailure.Message)"
    $combinedFailure = New-Object System.InvalidOperationException($combinedMessage, $StartupFailure)
    $combinedFailure.Data['RollbackFailure'] = $RollbackFailure.ToString()
    throw $combinedFailure
}
if ($null -ne $StartupFailure) { throw $StartupFailure }
if ($null -ne $RollbackFailure) { throw $RollbackFailure }
} finally {
    Exit-LauncherLifecycleLock -LockHandle $LifecycleLock
}

exit 0
