@echo off
title VRFlorian ^& Guardian Arthur's - Reactive Cockpit - Installer v1.1 TEST
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 reactive_cockpit.py community_install
) else (
    python reactive_cockpit.py community_install
)

echo.
pause
