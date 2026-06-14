@echo off
REM EchoGist cold-Windows acceptance (plan §12.1 / T13).
REM
REM Run this on the Windows ship target before a release-worthy commit. It exercises
REM the one thing only Windows validates: the run.bat provisioning sequence (venv,
REM --require-hashes install, model fetch, GPU preflight, execution-policy/PATH).
setlocal
cd /d "%~dp0\.."

call run.bat
if errorlevel 1 (
  echo [win-smoke] run.bat FAILED.
  endlocal
  exit /b 1
)

REM TODO(T3/T4): once transcribe + extract land, drive one canned ~2-min clip
REM through to an mp3 + summary and assert both artifacts exist.
echo [win-smoke] run.bat provisioning OK. Full clip acceptance pending T3/T4.
endlocal
