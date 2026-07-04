@echo off
setlocal

cd /d "%~dp0"

set "PID_FILE=%CD%\native-client.pid"
set "LOCK_FILE=%PID_FILE%.lock"

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
echo Backend is ready.

if exist "%LOCK_FILE%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$lockFile = '%LOCK_FILE%';" ^
    "$stream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite);" ^
    "try { $stream.Lock(0, 1); $stream.Unlock(0, 1); $stream.Close(); exit 0 }" ^
    "catch { $stream.Close(); $pidText = (Get-Content -LiteralPath $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1); if ($pidText) { Write-Host ('Native client already appears to be running with PID ' + $pidText + '.') } else { Write-Host 'Native client already appears to be running.' }; exit 10 }"
  if errorlevel 10 exit /b 0
  if errorlevel 1 exit /b 1
)

if exist "%PID_FILE%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pidText = (Get-Content -LiteralPath '%PID_FILE%' -ErrorAction SilentlyContinue | Select-Object -First 1).Trim();" ^
    "if ($pidText -match '^\d+$') {" ^
    "  $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue;" ^
    "  if ($process) { Write-Host ('Native client already appears to be running with PID ' + $pidText + '.'); exit 10 }" ^
    "}"
  if errorlevel 10 exit /b 0
  if errorlevel 1 exit /b 1
  del /f /q "%PID_FILE%" >nul 2>nul
)

echo Opening native listener window...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$runner = Join-Path (Get-Location).Path 'run-native-client.cmd';" ^
  "Start-Process -WindowStyle Normal -FilePath $env:ComSpec -ArgumentList @('/c', ('\"' + $runner + '\"'));" ^
  "Write-Host 'Native listener window opened.'"

exit /b 0
