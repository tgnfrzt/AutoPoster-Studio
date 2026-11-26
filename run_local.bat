@echo off
title AutoPoster Service and Studio
cd /d "%~dp0"

echo =======================================================
echo          AutoPoster Service and Visual Studio
echo =======================================================
echo.

:: Check python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

:: Virtual environment check
if not exist ".venv" (
    echo [INFO] Creating Python virtual environment in .venv...
    python -m venv .venv
)

echo [INFO] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [INFO] Installing / verifying dependencies...
pip install -r backend\requirements.txt --quiet

echo.
echo =======================================================
echo  AutoPoster Web UI Studio: http://localhost:17420
echo  API Documentation:        http://localhost:17420/docs
echo =======================================================
echo [INFO] Starting FastAPI server and background worker...
echo.

set PYTHONPATH=%cd%\backend
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -m uvicorn main:app --host 0.0.0.0 --port 17420 --app-dir backend --reload
pause
