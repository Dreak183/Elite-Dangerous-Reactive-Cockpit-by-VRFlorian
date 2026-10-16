@echo off
setlocal EnableExtensions EnableDelayedExpansion
title VRFlorian Reactive Cockpit
cd /d "%~dp0Story Platform"
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py -3"
) else (
  set "PYTHON_CMD=python"
)
for %%G in ("stories\mission_01\graph.json" "stories\mission_02\graph.json") do (
  %PYTHON_CMD% platform.py media-check "%%~G"
  set "MEDIA_CHECK_EXIT=!ERRORLEVEL!"
  if not "!MEDIA_CHECK_EXIT!"=="0" (
    echo.
    echo Story media preflight failed. Copy the complete authored story-assets map into:
    echo %CD%\story_assets
    pause
    exit /b !MEDIA_CHECK_EXIT!
  )
)
%PYTHON_CMD% platform.py run
set "RC_EXIT=%ERRORLEVEL%"
if not "%RC_EXIT%"=="0" (
  echo.
  echo VRFlorian Reactive Cockpit stopped with code %RC_EXIT%.
  echo See the internal logs and runtime folders for diagnostics.
  pause
)
exit /b %RC_EXIT%
