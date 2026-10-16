@echo off
setlocal
cd /d "%~dp0"
start "VRFlorian Reactive Cockpit" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0REACTIVE_COCKPIT_LAUNCHER.ps1"
exit /b 0
