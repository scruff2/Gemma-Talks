@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "PID_FILE=%CD%\native-client.pid"
set "OUT_LOG=%CD%\native-client.out.log"
set "ERR_LOG=%CD%\native-client.err.log"

if "%GEMMA_TALKS_URL%"=="" set "GEMMA_TALKS_URL=http://127.0.0.1:7860"

if not exist "%PYTHON_EXE%" (
  echo Python virtual environment not found. Run the setup steps in README.md first.
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
if errorlevel 1 exit /b 1
echo Backend is ready. Starting native listener...

if exist "%PID_FILE%" (
  set /p EXISTING_PID=<"%PID_FILE%"
  if not "%EXISTING_PID%"=="" (
    tasklist /FI "PID eq %EXISTING_PID%" /NH | findstr /I "python.exe" >nul
    if not errorlevel 1 (
      echo Native client already appears to be running with PID %EXISTING_PID%.
      exit /b 0
    )
  )
  del /f /q "%PID_FILE%" >nul 2>nul
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$workdir = (Get-Location).Path;" ^
  "$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe';" ^
  "if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $workdir '.venv\Scripts\python.exe' }" ^
  "$out = Join-Path $workdir 'native-client.out.log';" ^
  "$err = Join-Path $workdir 'native-client.err.log';" ^
  "$pidFile = Join-Path $workdir 'native-client.pid';" ^
  "$env:GEMMA_TALKS_URL = '%GEMMA_TALKS_URL%';" ^
  "$env:PYTHONPATH = ((Join-Path $workdir '.venv\Lib\site-packages') + ';' + $workdir);" ^
  "$pathValue = [Environment]::GetEnvironmentVariable('Path','Process'); if (-not $pathValue) { $pathValue = [Environment]::GetEnvironmentVariable('PATH','Process') };" ^
  "[Environment]::SetEnvironmentVariable('PATH',$null,'Process'); [Environment]::SetEnvironmentVariable('Path',((Join-Path $workdir '.venv\Scripts') + ';' + $pathValue),'Process');" ^
  "$p = Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList @('-u','-m','app.native_client') -WorkingDirectory $workdir -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "Start-Sleep -Seconds 2;" ^
  "if ($p.HasExited) { Write-Host ('Native client exited during startup. ExitCode=' + $p.ExitCode); exit 1 }" ^
  "Set-Content -LiteralPath $pidFile -Value $p.Id;" ^
  "Write-Host ('Native client started. PID=' + $p.Id)"

exit /b %errorlevel%
