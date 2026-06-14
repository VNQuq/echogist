@echo off
REM EchoGist launcher (plan §5 / TD-3). cmd-only, idempotent. No .ps1 (blocked by
REM execution policy). Heavy host steps live here; Python-side provisioning and
REM the app run via the venv python.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- 1. Python check (manual prerequisite; never auto-installed) ---
where python >nul 2>&1
if errorlevel 1 (
  echo [EchoGist] Python is not on PATH.
  echo            Install Python 3.11 from https://www.python.org/downloads/ and re-run run.bat.
  goto :fail
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo [EchoGist] Found Python !PYVER!
echo !PYVER! | findstr /b /c:"3.11." >nul
if errorlevel 1 (
  echo [EchoGist] EchoGist needs Python 3.11.x ^(found !PYVER!^).
  echo            Get it from https://www.python.org/downloads/release/python-3110/ and re-run.
  goto :fail
)

REM --- 2. venv (create/reuse; call its python by absolute path) ---
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" (
  echo [EchoGist] Creating virtual environment...
  python -m venv "%VENV%"
  if errorlevel 1 ( echo [EchoGist] Failed to create the virtual environment. & goto :fail )
)

REM --- 3. deps (hash-pinned lockfile; never -U) ---
if not exist "%~dp0requirements.lock" (
  echo [EchoGist] requirements.lock is missing.
  echo            Generate it on a dev machine with: scripts\lock-deps
  goto :fail
)
echo [EchoGist] Installing pinned dependencies...
"%VPY%" -m pip install --quiet --require-hashes -r "%~dp0requirements.lock"
if errorlevel 1 ( echo [EchoGist] Dependency install failed. See the messages above. & goto :fail )

REM --- 4-6. provisioning: output dirs, model fetch (TD-1), GPU preflight (F8) ---
"%VPY%" -m echogist.provision
if errorlevel 1 ( echo [EchoGist] Provisioning failed. See the diagnostic above. & goto :fail )

REM --- launch the app (menu lands in T9) ---
"%VPY%" -m echogist
goto :end

:fail
echo.
echo [EchoGist] Startup aborted.
endlocal
exit /b 1

:end
endlocal
