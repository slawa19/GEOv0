$ErrorActionPreference = 'Stop'

Write-Host '== Python tests ==' 
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
  throw "Python venv not found at $python. Create it via: py -m venv .venv"
}
& $python -m pytest -q

Write-Host ''
Write-Host '== Admin UI unit tests ==' 
& npm --prefix admin-ui run test

Write-Host ''
Write-Host '== Admin UI production build (includes fixtures validation) ==' 
& npm --prefix admin-ui run build

Write-Host ''
Write-Host 'OK'
