[CmdletBinding()]
param(
    [string]$TaskSlug = 'phase4_admin_real_contract',
    [int]$BackendPort = 18141,
    [int]$UiPort = 41741
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($TaskSlug -notmatch '^[a-z0-9][a-z0-9_-]{2,63}$') {
    throw 'TaskSlug must contain only lowercase letters, digits, underscores, or hyphens.'
}
if ($BackendPort -eq $UiPort -or $BackendPort -lt 1024 -or $UiPort -lt 1024) {
    throw 'BackendPort and UiPort must be distinct non-privileged ports.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$artifactRoot = Join-Path $repoRoot ".local-run\test-runs\$TaskSlug"
$runId = [guid]::NewGuid().ToString('N')
$runRoot = Join-Path $artifactRoot "run-$runId"
$expectedArtifactRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '.local-run\test-runs'))
$resolvedRunRoot = [System.IO.Path]::GetFullPath($runRoot)
if (-not $resolvedRunRoot.StartsWith($expectedArtifactRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Resolved output path escaped the approved test-runs root.'
}

New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw 'Repository Python virtual environment was not found.'
}
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
}
if (-not $nodeCommand) {
    throw 'Node.js executable was not found.'
}
$nodeExe = $nodeCommand.Source
$viteCli = Join-Path $repoRoot 'admin-ui\node_modules\vite\bin\vite.js'
$playwrightCli = Join-Path $repoRoot 'admin-ui\node_modules\@playwright\test\cli.js'
if (-not (Test-Path -LiteralPath $viteCli -PathType Leaf) -or -not (Test-Path -LiteralPath $playwrightCli -PathType Leaf)) {
    throw 'Admin UI dependencies are not installed.'
}

$listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
$backendPortFree = -not ($listeners.Port -contains $BackendPort)
$uiPortFree = -not ($listeners.Port -contains $UiPort)
Write-Host "Port preflight: backend_free=$backendPortFree ui_free=$uiPortFree"
if (-not $backendPortFree -or -not $uiPortFree) {
    throw 'A required Phase 4 smoke port is already in use.'
}

$databasePath = Join-Path $runRoot 'admin-real.db'
$databaseUrl = 'sqlite+aiosqlite:///' + $databasePath.Replace('\', '/')
$backendOrigin = "http://127.0.0.1:$BackendPort"
$uiOrigin = "http://127.0.0.1:$UiPort"
$adminToken = 'phase4-' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$jwtSecret = 'phase4-jwt-' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')

$environment = @{
    ENV = 'test'
    DATABASE_URL = $databaseUrl
    ADMIN_TOKEN = $adminToken
    JWT_SECRET = $jwtSecret
    PYTHONPATH = $repoRoot
    VITE_API_MODE = 'real'
    VITE_API_BASE_URL = $backendOrigin
    VITE_ADMIN_TOKEN = $adminToken
    PHASE4_ADMIN_TOKEN = $adminToken
    PHASE4_BACKEND_ORIGIN = $backendOrigin
    PHASE4_UI_PORT = [string]$UiPort
    PHASE4_PLAYWRIGHT_OUTPUT = (Join-Path $runRoot 'playwright')
}
$previousEnvironment = @{}
foreach ($key in $environment.Keys) {
    $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $environment[$key], 'Process')
}

$backendProcess = $null
$uiProcess = $null

function Wait-LocalEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$TimeoutSeconds = 90
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "$Name readiness: true"
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "$Name readiness: false"
}

try {
    Push-Location $repoRoot
    try {
        & $pythonExe scripts/init_sqlite_db.py
        if ($LASTEXITCODE -ne 0) { throw 'SQLite schema initialization failed.' }

        & $pythonExe scripts/seed_db.py --source fixtures
        if ($LASTEXITCODE -ne 0) { throw 'Fixture seed failed.' }
    } finally {
        Pop-Location
    }

    $backendProcess = Start-Process -FilePath $pythonExe `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$BackendPort) `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runRoot 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $runRoot 'backend.stderr.log') `
        -PassThru

    $uiProcess = Start-Process -FilePath $nodeExe `
        -ArgumentList @($viteCli, '--host', '127.0.0.1', '--port', [string]$UiPort, '--strictPort') `
        -WorkingDirectory (Join-Path $repoRoot 'admin-ui') `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runRoot 'ui.stdout.log') `
        -RedirectStandardError (Join-Path $runRoot 'ui.stderr.log') `
        -PassThru

    Wait-LocalEndpoint -Uri "$backendOrigin/health" -Name 'Backend'
    Wait-LocalEndpoint -Uri $uiOrigin -Name 'Admin UI'

    Push-Location (Join-Path $repoRoot 'admin-ui')
    try {
        & $nodeExe $playwrightCli test --config playwright.phase4-real.config.ts
        if ($LASTEXITCODE -ne 0) { throw 'Phase 4 real-contract Playwright smoke failed.' }
    } finally {
        Pop-Location
    }

    Write-Host 'Phase 4 real-contract smoke: passed'
} finally {
    foreach ($ownedProcess in @($uiProcess, $backendProcess)) {
        if ($null -ne $ownedProcess -and -not $ownedProcess.HasExited) {
            Stop-Process -Id $ownedProcess.Id -Force -ErrorAction SilentlyContinue
            $ownedProcess.WaitForExit(5000) | Out-Null
        }
    }

    foreach ($databaseArtifact in @($databasePath, "$databasePath-wal", "$databasePath-shm")) {
        $resolvedArtifact = [System.IO.Path]::GetFullPath($databaseArtifact)
        if ($resolvedArtifact.StartsWith($resolvedRunRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $resolvedArtifact -PathType Leaf)) {
            Remove-Item -LiteralPath $resolvedArtifact -Force
        }
    }

    foreach ($key in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], 'Process')
    }
    Write-Host 'Owned-process cleanup: complete'
    Write-Host 'Disposable database cleanup: complete'
}
