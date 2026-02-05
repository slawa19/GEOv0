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
    .\run_local.ps1 -Action restart-backend -ReloadBackend
    Перезапуск только бэкенда с флагом --reload.

.EXAMPLE
    .\run_local.ps1 -Action reset-db -SeedSource fixtures -FixturesCommunity riverside-town-50 -RegenerateFixtures
    Пересоздать SQLite DB и наполнить данными Riverside (50 участников).

.EXAMPLE
    .\run_local.ps1 -Action reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100 -RegenerateFixtures
    Пересоздать SQLite DB и наполнить данными Greenfield (100 участников).

.EXAMPLE
    .\run_local.ps1 -Action check-db
    Быстро проверить целостность текущей SQLite DB (geov0.db).

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

# --- Configuration & Paths ---
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $RepoRoot '.local-run'
$UiDir = Join-Path $RepoRoot 'admin-ui'
$EnvLocalPath = Join-Path $UiDir '.env.local'

# Создаем директорию для логов и PID-файлов
if (-not (Test-Path $StateDir)) {
    $null = New-Item -ItemType Directory -Force -Path $StateDir
}

# Пути к PID и логам
$BackendPidPath = Join-Path $StateDir 'backend.pid'
$UiPidPath = Join-Path $StateDir 'admin-ui.pid'
$BackendOutLog = Join-Path $StateDir 'backend.out.log'
$BackendErrLog = Join-Path $StateDir 'backend.err.log'

# --- Helper Functions ---

function Get-NullCoalesce {
    <#
    .SYNOPSIS
        Замена оператора ?? для совместимости с PowerShell 5.1
    #>
    param($Value, $Default)
    if ($null -eq $Value) { return $Default }
    return $Value
}

function Get-PidFromFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    
    try {
        $raw = Get-Content -Path $Path -ErrorAction Stop | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        if ($raw.Trim() -match '^\d+$') { return [int]$raw }
    } catch {
        Write-Verbose "Could not read PID file ${Path}: $_"
    }
    return $null
}

function Stop-ProcessById {
    param([int]$Id)
    if (-not $Id -or $Id -eq 0) { return }
    
    try {
        Write-Verbose "Stopping process with ID $Id..."
        Stop-Process -Id $Id -Force -ErrorAction Stop
    } catch {
        Write-Verbose "Process $Id not found or already stopped."
    }
}

function Get-ProcessCommandLine {
    param([int]$Id)
    if (-not $Id -or $Id -eq 0) { return '' }
    
    try {
        # Win32_Process нужен для получения аргументов командной строки
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction Stop
        if ($null -eq $p -or $null -eq $p.CommandLine) { return '' }
        return ($p.CommandLine | Out-String).Trim()
    } catch {
        Write-Verbose "Could not get command line for process ${Id}: $_"
        return ''
    }
}

function Test-IsOurBackend {
    param([int]$Id)
    $cmd = Get-ProcessCommandLine -Id $Id
    if (-not $cmd) { return $false }
    
    $cmdLower = $cmd.ToLowerInvariant()
    # Проверяем наличие ключевых маркеров запуска uvicorn и нашего приложения
    return ($cmdLower -like '*uvicorn*' -and $cmdLower -like '*app.main:app*')
}

function Test-WslDockerAvailable {
    try {
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
        & wsl.exe -e bash -lc 'command -v docker >/dev/null 2>&1'
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-OurDockerContainersByPort {
    param(
        [int]$Port
    )

    $names = @()

    # Native docker
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        try {
            $out = & docker ps --format '{{.Names}} {{.Ports}}' 2>$null
            foreach ($line in ($out | Where-Object { $_ })) {
                $text = $line.ToString()
                if ($text -notmatch '^(\S+)\s+(.+)$') { continue }
                $name = $Matches[1]
                $ports = $Matches[2]
                if ($name -notlike 'geov0-*') { continue }
                if ($ports -like "*:$Port->*") { $names += $name }
            }
        } catch {
            # ignore
        }
    }

    if ($names.Count -gt 0) { return $names }

    # WSL docker
    if (Test-WslDockerAvailable) {
        try {
            $out = & wsl.exe -e bash -lc "docker ps --format '{{.Names}} {{.Ports}}'" 2>$null
            foreach ($line in ($out | Where-Object { $_ })) {
                $text = $line.ToString()
                if ($text -notmatch '^(\S+)\s+(.+)$') { continue }
                $name = $Matches[1]
                $ports = $Matches[2]
                if ($name -notlike 'geov0-*') { continue }
                if ($ports -like "*:$Port->*") { $names += $name }
            }
        } catch {
            # ignore
        }
    }

    return $names
}

function Stop-OurDockerContainers {
    param(
        [string[]]$Names
    )

    $Names = @($Names | Where-Object { $_ })
    if ($Names.Count -eq 0) { return }

    Write-Host "Stopping repo docker containers: $($Names -join ', ')" -ForegroundColor Yellow

    if (Get-Command docker -ErrorAction SilentlyContinue) {
        try {
            & docker stop @Names | Out-Null
            return
        } catch {
            # fall through to WSL
        }
    }

    if (Test-WslDockerAvailable) {
        try {
            $escaped = $Names | ForEach-Object { "'" + ($_.Replace("'", "'\\''")) + "'" }
            $cmd = "docker stop " + ($escaped -join ' ')
            & wsl.exe -e bash -lc "$cmd >/dev/null 2>&1 || true" | Out-Null
        } catch {
            # ignore
        }
    }
}

function Test-IsOurAdminUi {
    param([int]$Id, [string]$TargetUiDir)
    $cmd = Get-ProcessCommandLine -Id $Id
    if (-not $cmd) { return $false }
    
    $cmdLower = $cmd.ToLowerInvariant()
    # Экранируем специальные wildcard символы в пути для корректного -like сравнения
    $escapedDir = [WildcardPattern]::Escape($TargetUiDir.ToLowerInvariant())
    
    # Проверяем, что это vite и путь к директории совпадает (защита от убийства чужих vite)
    return ($cmdLower -like '*vite*' -and $cmdLower -like "*$escapedDir*")
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

function Stop-IfListeningAndOurs {
    param(
        [int]$Port,
        [string]$Kind, # 'backend' or 'ui'
        [string]$ContextDir # Needed for UI check
    )

    $procId = Get-ListeningPid -Port $Port
    if (-not $procId) { return }

    $isOurs = $false
    if ($Kind -eq 'backend') { 
        $isOurs = Test-IsOurBackend -Id $procId 
    } elseif ($Kind -eq 'ui') { 
        $isOurs = Test-IsOurAdminUi -Id $procId -TargetUiDir $ContextDir 
    }

    if ($isOurs) {
        Write-Host "Stopping $Kind listening on port $Port (PID $procId)..."
        Stop-ProcessById -Id $procId
        return
    } else {
        # Extra dev convenience: if the port is held by OUR docker-compose stack (geov0-*), stop it.
        # Never touch чужие containers.
        $dockerNames = Get-OurDockerContainersByPort -Port $Port
        if ($dockerNames.Count -gt 0) {
            Stop-OurDockerContainers -Names $dockerNames
            return
        }

        Write-Warning "Port $Port is in use by PID $procId, but it doesn't look like our $Kind. Skipping."
        Write-Verbose "Command line was: $(Get-ProcessCommandLine -Id $procId)"
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

function Remove-StalePidFile {
    param([string]$Path)
    $procId = Get-PidFromFile -Path $Path
    if ($procId) {
        if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            Write-Verbose "Removing stale PID file: $Path"
            Remove-Item -Force $Path -ErrorAction SilentlyContinue
        }
    }
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

        # Если порт занят нашим старым процессом, убиваем его и переиспользуем порт
        Stop-IfListeningAndOurs -Port $p -Kind $Kind -ContextDir $ContextDir
        Start-Sleep -Milliseconds 200
        
        if (-not (Get-ListeningPid -Port $p)) { return $p }
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
    if ($Arguments.Count -gt 0) {
        & $PythonExe $ScriptPath @Arguments
    } else {
        & $PythonExe $ScriptPath
    }
    
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
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
Write-Host "[OK] npm: $($Tools.Npm)" -ForegroundColor Green
$Python = $Tools.Python
$WindowStyle = if ($ShowWindows) { 'Normal' } else { 'Hidden' }

switch ($Action) {
    'status' {
        Remove-StalePidFile $BackendPidPath
        Remove-StalePidFile $UiPidPath

        $bPid = Get-PidFromFile $BackendPidPath
        $uPid = Get-PidFromFile $UiPidPath
        $bListen = Get-ListeningPid -Port $BackendPort
        $uListen = Get-ListeningPid -Port $UiPort

        Write-Host "--- Status ---" -ForegroundColor Cyan
        Write-Host "Backend: PID File=$(Get-NullCoalesce $bPid 'None') | Port $BackendPort Listener=$(Get-NullCoalesce $bListen 'None')"
        if ($bListen) { Write-Host "  Cmd: $(Get-ProcessCommandLine $bListen)" -ForegroundColor Gray }
        
        Write-Host "Admin UI: PID File=$(Get-NullCoalesce $uPid 'None') | Port $UiPort Listener=$(Get-NullCoalesce $uListen 'None')"
        if ($uListen) { Write-Host "  Cmd: $(Get-ProcessCommandLine $uListen)" -ForegroundColor Gray }
        exit 0
    }

    'stop' {
        Write-Host "Stopping services..." -ForegroundColor Yellow
        Remove-StalePidFile $BackendPidPath
        Remove-StalePidFile $UiPidPath

        $bPid = Get-PidFromFile $BackendPidPath
        $uPid = Get-PidFromFile $UiPidPath

        Stop-ProcessById $uPid
        Stop-ProcessById $bPid

        Stop-IfListeningAndOurs -Port $UiPort -Kind 'ui' -ContextDir $UiDir
        Stop-IfListeningAndOurs -Port $BackendPort -Kind 'backend'

        if (Test-Path $UiPidPath) { Remove-Item -Force $UiPidPath -ErrorAction SilentlyContinue }
        if (Test-Path $BackendPidPath) { Remove-Item -Force $BackendPidPath -ErrorAction SilentlyContinue }

        Write-Host "Services stopped." -ForegroundColor Green
        exit 0
    }

    'reset-db' {
        $dbPath = Join-Path $RepoRoot 'geov0.db'
        if (Test-Path $dbPath) {
            Remove-Item -Force $dbPath
            Write-Host "Deleted existing DB: $dbPath"
        }

        Write-Host "Initializing DB..."
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
        $seedArgs = Get-SeedDbArgs -SeedSource $SeedSource -FixturesCommunity $FixturesCommunity -RegenerateFixtures:$RegenerateFixtures
        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
        Write-Host "DB reset complete." -ForegroundColor Green
        exit 0
    }

    'check-db' {
        $dbPath = Join-Path $RepoRoot 'geov0.db'
        if (-not (Test-Path $dbPath)) {
            throw "DB not found: $dbPath. Run: .\scripts\run_local.ps1 reset-db"
        }

        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\check_sqlite_db.py') -Description "check_sqlite_db.py"
        exit 0
    }

    'cleanup-simulator' {
        $cmdArgs = @('--retention-days', [string]$SimulatorRetentionDays)
        if ($DryRun) { $cmdArgs += '--dry-run' }

        Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\cleanup_simulator_runs.py') -Arguments $cmdArgs -Description "cleanup_simulator_runs.py"
        exit 0
    }

    'restart' {
        # Рекурсивный вызов скрипта для чистого рестарта
        $restartParams = @{
            Action = 'stop'
            BackendPort = $BackendPort
            UiPort = $UiPort
        }
        & $PSCommandPath @restartParams
        
        $startParams = @{
            Action = 'start'
            BackendPort = $BackendPort
            UiPort = $UiPort
        }
        if ($AutoPorts) { $startParams['AutoPorts'] = $true }
        if ($NoInstall) { $startParams['NoInstall'] = $true }
        if ($ReloadBackend) { $startParams['ReloadBackend'] = $true }
        if ($ShowWindows) { $startParams['ShowWindows'] = $true }
        
        & $PSCommandPath @startParams
        exit 0
    }

    'restart-backend' {
        Remove-StalePidFile $BackendPidPath
        
        # Stop backend
        $bPid = Get-PidFromFile $BackendPidPath
        Stop-ProcessById $bPid
        Stop-IfListeningAndOurs -Port $BackendPort -Kind 'backend'
        if (Test-Path $BackendPidPath) { Remove-Item -Force $BackendPidPath -ErrorAction SilentlyContinue }

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
        if ($ReloadBackend) { $backendArgs += '--reload' }

        Write-Host "Restarting Backend on port $backendPortUsed..." -ForegroundColor Cyan
        $backendProc = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $RepoRoot -PassThru -WindowStyle $WindowStyle -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog
        $backendProc.Id | Out-File -Encoding ascii -FilePath $BackendPidPath

        if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$backendPortUsed/api/v1/health" -TimeoutSec 60)) {
            Write-Warning "Backend health check failed, but process is running. Check logs: $BackendErrLog"
        }

        # Update UI config to point to new backend port
        Update-EnvLocal -Path $EnvLocalPath -BaseUrl "http://127.0.0.1:$backendPortUsed"

        Write-Host "Backend restarted." -ForegroundColor Green
        Write-Host "Docs: http://127.0.0.1:$backendPortUsed/docs"
        exit 0
    }

    'start' {
        Write-Host "[1/7] Cleaning up old processes..." -ForegroundColor Yellow
        Remove-StalePidFile $BackendPidPath
        Remove-StalePidFile $UiPidPath
        
        $bPid = Get-PidFromFile $BackendPidPath
        $uPid = Get-PidFromFile $UiPidPath
        Stop-ProcessById $uPid
        Stop-ProcessById $bPid
        
        if (Test-Path $UiPidPath) { Remove-Item -Force $UiPidPath }
        if (Test-Path $BackendPidPath) { Remove-Item -Force $BackendPidPath }
        Write-Host "     Done." -ForegroundColor Gray

        Write-Host "[2/7] Checking database..." -ForegroundColor Yellow
        $dbPath = Join-Path $RepoRoot 'geov0.db'
        if (-not (Test-Path $dbPath)) {
            Write-Host "     DB not found, initializing..." -ForegroundColor Gray
            Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\init_sqlite_db.py') -Description "init_sqlite_db.py"
            $seedArgs = Get-SeedDbArgs -SeedSource $SeedSource -FixturesCommunity $FixturesCommunity -RegenerateFixtures:$RegenerateFixtures
            Invoke-PythonScript -PythonExe $Python -ScriptPath (Join-Path $RepoRoot 'scripts\seed_db.py') -Arguments $seedArgs -Description "seed_db.py"
            Write-Host "     DB initialized with seed source: $SeedSource" -ForegroundColor Gray
        } else {
            Write-Host "     DB exists: $dbPath" -ForegroundColor Gray
            if ($SeedSource -eq 'fixtures' -and (Test-DbLooksLikeTinyTestSeed -PythonExe $Python -DbPath $dbPath)) {
                Write-Warning "DB looks like a tiny '(Test)' seed set. For full Greenfield/Riverside data run: .\scripts\run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100"
            }
        }

        Write-Host "[3/7] Selecting backend port..." -ForegroundColor Yellow
        $backendPortUsed = $BackendPort
        if ($AutoPorts) {
            $backendPortUsed = Get-FreePort -StartPort $BackendPort -MaxPort ($BackendPort + 50) -Kind 'backend'
            Write-Host "     AutoPorts: selected port $backendPortUsed" -ForegroundColor Gray
        } else {
            Stop-IfListeningAndOurs -Port $backendPortUsed -Kind 'backend'
            if (-not (Wait-ForPortToBeFree -Port $backendPortUsed -TimeoutSec 10)) {
                $currentListener = Get-ListeningPid -Port $backendPortUsed
                throw "Port $backendPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port."
            }
            $currentListener = Get-ListeningPid -Port $backendPortUsed
            if ($currentListener) { throw "Port $backendPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port." }
            Write-Host "     Using port $backendPortUsed" -ForegroundColor Gray
        }

        Write-Host "[4/7] Starting Backend (uvicorn)..." -ForegroundColor Yellow
        
        # Запускаем через cmd /c start /b для полной detach от родительского процесса
        # Без redirect, т.к. redirect держит pipes открытыми и блокирует exit скрипта
        $backendCmd = "`"$Python`" -m uvicorn app.main:app --host 127.0.0.1 --port $backendPortUsed"
        if ($ReloadBackend) {
            $backendCmd += " --reload"
            Write-Host "     Hot-reload enabled" -ForegroundColor Gray
        }
        
        $backendStartArgs = @('/c', 'start', '/b', 'cmd', '/c', $backendCmd)
        $null = Start-Process -FilePath 'cmd.exe' -ArgumentList $backendStartArgs -WorkingDirectory $RepoRoot -WindowStyle $WindowStyle
        Write-Host "     Waiting for uvicorn to start..." -ForegroundColor Gray

        if (-not (Test-HttpEndpoint -Url "http://127.0.0.1:$backendPortUsed/api/v1/health" -TimeoutSec 60)) {
            throw "Backend failed to start."
        }
        
        # Сохраняем реальный PID uvicorn процесса (не cmd.exe)
        $backendRealPid = Get-ListeningPid -Port $backendPortUsed
        if ($backendRealPid) {
            $backendRealPid | Out-File -Encoding ascii -FilePath $BackendPidPath
            Write-Host "     Backend PID: $backendRealPid" -ForegroundColor Gray
        }
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
            Stop-IfListeningAndOurs -Port $uiPortUsed -Kind 'ui' -ContextDir $UiDir
            $currentListener = Get-ListeningPid -Port $uiPortUsed
            if ($currentListener) {
                throw "Port $uiPortUsed is busy (PID: $currentListener). Use -AutoPorts or free the port."
            }
            Write-Host "     Using port $uiPortUsed" -ForegroundColor Gray
        }

        # Используем --strictPort, чтобы vite не прыгал по портам сам
        # ВАЖНО: Запускаем через cmd /c start для полной detach от родительского процесса
        # Без redirect, т.к. redirect держит pipes открытыми и блокирует exit скрипта
        $uiArgs = @('/c', 'start', '/b', 'npm', 'run', 'dev', '--', '--port', "$uiPortUsed", '--strictPort')
        $null = Start-Process -FilePath 'cmd.exe' -ArgumentList $uiArgs -WorkingDirectory $UiDir -WindowStyle $WindowStyle

        # Ждём пока Vite начнёт слушать порт (более надёжно чем TCP probe на localhost)
        Write-Host "     Waiting for Vite to start listening on port $uiPortUsed..." -NoNewline
        $uiDeadline = (Get-Date).AddSeconds(60)
        $uiStarted = $false
        $uiRealPid = $null
        while ((Get-Date) -lt $uiDeadline) {
            $uiRealPid = Get-ListeningPid -Port $uiPortUsed
            if ($uiRealPid) {
                $uiStarted = $true
                Write-Host " OK" -ForegroundColor Green
                # Сохраняем реальный PID node процесса (не cmd.exe)
                $uiRealPid | Out-File -Encoding ascii -FilePath $UiPidPath
                Write-Host "     UI PID: $uiRealPid" -ForegroundColor Gray
                break
            }
            Start-Sleep -Milliseconds 500
            Write-Host "." -NoNewline
        }
        if (-not $uiStarted) {
            Write-Host " Timeout!" -ForegroundColor Yellow
            Write-Warning "UI did not start listening within 60s."
        }

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
        [Environment]::Exit(0)
    }
}
