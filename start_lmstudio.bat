@echo off
rem Checks LM Studio's Local Model API for OpsLens and starts it if it is off.
rem Optional: OpsLens still runs without it and labels its answers as not AI-generated.
setlocal EnableExtensions
title OpsLens - LM Studio check
cd /d "%~dp0"

set "LMS_URL=http://127.0.0.1:1234"
set "LMS_MODEL=qwen2.5-7b-instruct"
set "LMS_CONTEXT=8192"
if exist "edge_intelligence\.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("edge_intelligence\.env") do (
    if /i "%%A"=="EDGE_LLM_URL" if not "%%B"=="" set "LMS_URL=%%B"
    if /i "%%A"=="EDGE_LLM_MODEL" if not "%%B"=="" set "LMS_MODEL=%%B"
    if /i "%%A"=="OPSLENS_LMS_EXE" if not "%%B"=="" set "LMS_EXE=%%B"
  )
)
if /i "%LMS_URL:~-3%"=="/v1" set "LMS_URL=%LMS_URL:~0,-3%"
if /i "%LMS_URL:~-6%"==":11434" set "LMS_URL=http://127.0.0.1:1234"

rem The lms command comes with LM Studio. Look in PATH, then in the usual folder.
if not defined LMS_EXE for /f "delims=" %%P in ('where lms 2^>nul') do if not defined LMS_EXE set "LMS_EXE=%%P"
if not defined LMS_EXE if exist "%USERPROFILE%\.lmstudio\bin\lms.exe" set "LMS_EXE=%USERPROFILE%\.lmstudio\bin\lms.exe"

powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 2 '%LMS_URL%/v1/models' | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto :running

if not defined LMS_EXE (
  echo [WARN] LM Studio's Local Model API is not running at %LMS_URL%.
  echo        Open LM Studio, go to Local Model API or Developer, and start the server.
  echo        Or set OPSLENS_LMS_EXE in edge_intelligence\.env to the lms.exe path.
  echo        OpsLens will run with deterministic answers, labelled as not AI-generated.
  exit /b 0
)
rem Start on the port OpsLens uses. Without --port, LM Studio picks the port saved
rem in its own settings (for example 1235), and OpsLens would not find it.
set "LMS_PORT=1234"
for /f "delims=" %%P in ('powershell -NoProfile -Command "([uri]'%LMS_URL%').Port"') do set "LMS_PORT=%%P"
echo Starting LM Studio's Local Model API on port %LMS_PORT% with: %LMS_EXE%
"%LMS_EXE%" server start --port %LMS_PORT%
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 40;$i++){ try { Invoke-RestMethod -TimeoutSec 2 '%LMS_URL%/v1/models' | Out-Null; $ok=$true; break } catch { Start-Sleep -Milliseconds 500 } }; if($ok){exit 0}else{exit 1}"
if errorlevel 1 (
  echo [WARN] LM Studio did not answer on %LMS_URL% within 20 seconds.
  echo        In LM Studio, open Developer, set Server Port to %LMS_PORT% and start the server.
  echo        If it says the port is in use, close the other program on %LMS_PORT% or set
  echo        EDGE_LLM_URL in edge_intelligence\.env to the port LM Studio shows.
  exit /b 0
)

:running
echo [OK] LM Studio's Local Model API is running at %LMS_URL%/v1.
if defined LMS_EXE (
  echo Loading %LMS_MODEL% with a %LMS_CONTEXT%-token context, if it is not loaded yet...
  "%LMS_EXE%" load "%LMS_MODEL%" --context-length %LMS_CONTEXT% -y >nul 2>&1
  if errorlevel 1 echo [INFO] Could not load it from here. Load %LMS_MODEL% in LM Studio with Context Length %LMS_CONTEXT% or more.
)
powershell -NoProfile -Command "$m=$env:LMS_MODEL.ToLower() -replace '[^a-z0-9.]',''; $ids=(Invoke-RestMethod -TimeoutSec 3 '%LMS_URL%/v1/models').data.id; if(($ids | ForEach-Object { $_.ToLower() -replace '[^a-z0-9.]','' }) -match [regex]::Escape($m)){exit 0}else{Write-Host ('Models in LM Studio: ' + ($ids -join ', ')); exit 1}"
if errorlevel 1 (
  echo [WARN] Model %LMS_MODEL% is not available in LM Studio.
  echo        Download it in LM Studio, or set EDGE_LLM_MODEL in edge_intelligence\.env to a name listed above.
  exit /b 0
)
echo [OK] Model %LMS_MODEL% is available.
exit /b 0
