@echo off
setlocal

cd /d "%~dp0"

set "LLAMA_EXE=%CD%\tools\llama-cpp\llama-server.exe"
set "PID_FILE=%CD%\llama-server.pid"
set "OUT_LOG=%CD%\llama-server.out.log"
set "ERR_LOG=%CD%\llama-server.err.log"

if not exist "%LLAMA_EXE%" (
  echo llama-server.exe not found. Expected: %LLAMA_EXE%
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pathValue = [Environment]::GetEnvironmentVariable('Path','Process'); if (-not $pathValue) { $pathValue = [Environment]::GetEnvironmentVariable('PATH','Process') };" ^
  "[Environment]::SetEnvironmentVariable('PATH',$null,'Process'); [Environment]::SetEnvironmentVariable('Path',$pathValue,'Process');" ^
  "$exe = (Resolve-Path -LiteralPath '%LLAMA_EXE%').Path;" ^
  "$pidFile = '%PID_FILE%';" ^
  "$existing = Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Select-Object -First 1;" ^
  "if ($existing) { Set-Content -LiteralPath $pidFile -Value $existing.Id; Write-Host ('llama-server already running. PID=' + $existing.Id); exit 10 }" ^
  "if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }"
if errorlevel 10 exit /b 0
if errorlevel 1 exit /b 1

if "%LLAMA_MODEL%"=="" set "LLAMA_MODEL=google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pathValue = [Environment]::GetEnvironmentVariable('Path','Process'); if (-not $pathValue) { $pathValue = [Environment]::GetEnvironmentVariable('PATH','Process') };" ^
  "[Environment]::SetEnvironmentVariable('PATH',$null,'Process'); [Environment]::SetEnvironmentVariable('Path',$pathValue,'Process');" ^
  "$workdir = '%CD%';" ^
  "$exe = '%LLAMA_EXE%';" ^
  "$out = '%OUT_LOG%';" ^
  "$err = '%ERR_LOG%';" ^
  "$model = $env:LLAMA_MODEL;" ^
  "$p = Start-Process -WindowStyle Hidden -FilePath $exe -ArgumentList @('--hf-repo',$model,'--host','127.0.0.1','--port','8080','--ctx-size','4096','--n-gpu-layers','999','--jinja','--reasoning','off') -WorkingDirectory $workdir -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "Set-Content -LiteralPath '%PID_FILE%' -Value $p.Id;" ^
  "Write-Host ('llama-server started. PID=' + $p.Id)"

exit /b %errorlevel%
