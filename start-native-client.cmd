@echo off
setlocal

cd /d "%~dp0"

set "PID_FILE=%CD%\native-client.pid"

if "%GEMMA_TALKS_URL%"=="" set "GEMMA_TALKS_URL=http://127.0.0.1:7860"

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
echo Backend is ready. Opening native listener window...

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
  "$runner = Join-Path (Get-Location).Path 'run-native-client.cmd';" ^
  "Start-Process -WindowStyle Normal -FilePath $env:ComSpec -ArgumentList @('/k', ('\"' + $runner + '\"'));" ^
  "Write-Host 'Native listener window opened.'"

exit /b 0
