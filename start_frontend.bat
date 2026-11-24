@echo off
setlocal EnableExtensions
title OpsLens Frontend
cd /d "%~dp0hmi_ui"
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js / npm not found. Install Node.js 18+ and run setup_windows.bat.
  pause
  exit /b 1
)
node tools\check_npm_deps.cjs
if errorlevel 1 (
  echo [ERROR] Frontend not started. Fix the error above and start again.
  pause
  exit /b 1
)
echo Starting Operator HMI (5173) and Engineering Console / Scenario Lab (5174)...
start "OpsLens Engineering Console (5174)" /D "%~dp0hmi_ui" cmd /k npm run dev:console
call npm run dev
pause
