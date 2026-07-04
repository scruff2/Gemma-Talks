@echo off
setlocal

cd /d "%~dp0"

set "PID_FILE=%CD%\native-client.pid"

if not exist "%PID_FILE%" (
  echo Native client PID file not found. It may not be running.
  exit /b 0
)

set /p PID=<"%PID_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pidValue = '%PID%';" ^
  "if (-not $pidValue) { exit 0 }" ^
  "try { Stop-Process -Id ([int]$pidValue) -Force -ErrorAction Stop; Write-Host ('Stopped native client PID ' + $pidValue) }" ^
  "catch [Microsoft.PowerShell.Commands.ProcessCommandException] { Write-Host ('Native client PID ' + $pidValue + ' is not running') }" ^
  "catch { Write-Host ('Could not stop native client PID ' + $pidValue + ': ' + $_.Exception.Message); exit 1 }"

del /f /q "%PID_FILE%" >nul 2>nul

exit /b %errorlevel%
