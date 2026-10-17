@echo off
title Zentinel AIOps - launcher
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] Project virtual env not found: .venv\Scripts\python.exe
    echo.
    echo First-time setup, run these once in this folder:
    echo     py -3.11 -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements.lock
    echo     .venv\Scripts\python -m pip install -e . --no-deps
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   Zentinel AIOps - ONE port: API + dashboard together
echo   Dashboard :  http://127.0.0.1:8000/dashboard
echo   API docs  :  http://127.0.0.1:8000/docs
echo   Health    :  http://127.0.0.1:8000/health
echo  ============================================================
echo.
echo  A server window will open. Browser opens automatically ~6s later.
echo  To stop: close the "Zentinel-Server" window or press Ctrl+C there.
echo.

start "Zentinel-Server" cmd /k ".venv\Scripts\python.exe -m zentinel.api.unified"
start "" cmd /c "timeout /t 6 >nul & start "" ""http://127.0.0.1:8000/dashboard"" & exit"

echo Server window launched.
echo (If the dashboard shows OFFLINE banner for the first seconds, wait a few
echo  seconds and press Connect in the sidebar - the API is starting up.)
echo.
pause
