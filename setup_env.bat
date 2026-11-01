@echo off
REM ============================================================
REM  One-shot environment setup for tara-automation (Windows)
REM  Creates .venv, installs pinned requirements, installs the
REM  tara_automation package in editable mode, runs smoke tests.
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/5] Creating virtual environment (.venv)...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto :fail

echo [2/5] Upgrading pip...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [3/5] Installing pinned requirements...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [4/5] Installing tara_automation (editable)...
call .venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 goto :fail

echo [5/5] Running smoke tests...
call .venv\Scripts\python.exe -m pytest tests -q
if errorlevel 1 goto :fail

echo.
echo Setup complete. Activate with:  .venv\Scripts\activate
echo Next: copy settings\machine.example.json
echo to settings\machine.json and edit paths.
exit /b 0

:fail
echo.
echo Setup FAILED. See messages above.
exit /b 1
