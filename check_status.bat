@echo off
title AutoPoster Status Checker
echo ========================================
echo Checking AutoPoster Service Status...
echo ========================================

netstat -aon | findstr ":17420" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [RUNNING] AutoPoster service is active on port 17420.
    echo Open web dashboard at: http://localhost:17420
) else (
    echo [STOPPED] AutoPoster service is not running.
    echo Double-click 'start_background.vbs' or 'run_local.bat' to start.
)
echo.
pause
