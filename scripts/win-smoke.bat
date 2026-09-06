@echo off
REM EchoGist cold-Windows acceptance (plan §12.1 / §12.3 / T13).
REM
REM The ship-target gate: run on Windows before a release-worthy commit. It exercises
REM what only Windows validates — run.bat provisioning (venv, --require-hashes install,
REM model fetch, GPU preflight / cuDNN-cuBLAS DLL load), the bundled ffmpeg extract,
REM and a real-GPU transcribe — driving one canned clip through to its artifacts.
REM
REM The one paid SUMMARIZE call is killswitch-gated: it runs only when
REM ECHOGIST_LIVE_SMOKE=1 and ANTHROPIC_API_KEY are both set (otherwise the smoke
REM stays fully offline and still validates the mp3 + transcript artifacts).
REM
REM Place a short clip at tests\fixtures\audio\smoke.* (not committed) or pass one
REM through, e.g.:  scripts\win-smoke.bat --clip C:\path\to\clip.mp4
REM
REM THIS FILE is the entry point, not scripts\smoke_run.py. The .py needs the venv
REM interpreter and a provisioned tree, so double-clicking it runs the system python,
REM fails on the first import and takes the window with it. Double-click THIS instead
REM (or run it from a prompt) — it provisions first and holds the window open at the
REM end so the result is readable.
setlocal
cd /d "%~dp0\.."
set "RC=0"

REM Provision without launching the interactive menu, then drive the clip ourselves.
call run.bat --provision-only
if errorlevel 1 (
  echo [win-smoke] run.bat provisioning FAILED.
  set "RC=1"
  goto :finish
)

set "VPY=%~dp0..\.venv\Scripts\python.exe"
"%VPY%" "%~dp0smoke_run.py" %*
if errorlevel 1 (
  echo [win-smoke] clip acceptance FAILED.
  set "RC=1"
  goto :finish
)

echo [win-smoke] PASS - ship target works.

:finish
REM Launched from Explorer (a double-click), cmd closes the console the moment this
REM script ends and the operator sees neither the PASS line nor the failure that
REM produced it. CMDCMDLINE holds this script's name only in that case: from an
REM existing prompt it is just the interpreter, so the gate still runs unattended
REM without swallowing a keystroke.
echo %CMDCMDLINE% | find /i "%~nx0" >nul && pause
endlocal & exit /b %RC%
