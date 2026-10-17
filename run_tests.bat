@echo off
title Zentinel AIOps - test runner
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv missing - run start_zentinel.bat first for setup help.
    pause
    exit /b 1
)

echo Running the full Zentinel test suite (about 10-25 minutes)...
echo ================================================================
.venv\Scripts\python.exe -m pytest -q --no-header
echo.
echo Done. Exit code %ERRORLEVEL% (0 = all tests passed).
pause
