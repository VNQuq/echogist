@echo off
REM EchoGist T11 measurement launcher (TD-4).
REM
REM Runs measure_model.py with the VENV python — faster-whisper lives in .venv,
REM not the global python, so `python scripts\measure_model.py` would fail with
REM "faster-whisper is not installed". Provision once via run.bat first (the model
REM + GPU wheels must be present). All args are forwarded, e.g.:
REM   scripts\measure.bat --host "RTX 4060"
REM   scripts\measure.bat --host "RTX 4060" --bar 1.5
setlocal
cd /d "%~dp0\.."
set "VPY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [measure] .venv not found - run run.bat once to provision, then retry.
  endlocal
  exit /b 1
)
"%VPY%" "%~dp0measure_model.py" %*
endlocal
