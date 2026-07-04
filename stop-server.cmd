@echo off
setlocal

cd /d "%~dp0"

set "PID_FILE=%CD%\server.pid"

if not exist "%PID_FILE%" (
  echo No server.pid file found. Nothing to stop.
  call "%CD%\stop-llama-server.cmd" >nul 2>nul
  exit /b 0
)

set /p SERVER_PID=<"%PID_FILE%"
if "%SERVER_PID%"=="" (
  echo server.pid is empty. Removing stale file.
  del /f /q "%PID_FILE%" >nul 2>nul
  call "%CD%\stop-llama-server.cmd" >nul 2>nul
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-Process -Id %SERVER_PID% -Force" >nul 2>nul
if errorlevel 1 (
  echo Could not stop PID %SERVER_PID%. The process may already be gone.
) else (
  echo Stopped server PID %SERVER_PID%.
)

del /f /q "%PID_FILE%" >nul 2>nul

call "%CD%\stop-llama-server.cmd" >nul 2>nul
exit /b 0
