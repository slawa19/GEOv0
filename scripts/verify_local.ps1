[CmdletBinding()]
param(
    [string]$Python,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$TaskSlug = 'verify-local',
    [switch]$StaticDiagnostics,
    [string[]]$BackendSelector = @(),
    [switch]$IncludeExpensive,
    [switch]$BackendOnly,
    [switch]$UiOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# `-UiOnly` IS THE OTHER HALF OF `-BackendOnly`, ADDED 2026-09-21 (programme 017, T1701). The
# required CI gate used to be one job running this script with neither switch; it now runs as two,
# because GitHub Actions service containers - and therefore PostgreSQL - are not available to
# Windows runners, and the backend half has to see PostgreSQL on every pull request. The UI half
# keeps its Windows runner and passes `-UiOnly`.
#
# The two refusals below exist because a switch that silently does nothing is the false green this
# repository keeps rediscovering (AGENTS.md §9): `-BackendOnly -UiOnly` together would run no gate
# at all and still exit 0, and `-UiOnly -BackendSelector ...` would report success without having
# collected the selected tests.
if ($BackendOnly -and $UiOnly) {
    throw 'Pass -BackendOnly or -UiOnly, not both: together they would run no gate at all and still exit 0. Pass neither to run both halves.'
}
if ($UiOnly) {
    $ignoredBackendArguments = @()
    if ($BackendSelector.Count -gt 0) { $ignoredBackendArguments += '-BackendSelector' }
    if ($IncludeExpensive) { $ignoredBackendArguments += '-IncludeExpensive' }
    if ($ignoredBackendArguments.Count -gt 0) {
        throw "-UiOnly runs no backend step, so $($ignoredBackendArguments -join ', ') would be accepted and ignored. Drop the switch, or drop -UiOnly and let the backend half run."
    }
}

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

# The runner resolves Python only for work it drives itself. That is NOT the same as "-UiOnly needs
# no Python": the Simulator UI v2 production build has a prebuild step (sync:demo-fixtures:strict)
# whose generator imports app.core.simulator, and it finds its own interpreter on PATH. Measured
# 2026-09-21 by the first run of the split gate, which died on ModuleNotFoundError: pydantic. A UI
# job that installs no Python dependencies will fail there, not here.
$pythonExe = $null
if ((-not $UiOnly) -or $StaticDiagnostics) {
    $pythonExe = Resolve-PythonExecutable
}
if (-not $BackendOnly) {
    Assert-CommandAvailable -Name 'npm'
}

$localRunRoot = Join-Path $repoRoot '.local-run'
$testRunsRoot = Join-Path $localRunRoot 'test-runs'
$taskRoot = Join-Path $testRunsRoot $TaskSlug
$baseTemp = Join-Path $taskRoot 'pytest'
$artifactRoot = Join-Path $taskRoot 'artifacts'
# THE TIER RUNS ONLY ON POSTGRESQL (017 stage 2c, T1702). Until then an unset TEST_DATABASE_URL
# meant a SQLite file under this task's directory, and PostgreSQL was a second tier behind
# `-BackendMarker postgres`. Both are gone. An unset URL is now DERIVED from the task slug:
#
#     postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_<TaskSlug>
#
# WHY DERIVING IS SAFE, WHICH IS THE WHOLE QUESTION, because deriving also means setting the
# destructive-reset opt-in on the operator's behalf:
#   * the name is `geov0_test_` + a slug that `ValidatePattern` above has already restricted to
#     [A-Za-z0-9_-], so it matches the guard's `geov0_test_*` rule BY CONSTRUCTION and cannot name a
#     developer database (`geov0`, `geov0_dev_*`), carry a quote, a slash or a second URL part; a slug
#     with the reserved `__` is still refused by the guard, opt-in or not;
#   * the host is the literal 127.0.0.1 - this machine - and the credentials are the documented
#     local convention (AGENTS.md section 5), so a wrong server cannot be reached by accident;
#   * the tier creates that database itself if it is missing (tests/migrated_schema.py,
#     ensure_tier_database), so there is nothing else it could be;
#   * the opt-in is set ONLY in the derived branch. A URL handed in from outside keeps needing
#     GEO_TEST_ALLOW_DB_RESET=1 from whoever handed it in, exactly as before; the guard step below
#     refuses it otherwise. Both variables are restored when the runner exits.
# `127.0.0.1`, not `localhost`: see AGENTS.md section 5 (2 s per connection over IPv6 first).
$derivedTestDb = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$TaskSlug"
$previousTestDatabaseUrl = $env:TEST_DATABASE_URL
$previousAllowDbReset = $env:GEO_TEST_ALLOW_DB_RESET
$previousUseMigratedSchema = $env:GEO_TEST_USE_MIGRATED_SCHEMA
$previousTestArtifactRoot = $env:GEO_TEST_ARTIFACT_ROOT

New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null

try {
    if (-not $UiOnly) {
        if (-not $env:TEST_DATABASE_URL) {
            $env:TEST_DATABASE_URL = $derivedTestDb
            $env:GEO_TEST_ALLOW_DB_RESET = '1'
            Write-Host "TEST_DATABASE_URL not set: using the derived $($derivedTestDb -replace '//geo:geo@', '//geo:***@')" -ForegroundColor Cyan
        }
        # The tier builds its schema with the migrations, as CI does, not with `create_all`: the two
        # differ (87 against 90 constraints, measured T1534), and only the migrated one is deployed.
        if (-not $env:GEO_TEST_USE_MIGRATED_SCHEMA) {
            $env:GEO_TEST_USE_MIGRATED_SCHEMA = '1'
        }
    }
    $env:GEO_TEST_ARTIFACT_ROOT = $artifactRoot

    Push-Location $repoRoot
    try {
        if (-not $UiOnly) {
            if ($BackendSelector.Count -gt 0) {
                Invoke-RequiredStep -Name 'Backend selector safety guard' -Command {
                    & $pythonExe scripts/validate_pytest_selectors.py --repo-root $repoRoot -- @BackendSelector
                }
            }
            Invoke-RequiredStep -Name 'Test database safety guard' -Command {
                # Always PostgreSQL: there is no other tier any more (017 stage 2c).
                & $pythonExe scripts/validate_test_database_url.py --require-backend postgresql
            }
            Invoke-RequiredStep -Name 'Backend tests (pytest)' -Command {
                $pytestCache = Join-Path $taskRoot 'cache'
                $pytestArgs = @(
                    '-m', 'pytest',
                    '--basetemp', $baseTemp,
                    '-o', "cache_dir=$pytestCache",
                    '-q'
                )
                # NOTHING IS EXCLUDED FROM THE TIER BEYOND `slow`, and both deletions are worth naming.
                # Until 2026-09-12 every branch also appended `and not b4_counterexample`, which took
                # the 107 programme-015 step-2 counterexamples out of every tier while the debt journal
                # did not exist; they went green through the journal and the exclusion left with the
                # marker. Until 017 stage 2c the default also subtracted `postgres`, which ran the
                # PostgreSQL tests as a second tier behind `-BackendMarker postgres`; that tier and the
                # parameter are gone, and every database test runs on the PostgreSQL tier. With
                # -IncludeExpensive nothing at all is excluded.
                if (-not $IncludeExpensive) {
                    $pytestArgs += @('-m', 'not slow')
                }
                if ($BackendSelector.Count -gt 0) {
                    $pytestArgs += '--'
                    $pytestArgs += $BackendSelector
                }
                & $pythonExe @pytestArgs
            }
            # THIS CHECK IS BACKEND WORK AND IT USED TO RUN IN THE UI HALF (moved 2026-09-21,
            # T1701). It asks whether `migrations/versions/` still has exactly one head; it needs
            # no npm and says nothing about either UI. It sat inside the `-not $BackendOnly` block,
            # so the day the required gate split into a backend job and a UI job it would have
            # travelled with the UI half and left migrations unguarded on the job that owns them.
            Invoke-RequiredStep -Name 'Single Alembic migration head' -Command {
                & $pythonExe scripts/check_alembic_heads.py
            }
        }
        if (-not $BackendOnly) {
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
    $env:GEO_TEST_ALLOW_DB_RESET = $previousAllowDbReset
    $env:GEO_TEST_USE_MIGRATED_SCHEMA = $previousUseMigratedSchema
    $env:GEO_TEST_ARTIFACT_ROOT = $previousTestArtifactRoot
}

if ($BackendOnly) {
    Write-Host "`nRequired backend validation passed." -ForegroundColor Green
}
elseif ($UiOnly) {
    Write-Host "`nRequired UI validation passed. The backend half did not run here." -ForegroundColor Green
}
else {
    Write-Host "`nRequired local validation passed." -ForegroundColor Green
}
exit 0
