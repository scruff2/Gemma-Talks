@echo off
setlocal

cd /d "%~dp0"

set "LLAMA_EXE=%CD%\tools\llama-cpp\llama-server.exe"
set "PID_FILE=%CD%\llama-server.pid"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pidFile = '%PID_FILE%';" ^
  "$ids = @();" ^
  "if (Test-Path -LiteralPath $pidFile) { $raw = (Get-Content -LiteralPath $pidFile -Raw).Trim(); if ($raw -match '^\d+$') { $ids += [int]$raw } }" ^
  "if (Test-Path -LiteralPath '%LLAMA_EXE%') { $exe = (Resolve-Path -LiteralPath '%LLAMA_EXE%').Path; $ids += @(Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | ForEach-Object { $_.Id }) }" ^
  "$ids = @($ids | Sort-Object -Unique);" ^
  "if (-not $ids) { if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }; Write-Host 'No llama-server process found. Nothing to stop.'; exit 0 }" ^
  "foreach ($id in $ids) { try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host ('Stopped llama-server PID ' + $id + '.') } catch { Write-Host ('Could not stop llama-server PID ' + $id + '. The process may already be gone.') } }" ^
  "if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }"
exit /b %errorlevel%
