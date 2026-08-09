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

# PID file paths
$BackendPidPath = Join-Path $StateDir 'backend.pid'
$AdminUiPidPath = Join-Path $StateDir 'admin-ui.pid'
$SimulatorUiPidPath = Join-Path $StateDir 'simulator-ui.pid'

$AdminUiOutLogPath = Join-Path $StateDir 'admin-ui.out.log'
$AdminUiErrLogPath = Join-Path $StateDir 'admin-ui.err.log'
$BackendOutLogPath = Join-Path $StateDir 'backend.out.log'
$BackendErrLogPath = Join-Path $StateDir 'backend.err.log'
$SimulatorUiOutLogPath = Join-Path $StateDir 'simulator-ui.out.log'
$SimulatorUiErrLogPath = Join-Path $StateDir 'simulator-ui.err.log'

$WindowStyle = if ($ShowWindows) { 'Normal' } else { 'Hidden' }

# --- Helper Functions ---

function Get-PidFromFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        $raw = Get-Content -Path $Path -ErrorAction Stop | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        if ($raw.Trim() -match '^\d+$') { return [int]$raw }
    } catch {}
    return $null
}

function Stop-ProcessById {
    param([int]$Id)
    if (-not $Id -or $Id -eq 0) { return }
    try {
        Stop-Process -Id $Id -Force -ErrorAction Stop
        Write-Host "     Stopped PID $Id" -ForegroundColor Gray
    } catch {
        Write-Verbose "Process $Id not found or already stopped."
    }
}

function Get-ListeningPid {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($conn -and $conn.OwningProcess) { return [int]$conn.OwningProcess }
    } catch {}
    return $null
}

function Test-ProcessExists {
    param([int]$Id)
    if (-not $Id -or $Id -eq 0) { return $false }
    return $null -ne (Get-Process -Id $Id -ErrorAction SilentlyContinue)
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

function Remove-StalePidFile {
    param([string]$Path)
    $procId = Get-PidFromFile -Path $Path
    if ($procId) {
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            Remove-Item -Force $Path -ErrorAction SilentlyContinue
        }
    }
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
    }
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
        if ([string]::IsNullOrWhiteSpace($authority)) {
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

    $savedProcId = Get-PidFromFile -Path $Service.PidFile
    $listener = Get-ListeningPid -Port $Service.Port
    $savedProcessExists = if ($savedProcId) { Test-ProcessExists -Id $savedProcId } else { $false }
    $owned = $savedProcId -and $savedProcessExists -and $listener -and $savedProcId -eq $listener
    $conflict = $false
    $reason = $null

    if ($listener -and -not $savedProcId) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but no service PID file establishes ownership"
    } elseif ($listener -and -not $savedProcessExists) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but the saved service PID is missing or stale"
    } elseif ($listener -and $savedProcId -ne $listener) {
        $conflict = $true
        $reason = "port $($Service.Port) is listening on PID $listener but the saved service PID is $savedProcId"
    } elseif (-not $listener -and $savedProcessExists) {
        $conflict = $true
        $reason = "saved service PID $savedProcId exists but is not listening on port $($Service.Port)"
    }

    return [pscustomobject]@{
        Service = $Service
        SavedPid = $savedProcId
        ListenerPid = $listener
        Owned = [bool]$owned
        Conflict = [bool]$conflict
        Reason = $reason
        StalePidFile = [bool]($savedProcId -and -not $savedProcessExists)
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

    # Validate every service before stopping the first owned process. start and
    # restart use FailOnConflict so an unrelated listener cannot cause a partial
    # replacement or be mistaken for one of this launcher's children.
    $plan = @(Get-ServiceStopPlan -Services $Services)
    $conflicts = @($plan | Where-Object { $_.Conflict })
    foreach ($state in $conflicts) {
        Write-Warning "$($state.Service.Name): $($state.Reason). The listener was not stopped."
    }
    if ($FailOnConflict -and $conflicts.Count -gt 0) {
        throw 'Full-stack replacement refused because service ownership could not be proven.'
    }

    foreach ($state in $plan) {
        if ($state.Owned) {
            # Re-check immediately before the destructive action so a listener
            # change after the plan was built can never redirect the stop.
            $currentListener = Get-ListeningPid -Port $state.Service.Port
            if ($currentListener -eq $state.SavedPid -and (Test-ProcessExists -Id $state.SavedPid)) {
                Stop-ProcessById -Id $state.SavedPid
                Remove-ServicePidFile -Path $state.Service.PidFile
            } else {
                Write-Warning "$($state.Service.Name): ownership changed before stop; no process was stopped."
                if ($FailOnConflict) {
                    throw 'Full-stack replacement refused because service ownership changed during stop.'
                }
            }
        } elseif ($state.StalePidFile -and -not $state.ListenerPid) {
            Remove-ServicePidFile -Path $state.Service.PidFile
        }
    }

    if ($conflicts.Count -eq 0) {
        Write-Host "     Owned services stopped." -ForegroundColor Green
        return $true
    }
    Write-Warning 'One or more services were not stopped because ownership was not proven.'
    return $false
}

# --- Main Logic ---

Write-Host ""
Write-Host "=== GEO Full Stack (Backend + Admin UI + Simulator UI) ===" -ForegroundColor Cyan
Write-Host "Action: $Action" -ForegroundColor Gray
Write-Host ""

$Services = @(Get-FullStackServices)

switch ($Action) {
    'status' {
        Write-Host ""
        Write-Host "--- Service Status ---" -ForegroundColor Cyan
        
        foreach ($svc in $Services) {
            Remove-StalePidFile $svc.PidFile
            $state = Get-ServiceStopState -Service $svc

            $status = $null
            $color = $null

            if ($state.Owned) {
                $status = "Running (owned PID $($state.ListenerPid))"
                $color = "Green"
            } elseif ($state.ListenerPid) {
                $status = "Port occupied (unowned PID $($state.ListenerPid))"
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
        $allStopped = Stop-AllServices -Services $Services
        if ($allStopped) { exit 0 }
        exit 1
    }
}

# --- START action ---

# Every fallible settings/summary/reset/ownership check completes before the
# first process is stopped. This preserves a running stack when replacement
# cannot safely proceed.
$Tools = Get-ProjectTools
Write-Host "[OK] Python: $($Tools.Python)" -ForegroundColor Green
Write-Host "[OK] npm: $($Tools.Npm)" -ForegroundColor Green
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
$null = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $RepoRoot -WindowStyle $WindowStyle -RedirectStandardOutput $BackendOutLogPath -RedirectStandardError $BackendErrLogPath

if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$BackendPort/api/v1/health" -TimeoutSec 60)) {
    throw "Backend failed to start on port $BackendPort"
}

$backendRealPid = Get-ListeningPid -Port $BackendPort
if ($backendRealPid) {
    $backendRealPid | Out-File -Encoding ascii -FilePath $BackendPidPath
    Write-Host "     Backend PID: $backendRealPid" -ForegroundColor Gray
}

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

$adminUiArgs = @('run', 'dev', '--', '--port', "$AdminUiPort", '--strictPort')
$adminUiCmdArgs = @('/c', 'npm') + $adminUiArgs
$null = Start-Process -FilePath 'cmd.exe' -ArgumentList $adminUiCmdArgs -WorkingDirectory $AdminUiDir -WindowStyle $WindowStyle -RedirectStandardOutput $AdminUiOutLogPath -RedirectStandardError $AdminUiErrLogPath

# Wait for Admin UI
$adminUiDeadline = (Get-Date).AddSeconds(60)
Write-Host "     Waiting for Admin UI..." -NoNewline
while ((Get-Date) -lt $adminUiDeadline) {
    $adminUiPid = Get-ListeningPid -Port $AdminUiPort
    if ($adminUiPid) {
        Write-Host " OK" -ForegroundColor Green
        $adminUiPid | Out-File -Encoding ascii -FilePath $AdminUiPidPath
        Write-Host "     Admin UI PID: $adminUiPid" -ForegroundColor Gray
        break
    }
    Start-Sleep -Milliseconds 500
    Write-Host "." -NoNewline
}
if (-not (Get-ListeningPid -Port $AdminUiPort)) {
    Write-Host " Timeout!" -ForegroundColor Yellow
    Write-Host "     See logs: $AdminUiOutLogPath and $AdminUiErrLogPath" -ForegroundColor Gray
    Show-RecentLog -Path $AdminUiErrLogPath -Tail 80 -Title 'admin-ui.err.log'
    Show-RecentLog -Path $AdminUiOutLogPath -Tail 40 -Title 'admin-ui.out.log'
}

Write-Host "[8/8] Starting Simulator UI (Vite on port $SimulatorUiPort)..." -ForegroundColor Yellow

# Set environment variables for Simulator UI to run in real mode
$env:VITE_API_MODE = 'real'
$env:VITE_GEO_BACKEND_ORIGIN = $backendUrl

$SimulatorUiOutLogPath = Ensure-LogFilePath -Path $SimulatorUiOutLogPath
$SimulatorUiErrLogPath = Ensure-LogFilePath -Path $SimulatorUiErrLogPath

$simulatorUiArgs = @('run', 'dev', '--', '--port', "$SimulatorUiPort", '--strictPort')
$simulatorUiCmdArgs = @('/c', 'npm') + $simulatorUiArgs
$null = Start-Process -FilePath 'cmd.exe' -ArgumentList $simulatorUiCmdArgs -WorkingDirectory $SimulatorUiDir -WindowStyle $WindowStyle -RedirectStandardOutput $SimulatorUiOutLogPath -RedirectStandardError $SimulatorUiErrLogPath

# Wait for Simulator UI
$simulatorUiDeadline = (Get-Date).AddSeconds(60)
Write-Host "     Waiting for Simulator UI..." -NoNewline
while ((Get-Date) -lt $simulatorUiDeadline) {
    $simulatorUiPid = Get-ListeningPid -Port $SimulatorUiPort
    if ($simulatorUiPid) {
        Write-Host " OK" -ForegroundColor Green
        $simulatorUiPid | Out-File -Encoding ascii -FilePath $SimulatorUiPidPath
        Write-Host "     Simulator UI PID: $simulatorUiPid" -ForegroundColor Gray
        break
    }
    Start-Sleep -Milliseconds 500
    Write-Host "." -NoNewline
}
if (-not (Get-ListeningPid -Port $SimulatorUiPort)) {
    Write-Host " Timeout!" -ForegroundColor Yellow
    Write-Host "     See logs: $SimulatorUiOutLogPath and $SimulatorUiErrLogPath" -ForegroundColor Gray
    Show-RecentLog -Path $SimulatorUiErrLogPath -Tail 80 -Title 'simulator-ui.err.log'
    Show-RecentLog -Path $SimulatorUiOutLogPath -Tail 40 -Title 'simulator-ui.out.log'
}

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

[Environment]::Exit(0)
