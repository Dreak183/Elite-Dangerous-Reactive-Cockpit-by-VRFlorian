@echo off
setlocal
set "RC_FILES=%~dp0Reactive Cockpit Files"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RC_FILES%\REACTIVE_COCKPIT_LAUNCHER.ps1"
exit /b %ERRORLEVEL%
