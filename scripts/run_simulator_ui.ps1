$ErrorActionPreference = 'Stop'

# Runs GEO Simulator UI prototype (standalone Vite app).
# Usage (PowerShell):
#   ./scripts/run_simulator_ui.ps1

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$appDir = Join-Path $repoRoot 'simulator-ui'

if (-not (Test-Path $appDir)) {
  throw "simulator-ui directory not found: $appDir"
}

Set-Location $appDir

if (-not (Test-Path (Join-Path $appDir 'node_modules'))) {
  Write-Host 'Installing dependencies (npm install)...'
  npm install
}

Write-Host ''
Write-Host 'Starting GEO Simulator UI (Vite dev server)...'
Write-Host 'Open in browser: http://localhost:5176/'
Write-Host ''

npm run dev


