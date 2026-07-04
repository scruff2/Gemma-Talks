@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

if "%GEMMA_TALKS_URL%"=="" set "GEMMA_TALKS_URL=http://127.0.0.1:7860"
set "GEMMA_TALKS_NATIVE_PID_FILE=%CD%\native-client.pid"
set "GEMMA_TALKS_NATIVE_LOG_FILE=%CD%\native-client.out.log"
set "PYTHONPATH=%CD%\.venv\Lib\site-packages;%CD%"
set "PATH=%CD%\.venv\Scripts;%PATH%"

if not exist "%PYTHON_EXE%" (
  echo Python was not found. Run the setup steps in README.md first.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok = $false;" ^
  "for ($i = 0; $i -lt 10; $i++) {" ^
  "  try {" ^
  "    $r = Invoke-RestMethod -Uri '%GEMMA_TALKS_URL%/api/health' -TimeoutSec 2;" ^
  "    if ($r.ok) { $ok = $true; break }" ^
  "  } catch {" ^
  "    Start-Sleep -Seconds 1" ^
  "  }" ^
  "}" ^
  "if (-not $ok) { Write-Host 'Backend is not ready. Start it with start-server.cmd first.'; exit 1 }"
if errorlevel 1 (
  pause
  exit /b 1
)

echo Gemma Talks native listener is starting.
echo This window is the non-browser voice listener. You can minimize it, but leave it open.
echo.

"%PYTHON_EXE%" -u -m app.native_client
set EXIT_CODE=%ERRORLEVEL%

if "%EXIT_CODE%"=="2" (
  echo Another native listener is already running.
  timeout /t 2 >nul
  exit /b 2
)

echo.
echo Gemma Talks native listener exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
