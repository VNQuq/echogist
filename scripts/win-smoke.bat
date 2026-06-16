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
setlocal
cd /d "%~dp0\.."

REM Provision without launching the interactive menu, then drive the clip ourselves.
call run.bat --provision-only
if errorlevel 1 (
  echo [win-smoke] run.bat provisioning FAILED.
  endlocal
  exit /b 1
)

set "VPY=%~dp0..\.venv\Scripts\python.exe"
"%VPY%" "%~dp0smoke_run.py" %*
if errorlevel 1 (
  echo [win-smoke] clip acceptance FAILED.
  endlocal
  exit /b 1
)

echo [win-smoke] PASS — ship target works.
endlocal
