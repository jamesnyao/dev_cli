@echo off
setlocal

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py "%~dp0dev.py" %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python3 "%~dp0dev.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python "%~dp0dev.py" %*
  exit /b %ERRORLEVEL%
)

rem No Python found on PATH; delegate to dev.ps1, which bootstraps it via
rem winget when invoked as `dev python update`.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
exit /b %ERRORLEVEL%
