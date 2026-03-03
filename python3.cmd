@echo off
setlocal enabledelayedexpansion

REM Prevent infinite recursion
if defined PYTHON3_WRAPPER_ACTIVE (
  py.exe %*
  exit /b %ERRORLEVEL%
)
set PYTHON3_WRAPPER_ACTIVE=1

set "FOUND="

REM Prefer .bat over .exe on Windows shims
for %%I in (python3.bat python3.exe) do (
  set "CAND=%%~$PATH:I"

  REM Skip toolchain_tools python (any drive)
  if defined CAND (
    echo !CAND! | findstr /I /c:"toolchain_tools" >nul && set "CAND="
  )

  REM Skip Windows Store stubs
  if defined CAND (
    echo !CAND! | findstr /I /c:"Microsoft\WindowsApps" >nul && set "CAND="
  )

  REM Skip self
  if defined CAND (
    if /I not "!CAND!"=="%~f0" (
      set "FOUND=!CAND!"
      goto :found_done
    )
  )
)
:found_done

if defined FOUND (
  "!FOUND!" %*
  exit /b %ERRORLEVEL%
)

REM Fallback
py.exe %*