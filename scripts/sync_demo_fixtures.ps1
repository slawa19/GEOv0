# Sync demo fixtures with graceful fallback.
#
# This script is intended to be used from npm hooks (predev/prebuild) in simulator-ui/v2.
# It must NOT break dev workflow if Python/venv is missing.

param(
    [switch]$Strict
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

$pythonCandidates = @(
    (Join-Path $repoRoot '.venv\Scripts\python.exe'),
    'py -3',
    'python'
)

function Resolve-Python {
    foreach ($candidate in $pythonCandidates) {
        try {
            if ($candidate -like '* *') {
                $parts = $candidate.Split(' ', 2)
                & $parts[0] $parts[1] --version 2>$null | Out-Null
            } else {
                if (Test-Path $candidate) {
                    return $candidate
                }
                & $candidate --version 2>$null | Out-Null
            }

            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
            # ignore
        }
    }

    return $null
}

try {
    $python = Resolve-Python

    if (-not $python) {
        Write-Warning 'Python not found. Demo fixtures not synced (using cached).'
        if ($Strict) { exit 1 }
        exit 0
    }

    $script = Join-Path $repoRoot 'admin-fixtures\tools\generate_simulator_demo_snapshots.py'
    if (-not (Test-Path $script)) {
        Write-Warning "Generator not found: $script. Demo fixtures not synced (using cached)."
        if ($Strict) { exit 1 }
        exit 0
    }

    Write-Host "Syncing demo fixtures (UAH) via: $python $script" -ForegroundColor Cyan

    if ($python -like '* *') {
        $parts = $python.Split(' ', 2)
        & $parts[0] $parts[1] $script --eq UAH
    } else {
        & $python $script --eq UAH
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Demo fixtures sync failed (non-zero exit). Using cached.'
        if ($Strict) { exit 1 }
        exit 0
    }
} catch {
    Write-Warning "Demo fixtures sync failed: $($_.Exception.Message). Using cached."
    if ($Strict) { exit 1 }
    exit 0
}
