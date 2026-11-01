@echo off
REM ============================================================
REM  Start the study desk: the Footprint Builder with study
REM  creation, folder browsing, bus lists and "Run the study".
REM  Double-click this file. It opens http://127.0.0.1:8770 in
REM  your browser. Keep this window open while you work; close
REM  it (or press Ctrl+C) to stop the desk.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment is missing. Run setup_env.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts\study_desk.py %*
if errorlevel 1 (
    echo.
    echo The study desk stopped with an error. If it says the port is in use,
    echo it is probably already running: open http://127.0.0.1:8770
    pause
)
