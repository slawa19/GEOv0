@echo off
setlocal

rem Keep output readable on Windows terminals.
chcp 65001 >nul

rem Runs GEO Simulator UI prototype (standalone Vite app).
rem Usage (cmd.exe):
rem   scripts\run_simulator_ui.cmd

set "REPO_ROOT=%~dp0.."
set "APP_DIR=%REPO_ROOT%\simulator-ui\v2"

if not exist "%APP_DIR%" goto :ERR_NO_DIR
if not exist "%APP_DIR%\package.json" goto :ERR_NO_PKG

rem Prefer PowerShell script: it detects an already-running server on the port,
rem and if the port is occupied by something else it picks the next free port.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\run_simulator_ui.ps1"
if errorlevel 1 goto :ERR_PWSH
goto :EOF

:ERR_NO_DIR
echo simulator-ui directory not found: %APP_DIR%
exit /b 1

:ERR_NO_PKG
echo simulator-ui package.json not found: %APP_DIR%\package.json
exit /b 1

:ERR_CD
echo Failed to cd into: %APP_DIR%
exit /b 1

:ERR_NPM
echo npm install failed
exit /b 1

:ERR_PWSH
echo Failed to start Simulator UI via PowerShell script.
exit /b 1


