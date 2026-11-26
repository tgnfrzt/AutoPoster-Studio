@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set PYTHON_EXE=.venv\Scripts\python.exe
) else (
    set PYTHON_EXE=python.exe
)

set PYTHONPATH=%cd%\backend;%cd%
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%PYTHON_EXE%" -m uvicorn main:app --host 0.0.0.0 --port 17420 --app-dir backend > "storage\data\service.log" 2>&1
