@echo off
title Stop AutoPoster Service
echo Stopping AutoPoster background processes on port 17420...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":17420" ^| findstr "LISTENING"') do (
    echo Terminating PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo AutoPoster background service stopped.
pause
