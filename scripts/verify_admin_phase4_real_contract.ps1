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

# Programme 017 `T1710`: this is the only Admin e2e with a real backend, and it now runs on a
# disposable PostgreSQL database created for this run and dropped in the `finally`. The name carries
# the run id so two runs cannot collide, and it obeys the same contract every launcher database
# obeys - `scripts/dev_database.py` refuses anything else, which is what makes the drop below safe.
$databaseName = "geov0_dev_$($TaskSlug -replace '_', '-')-$($runId.Substring(0, 8))"
$pgHost = if ([string]::IsNullOrWhiteSpace($env:GEO_DEV_PG_HOST)) { '127.0.0.1' } else { $env:GEO_DEV_PG_HOST }
$pgPort = if ([string]::IsNullOrWhiteSpace($env:GEO_DEV_PG_PORT)) { '5432' } else { $env:GEO_DEV_PG_PORT }
$pgUser = if ([string]::IsNullOrWhiteSpace($env:GEO_DEV_PG_USER)) { 'geo' } else { $env:GEO_DEV_PG_USER }
$pgPassword = if ([string]::IsNullOrWhiteSpace($env:GEO_DEV_PG_PASSWORD)) { 'geo' } else { $env:GEO_DEV_PG_PASSWORD }
$databaseUrl = "postgresql+asyncpg://${pgUser}:${pgPassword}@${pgHost}:${pgPort}/${databaseName}"
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
$databaseCreated = $false

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
        & $pythonExe scripts/dev_database.py ensure
        if ($LASTEXITCODE -ne 0) { throw "Creating the disposable database failed (exit $LASTEXITCODE)." }
        $databaseCreated = $true

        # The one migration entry, the same one `docker/docker-entrypoint.sh` calls. The
        # `alembic_version` precondition comes from `migrations/env.py` inside this run.
        & $pythonExe -m alembic -c migrations/alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'alembic upgrade head failed.' }

        # Real operations through the domain services, not rows written past them: participants,
        # trust lines, payments and a clearing, with the reconciliation baseline taken on empty
        # debts before the first payment (`scripts/seed_recipe.py`).
        & $pythonExe scripts/seed_db.py --source recipe --community riverside-town-50
        if ($LASTEXITCODE -ne 0) { throw 'Recipe seed failed.' }

        # The seed asserts its own acceptance; this asserts the same of the database the backend is
        # about to be pointed at, which is the check that would catch a half-written seed.
        & $pythonExe scripts/dev_database.py ready --community riverside-town-50
        if ($LASTEXITCODE -ne 0) { throw "The seeded database is not ready (exit $LASTEXITCODE)." }
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

    # The processes are stopped ABOVE this line, and the drop below is a plain DROP DATABASE that
    # `scripts/dev_database.py` refuses while any session is still connected. A database this run
    # did not create is never dropped - `$databaseCreated` is set only after `ensure` succeeded.
    $databaseDropped = $false
    if ($databaseCreated) {
        Push-Location $repoRoot
        try {
            & $pythonExe scripts/dev_database.py drop
            $databaseDropped = ($LASTEXITCODE -eq 0)
        } finally {
            Pop-Location
        }
    }

    foreach ($key in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], 'Process')
    }
    Write-Host 'Owned-process cleanup: complete'
    if (-not $databaseCreated) {
        Write-Host 'Disposable database cleanup: nothing was created'
    } elseif ($databaseDropped) {
        Write-Host "Disposable database cleanup: complete ($databaseName dropped)"
    } else {
        # Named, not swallowed: a database left behind is a leak the operator has to know about.
        Write-Warning "Disposable database cleanup: FAILED, $databaseName still exists. Drop it by hand."
    }
}
