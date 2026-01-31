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
    
    [ValidateSet('greenfield-village-100', 'riverside-town-50')]
    [string]$FixturesCommunity = 'greenfield-village-100',
    
    [switch]$ShowWindows,
    [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'

# --- Configuration & Paths ---
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $RepoRoot '.local-run'
$AdminUiDir = Join-Path $RepoRoot 'admin-ui'
$SimulatorUiDir = Join-Path $RepoRoot 'simulator-ui/v2'
$AdminEnvLocalPath = Join-Path $AdminUiDir '.env.local'
$SimulatorEnvLocalPath = Join-Path $SimulatorUiDir '.env.local'

# Create state directory
if (-not (Test-Path $StateDir)) {
    $null = New-Item -ItemType Directory -Force -Path $StateDir
}

# PID file paths
$BackendPidPath = Join-Path $StateDir 'backend.pid'
$AdminUiPidPath = Join-Path $StateDir 'admin-ui.pid'
$SimulatorUiPidPath = Join-Path $StateDir 'simulator-ui.pid'

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
    
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm not found in PATH"
    }

    return @{
        Python = $venvPython
        Npm = $npm.Source
    }
}

function Invoke-PythonScript {
    param(
        [string]$PythonExe,
        [string]$ScriptPath,
        [string[]]$Arguments = @(),
        [string]$Description = "Python script"
    )
    
    if ($Arguments.Count -gt 0) {
        & $PythonExe $ScriptPath @Arguments
    } else {
        & $PythonExe $ScriptPath
    }
    
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
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
    Write-Host "[STOP] Stopping all services..." -ForegroundColor Yellow
    
    # Stop by PID files
    foreach ($pidFile in @($SimulatorUiPidPath, $AdminUiPidPath, $BackendPidPath)) {
        Remove-StalePidFile $pidFile
        $procId = Get-PidFromFile $pidFile
        if ($procId) {
            Stop-ProcessById $procId
        }
        if (Test-Path $pidFile) {
            Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
        }
    }
    
    # Stop by listening ports
    foreach ($port in @($SimulatorUiPort, $AdminUiPort, $BackendPort)) {
        $listener = Get-ListeningPid -Port $port
        if ($listener) {
            Stop-ProcessById $listener
        }
    }
    
    Write-Host "     All services stopped." -ForegroundColor Green
}

# --- Main Logic ---

Write-Host ""
Write-Host "=== GEO Full Stack (Backend + Admin UI + Simulator UI) ===" -ForegroundColor Cyan
Write-Host "Action: $Action" -ForegroundColor Gray
Write-Host ""

$Tools = Get-ProjectTools
Write-Host "[OK] Python: $($Tools.Python)" -ForegroundColor Green
Write-Host "[OK] npm: $($Tools.Npm)" -ForegroundColor Green
$Python = $Tools.Python

switch ($Action) {
    'status' {
        Write-Host ""
        Write-Host "--- Service Status ---" -ForegroundColor Cyan
        
        $services = @(
            @{ Name = "Backend"; Port = $BackendPort; PidFile = $BackendPidPath },
            @{ Name = "Admin UI"; Port = $AdminUiPort; PidFile = $AdminUiPidPath },
            @{ Name = "Simulator UI"; Port = $SimulatorUiPort; PidFile = $SimulatorUiPidPath }
        )
        
        foreach ($svc in $services) {
            Remove-StalePidFile $svc.PidFile
            $savedProcId = Get-PidFromFile $svc.PidFile
            $listener = Get-ListeningPid -Port $svc.Port
            
            $status = if ($listener) { "Running (PID $listener)" } else { "Stopped" }
            $color = if ($listener) { "Green" } else { "Red" }
            
            Write-Host "$($svc.Name.PadRight(15)): " -NoNewline
            Write-Host $status -ForegroundColor $color
            Write-Host "                Port: $($svc.Port)" -ForegroundColor Gray
        }
        
        Write-Host ""
        exit 0
    }

    'stop' {
        Stop-AllServices
        exit 0
    }

    'restart' {
        Stop-AllServices
        Start-Sleep -Milliseconds 1000
        # Fall through to 'start'
    }
}

# --- START action ---

Write-Host "[1/8] Cleaning up old processes..." -ForegroundColor Yellow
Stop-AllServices

if ($ResetDb) {
    Write-Host "[2/8] Resetting database..." -ForegroundColor Yellow
    $dbPath = Join-Path $RepoRoot 'geov0.db'
    if (Test-Path $dbPath) {
        Remove-Item -Force $dbPath
        Write-Host "     Deleted existing DB" -ForegroundColor Gray
    }
    Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
    $seedArgs = @('--source', 'fixtures', '--community', $FixturesCommunity, '--regenerate-fixtures')
    Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
    Write-Host "     DB initialized with $FixturesCommunity" -ForegroundColor Green
} else {
    Write-Host "[2/8] Checking database..." -ForegroundColor Yellow
    $dbPath = Join-Path $RepoRoot 'geov0.db'
    if (-not (Test-Path $dbPath)) {
        Write-Host "     DB not found, initializing..." -ForegroundColor Gray
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
        $seedArgs = @('--source', 'fixtures', '--community', $FixturesCommunity, '--regenerate-fixtures')
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
        Write-Host "     DB initialized with $FixturesCommunity" -ForegroundColor Green
    } else {
        Write-Host "     DB exists: $dbPath" -ForegroundColor Gray
    }
}

Write-Host "[3/8] Starting Backend (uvicorn on port $BackendPort)..." -ForegroundColor Yellow
$backendCmd = "`"$Python`" -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort"
$backendStartArgs = @('/c', 'start', '/b', 'cmd', '/c', $backendCmd)
$null = Start-Process -FilePath 'cmd.exe' -ArgumentList $backendStartArgs -WorkingDirectory $RepoRoot -WindowStyle $WindowStyle

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
$adminUiArgs = @('/c', 'start', '/b', 'npm', 'run', 'dev', '--', '--port', "$AdminUiPort", '--strictPort')
$null = Start-Process -FilePath 'cmd.exe' -ArgumentList $adminUiArgs -WorkingDirectory $AdminUiDir -WindowStyle $WindowStyle

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
}

Write-Host "[8/8] Starting Simulator UI (Vite on port $SimulatorUiPort)..." -ForegroundColor Yellow

# Set environment variables for Simulator UI to run in real mode
$env:VITE_API_MODE = 'real'
$env:VITE_GEO_BACKEND_ORIGIN = $backendUrl

$simulatorUiArgs = @('/c', 'start', '/b', 'npm', 'run', 'dev', '--', '--port', "$SimulatorUiPort", '--strictPort')
$null = Start-Process -FilePath 'cmd.exe' -ArgumentList $simulatorUiArgs -WorkingDirectory $SimulatorUiDir -WindowStyle $WindowStyle

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
Write-Host "  Database:      " -NoNewline; Write-Host "$RepoRoot\geov0.db" -ForegroundColor Gray
Write-Host "  Logs:          " -NoNewline; Write-Host "$StateDir" -ForegroundColor Gray
Write-Host ""
Write-Host "All UIs share the same backend and database." -ForegroundColor DarkGray
Write-Host "Changes made in Admin UI will be visible in Simulator and vice versa." -ForegroundColor DarkGray
Write-Host ""
Write-Host "To stop all services: .\scripts\run_full_stack.ps1 stop" -ForegroundColor Gray
Write-Host ""

[Environment]::Exit(0)
