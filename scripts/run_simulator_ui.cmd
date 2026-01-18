@echo off
setlocal

rem Keep output readable on Windows terminals.
chcp 65001 >nul

rem Runs GEO Simulator UI prototype (standalone Vite app).
rem Usage (cmd.exe):
rem   scripts\run_simulator_ui.cmd

set "APP_DIR=%~dp0..\simulator-ui"

if not exist "%APP_DIR%" goto :ERR_NO_DIR
if not exist "%APP_DIR%\package.json" goto :ERR_NO_PKG

cd /d "%APP_DIR%" || goto :ERR_CD

if exist "node_modules\" goto :RUN
echo Installing dependencies (npm install)...
call npm install
if errorlevel 1 goto :ERR_NPM

echo.
echo Starting GEO Simulator UI (Vite dev server)...
echo Open in browser: http://localhost:5176/
echo.

:RUN
call npm run dev
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


