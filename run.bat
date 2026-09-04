@echo off
REM EchoGist launcher (plan §5 / TD-3). cmd-only, idempotent. No .ps1 (blocked by
REM execution policy). Heavy host steps live here; Python-side provisioning and
REM the app run via the venv python.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- 0. Double-click detection (humane exit) ---
REM When launched from Explorer, the spawning shell's command line contains this
REM script's name (cmd /c "...run.bat"). When run from an open prompt or `call`ed
REM from win-smoke.bat, it does not. We only pause-on-exit in the double-click case
REM so the window stays readable, without hanging automated acceptance runs.
set "PAUSE_ON_EXIT="
echo "%cmdcmdline%" | find /i "%~nx0" >nul && set "PAUSE_ON_EXIT=1"

REM --- 1. Python check (manual prerequisite; never auto-installed) ---
where python >nul 2>&1
if errorlevel 1 (
  echo [EchoGist] Python is not on PATH.
  echo            Install Python 3.11 from https://www.python.org/downloads/ and re-run run.bat.
  goto :fail
)
REM `where` finding `python` does NOT mean an interpreter is there. Windows ships an
REM App Execution Alias stub in WindowsApps that resolves on PATH, exits non-zero and
REM prints "Python was not found; run without arguments to install from the Microsoft
REM Store". Tokenizing THAT with tokens=2 yields the word `was`, which the old check
REM reported as the detected version: it turns "no interpreter" into "wrong version"
REM (issue #1). So: honour the exit code, then require the token to look like a version
REM before believing it. Neither check alone is enough; a stub that changes its wording
REM still fails the format check, and a real interpreter that prints something odd on
REM stderr still passes it.
python --version >nul 2>&1
if errorlevel 1 (
  echo [EchoGist] `python` is on PATH but is not a working interpreter.
  echo            This is almost always the Microsoft Store stub in WindowsApps.
  echo            Install Python 3.11 from https://www.python.org/downloads/ ^(tick
  echo            "Add python.exe to PATH"^), or turn the stub off in
  echo            Settings ^> Apps ^> Advanced app settings ^> App execution aliases.
  goto :fail
)
set "PYVER="
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo !PYVER!| findstr /r /b "[0-9][0-9]*\.[0-9][0-9]*\." >nul
if errorlevel 1 (
  echo [EchoGist] Could not read a version number from `python --version`.
  echo            It said: !PYVER!
  echo            Install Python 3.11 from https://www.python.org/downloads/ and re-run.
  goto :fail
)
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

REM --- provision-only mode: scripts\win-smoke.bat provisions then drives the clip
REM     itself, so it must NOT block on the interactive menu. ---
if /i "%~1"=="--provision-only" (
  echo [EchoGist] Provision-only mode: skipping the interactive menu.
  goto :end
)

REM --- launch the app (the interactive menu) ---
"%VPY%" -m echogist
goto :end

:fail
echo.
echo [EchoGist] Startup aborted. Read the messages above for what to fix.
if defined PAUSE_ON_EXIT ( echo. & echo Press any key to close this window... & pause >nul )
endlocal
exit /b 1

:end
echo.
echo [EchoGist] Done.
if defined PAUSE_ON_EXIT ( echo Press any key to close this window... & pause >nul )
endlocal
