@echo off
setlocal EnableExtensions
title OpsLens - first-time setup
cd /d "%~dp0"
echo ============================================================
echo   OpsLens first-time setup (Windows)
echo   Creates edge_intelligence\.venv and installs hmi_ui packages.
echo   Safe to run again; it only adds what is missing.
echo ============================================================
echo.

rem ---- 1. Python ------------------------------------------------------------
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
  echo [ERROR] Python 3.10+ was not found.
  echo         Install it from https://www.python.org/downloads/ and tick
  echo         "Add python.exe to PATH", then run this script again.
  goto :fail
)
%PYEXE% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.10 or newer is required.
  %PYEXE% --version
  goto :fail
)
echo [OK] Python found:
%PYEXE% --version

rem ---- 2. Backend virtual environment --------------------------------------
if not exist "edge_intelligence\.venv\Scripts\python.exe" (
  echo Creating virtual environment edge_intelligence\.venv ...
  %PYEXE% -m venv "edge_intelligence\.venv" || goto :fail
  "edge_intelligence\.venv\Scripts\python.exe" -m pip install --upgrade pip >nul
)
set "VPY=%~dp0edge_intelligence\.venv\Scripts\python.exe"

echo Checking backend packages (installs only if requirements.txt changed)...
"%VPY%" "edge_intelligence\tools\check_python_deps.py"
if errorlevel 1 (
  echo.
  echo [WARN] Full install failed, usually PyTorch or sentence-transformers.
  echo        Installing the core set instead. OpsLens will use BM25 SOP retrieval.
  "%VPY%" "edge_intelligence\tools\check_python_deps.py" --profile core || goto :fail
)
"%VPY%" -c "import pytest" >nul 2>&1 || "%VPY%" -m pip install "pytest>=8" >nul

rem ---- 3. Backend configuration --------------------------------------------
if not exist "edge_intelligence\.env" (
  copy /y "edge_intelligence\.env.example" "edge_intelligence\.env" >nul
  echo [OK] Created edge_intelligence\.env from .env.example - review it before going live.
) else (
  echo [OK] edge_intelligence\.env already exists - not changed.
)

rem ---- 4. Frontend ---------------------------------------------------------
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js / npm not found. Install Node.js 18 LTS or newer from https://nodejs.org/
  goto :fail
)
echo Checking frontend packages (installs only if package-lock.json changed)...
node "hmi_ui\tools\check_npm_deps.cjs" || goto :fail

rem ---- 5. Optional services (information only) -----------------------------
echo.
echo Checking optional services (OpsLens runs without them):
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 2 http://127.0.0.1:1234/v1/models | Out-Null; Write-Host '[OK] LM Studio Local Model API is running on port 1234' } catch { Write-Host '[INFO] LM Studio is not running. Optional. In LM Studio download Qwen2.5-7B-Instruct and start the Local Model API.' }"
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 2 http://localhost:6333/collections | Out-Null; Write-Host '[OK] Qdrant is running on port 6333' } catch { Write-Host '[INFO] Qdrant is not running. Optional - BM25 keyword retrieval will be used. To start: docker compose up -d qdrant' }"

echo.
echo ============================================================
echo  Setup complete. Next: double-click start_all.bat
echo ============================================================
pause
exit /b 0

:fail
echo.
echo Setup did not finish. Fix the message above and run setup_windows.bat again.
pause
exit /b 1
