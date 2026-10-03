<#
.SYNOPSIS
Per-test coverage measurement of the backend tier (programme 025, T2501). DEBUG PATH, NOT A GATE.

.DESCRIPTION
Runs the default backend tier (`-m "not slow"`, as scripts/verify_local.ps1 does; -IncludeExpensive drops
the marker filter with the same semantics) under pytest-cov with
`--cov=app --cov=tests --cov-branch --cov-context=test`, concurrency greenlet,thread, and writes
`.coverage`, `junit.xml`, `pytest.log` and `exit.txt` (pytest exit code, elapsed time, HEAD SHA) to
`.local-run/test-runs/<TaskSlug>/coverage/`, which is emptied first so no earlier run's file survives.
Analyse the result with `scripts/test_asset/analyze.py <that dir>`; it refuses a run whose exit.txt is
missing, non-zero or from another HEAD (exit 6). A full deletion acceptance needs -IncludeExpensive:
verdict rows of deselected `slow` tests are otherwise unmeasured (analyze.py exit 5).

Promoted from specs/025-test-asset-consolidation/evidence-2026-09-28/measure/run.ps1 + coveragerc
(dated evidence, kept unchanged). Changes: no hard-coded paths or database name; the database is
derived from the task slug exactly like verify_local.ps1 (`geov0_test_<TaskSlug>` on 127.0.0.1, reset
opt-in only for the derived name) and checked by scripts/validate_test_database_url.py; `tests` is
measured too, ONLY as a sentinel: every test that ran has its own body lines recorded, so a test with
no run context is a lost measurement and analyze.py can tell it from a test with zero app coverage.

WHAT IT DOES NOT SEE (AGENTS.md section 12): a Python subprocess started with an environment that lacks
pytest-cov's COV_CORE_* variables (a child that inherits them IS measured and credited to the active
context via COV_CORE_CONTEXT; one started during a fixture goes to `|setup`); lines run by a thread or
task outliving its test land in whichever context is active (hypothesis); SQL, triggers and the
database's own behaviour; timing and race schedules. Tracing slows the tier, so its durations are NOT T0 (spec 025, section on time: T0 is measured without tracing). The exit
code of pytest is recorded, not judged: a red test is a fact of the measurement, not of this tool.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$')]
    [string]$TaskSlug,
    [string[]]$Selector = @(),
    [switch]$IncludeExpensive,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Python) {
    $Python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$out = Join-Path $repoRoot ".local-run\test-runs\$TaskSlug\coverage"
if (Test-Path -LiteralPath $out) { Remove-Item -Recurse -Force -LiteralPath $out }  # this task's directory only
New-Item -ItemType Directory -Force -Path (Join-Path $out 'artifacts') | Out-Null
$rc = Join-Path $out 'coveragerc'
Set-Content -Path $rc -Encoding utf8 -Value "[run]`nbranch = True`nconcurrency = greenlet,thread`n"

$saved = @{}
foreach ($n in 'TEST_DATABASE_URL', 'GEO_TEST_ALLOW_DB_RESET', 'GEO_TEST_USE_MIGRATED_SCHEMA', 'GEO_TEST_ARTIFACT_ROOT', 'COVERAGE_FILE') {
    $saved[$n] = [Environment]::GetEnvironmentVariable($n)
}
try {
    if (-not $env:TEST_DATABASE_URL) {
        $env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$TaskSlug"
        $env:GEO_TEST_ALLOW_DB_RESET = '1'
    }
    $env:GEO_TEST_USE_MIGRATED_SCHEMA = '1'
    $env:GEO_TEST_ARTIFACT_ROOT = Join-Path $out 'artifacts'
    $env:COVERAGE_FILE = Join-Path $out '.coverage'
    Push-Location $repoRoot
    try {
        & $Python scripts/validate_test_database_url.py --require-backend postgresql
        if ($LASTEXITCODE -ne 0) { throw "test database guard refused TEST_DATABASE_URL (exit $LASTEXITCODE)" }
        $pytestArgs = @('-m', 'pytest', '--basetemp', (Join-Path $out 'pytest'), '-o', "cache_dir=$(Join-Path $out 'cache')", '-q',
            '--cov=app', '--cov=tests', '--cov-branch', '--cov-context=test', "--cov-config=$rc", '--cov-report=',
            '--durations=0', '--durations-min=0.05', "--junitxml=$(Join-Path $out 'junit.xml')")
        if (-not $IncludeExpensive) { $pytestArgs += @('-m', 'not slow') }
        if ($Selector.Count -gt 0) { $pytestArgs += '--'; $pytestArgs += $Selector }
        $start = Get-Date
        & $Python @pytestArgs *> (Join-Path $out 'pytest.log')
        $code = $LASTEXITCODE
        $line = "exit=$code elapsed_s=$([int]((Get-Date) - $start).TotalSeconds) head=$(git rev-parse HEAD)"
        $line | Tee-Object -FilePath (Join-Path $out 'exit.txt')
        exit $code
    }
    finally { Pop-Location }
}
finally {
    foreach ($n in $saved.Keys) { [Environment]::SetEnvironmentVariable($n, $saved[$n]) }
}
