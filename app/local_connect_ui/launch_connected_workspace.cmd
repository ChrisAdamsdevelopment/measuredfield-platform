@echo off
setlocal
cd /d "%~dp0..\.."
python -m app.local_connect_ui
if errorlevel 1 (
  echo.
  echo The Connected Workspace window could not start. Check that Python 3 and the project are installed.
  pause
)
endlocal
