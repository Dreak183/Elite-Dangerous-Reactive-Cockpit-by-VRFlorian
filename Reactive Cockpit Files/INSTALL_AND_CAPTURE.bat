@echo off
title VRFlorian ^& Guardian Arthur's - Reactive Cockpit - Advanced Capture
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 reactive_cockpit.py setup
) else (
    python reactive_cockpit.py setup
)

echo.
pause
