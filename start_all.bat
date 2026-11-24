@echo off
setlocal EnableExtensions
title OpsLens launcher
cd /d "%~dp0"
if not exist "edge_intelligence\.venv\Scripts\python.exe" (
  echo First launch: initial setup is required. Running setup_windows.bat ...
  call "%~dp0setup_windows.bat" || exit /b 1
)
rem Installs nothing unless requirements.txt or package-lock.json changed.
"edge_intelligence\.venv\Scripts\python.exe" "edge_intelligence\tools\check_python_deps.py"
if errorlevel 1 (
  echo [ERROR] Python dependency update failed. OpsLens was not started.
  pause
  exit /b 1
)
node "hmi_ui\tools\check_npm_deps.cjs"
if errorlevel 1 (
  echo [ERROR] Frontend dependency update failed. OpsLens was not started.
  pause
  exit /b 1
)
call "%~dp0start_lmstudio.bat"
start "OpsLens Backend (8001)" /D "%~dp0" cmd /k start_backend.bat

echo Waiting for the backend to answer on http://127.0.0.1:8001/api/health ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 40;$i++){ try { Invoke-RestMethod -TimeoutSec 2 http://127.0.0.1:8001/api/health | Out-Null; $ok=$true; break } catch { Start-Sleep -Milliseconds 750 } }; if($ok){Write-Host '[OK] Backend is up.'} else {Write-Host '[WARN] Backend did not answer yet - check the backend window.'}"

start "OpsLens Frontend" /D "%~dp0" cmd /k start_frontend.bat
timeout /t 6 /nobreak >nul
start "" http://localhost:5173
start "" http://localhost:5174
echo.
echo  Operator HMI ............ http://localhost:5173
echo  Scenario Lab / SOPs ..... http://localhost:5174
echo  Backend API ............. http://127.0.0.1:8001/api/health
echo.
echo  Demo: open 5174, pick "The lift station pumps clog", START.
echo        Then watch 5173: the alarm, what happened, and what to check.
echo.
pause
