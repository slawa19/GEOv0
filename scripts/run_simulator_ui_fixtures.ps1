<#
.SYNOPSIS
  Start GEO Simulator UI (fixtures/demo mode).

.DESCRIPTION
  Convenience wrapper around scripts/run_simulator_ui.ps1.
  This starts simulator-ui/v2 without requiring a backend.

.EXAMPLE
  ./scripts/run_simulator_ui_fixtures.ps1

.EXAMPLE
  ./scripts/run_simulator_ui_fixtures.ps1 -Port 5176 -HostName 127.0.0.1
#>
[CmdletBinding()]
param(
  [int]$Port = 5176,
  [string]$HostName = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$inner = Join-Path $repoRoot 'scripts/run_simulator_ui.ps1'

& $inner -Port $Port -HostName $HostName -Mode fixtures
