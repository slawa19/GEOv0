[CmdletBinding()]
param(
    [string]$Python,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$TaskSlug = 'verify-local',
    [switch]$StaticDiagnostics,
    [string[]]$BackendSelector = @(),
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_]*$')]
    [string]$BackendMarker,
    [switch]$IncludeExpensive,
    [switch]$BackendOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Resolve-PythonExecutable {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
            throw "Python executable not found: $Python"
        }
        return (Resolve-Path -LiteralPath $Python).Path
    }

    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw 'Python not found. Create .venv or pass -Python with an executable path.'
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Invoke-RequiredStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Command
    )

    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Name failed with exit code $exitCode"
    }
}

function Invoke-DiagnosticStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Command
    )

    Write-Host "`n== $Name (diagnostic, non-blocking) ==" -ForegroundColor Yellow
    & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Warning "$Name reported existing debt (exit code $exitCode). This is not a required gate yet."
    }
}

$pythonExe = Resolve-PythonExecutable
if (-not $BackendOnly) {
    Assert-CommandAvailable -Name 'npm'
}

$localRunRoot = Join-Path $repoRoot '.local-run'
$testRunsRoot = Join-Path $localRunRoot 'test-runs'
$taskRoot = Join-Path $testRunsRoot $TaskSlug
$baseTemp = Join-Path $taskRoot 'pytest'
$artifactRoot = Join-Path $taskRoot 'artifacts'
$defaultTestDb = "sqlite+aiosqlite:///./.local-run/test-runs/$TaskSlug/test.db"
$previousTestDatabaseUrl = $env:TEST_DATABASE_URL
$previousTestArtifactRoot = $env:GEO_TEST_ARTIFACT_ROOT

New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null

try {
    if (-not $env:TEST_DATABASE_URL) {
        $env:TEST_DATABASE_URL = $defaultTestDb
    }
    $env:GEO_TEST_ARTIFACT_ROOT = $artifactRoot

    Push-Location $repoRoot
    try {
        if ($BackendSelector.Count -gt 0) {
            Invoke-RequiredStep -Name 'Backend selector safety guard' -Command {
                & $pythonExe scripts/validate_pytest_selectors.py --repo-root $repoRoot -- @BackendSelector
            }
        }
        Invoke-RequiredStep -Name 'Test database safety guard' -Command {
            $databaseGuardArgs = @()
            if ($BackendMarker -eq 'postgres') {
                $databaseGuardArgs += @('--require-backend', 'postgresql')
            }
            & $pythonExe scripts/validate_test_database_url.py @databaseGuardArgs
        }
        Invoke-RequiredStep -Name 'Backend tests (pytest)' -Command {
            $pytestCache = Join-Path $taskRoot 'cache'
            $pytestArgs = @(
                '-m', 'pytest',
                '--basetemp', $baseTemp,
                '-o', "cache_dir=$pytestCache",
                '-q'
            )
            if ($BackendMarker) {
                $pytestArgs += @('-m', $BackendMarker)
            }
            elseif ($IncludeExpensive) {
                $pytestArgs += @('-m', 'not postgres')
            }
            else {
                $pytestArgs += @('-m', 'not slow and not postgres')
            }
            if ($BackendSelector.Count -gt 0) {
                $pytestArgs += '--'
                $pytestArgs += $BackendSelector
            }
            & $pythonExe @pytestArgs
        }
        if (-not $BackendOnly) {
            Invoke-RequiredStep -Name 'Single Alembic migration head' -Command {
                & $pythonExe scripts/check_alembic_heads.py
            }
            Invoke-RequiredStep -Name 'Admin UI lint' -Command {
                & npm --prefix admin-ui run lint
            }
            Invoke-RequiredStep -Name 'Admin UI unit tests' -Command {
                & npm --prefix admin-ui run test
            }
            Invoke-RequiredStep -Name 'Admin UI production build' -Command {
                & npm --prefix admin-ui run build
            }
            Invoke-RequiredStep -Name 'Simulator UI v2 correctness lint' -Command {
                & npm --prefix simulator-ui/v2 run lint
            }
            Invoke-RequiredStep -Name 'Simulator UI v2 typecheck' -Command {
                & npm --prefix simulator-ui/v2 run typecheck
            }
            Invoke-RequiredStep -Name 'Simulator UI v2 unit tests' -Command {
                & npm --prefix simulator-ui/v2 run test:unit
            }
            Invoke-RequiredStep -Name 'Simulator UI v2 production build' -Command {
                & npm --prefix simulator-ui/v2 run build
            }
        }

        if ($StaticDiagnostics) {
            Write-Host "`nLocal diagnostics do not gate this command; CI separately requires pinned Ruff while Black remains non-blocking." -ForegroundColor Yellow
            Write-Host 'Mypy is not configured in this repository and is not run.' -ForegroundColor Yellow
            Invoke-DiagnosticStep -Name 'Ruff' -Command {
                & $pythonExe -m ruff check app migrations --no-cache
            }
            Invoke-DiagnosticStep -Name 'Black' -Command {
                & $pythonExe -m black --check app migrations
            }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:TEST_DATABASE_URL = $previousTestDatabaseUrl
    $env:GEO_TEST_ARTIFACT_ROOT = $previousTestArtifactRoot
}

if ($BackendOnly) {
    Write-Host "`nRequired backend validation passed." -ForegroundColor Green
}
else {
    Write-Host "`nRequired local validation passed." -ForegroundColor Green
}
exit 0
