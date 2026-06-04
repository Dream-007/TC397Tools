@echo off
setlocal

rem Native Windows entrypoint. This does not require WSL.
rem It calls the PowerShell helper next to this .bat file.

set "SCRIPT=%~dp0setup_ccfc_stepcode_windows.ps1"

if not exist "%SCRIPT%" (
  echo ERROR: PowerShell helper not found:
  echo   %SCRIPT%
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
exit /b %ERRORLEVEL%
