@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "PID_FILE=%CD%\server.pid"
set "OUT_LOG=%CD%\server.out.log"
set "ERR_LOG=%CD%\server.err.log"
set "LLAMA_CPP_DIR=%CD%\tools\llama-cpp"

if "%LLM_PROVIDER%"=="" set "LLM_PROVIDER=llama"
if "%LLAMA_BASE_URL%"=="" set "LLAMA_BASE_URL=http://127.0.0.1:8080"
if "%LLAMA_MODEL%"=="" set "LLAMA_MODEL=google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0"
if "%WHISPER_MODEL%"=="" set "WHISPER_MODEL=medium.en"
if "%WHISPER_FAST_MODEL%"=="" set "WHISPER_FAST_MODEL=%WHISPER_MODEL%"
if "%WHISPER_WAKE_MODEL%"=="" set "WHISPER_WAKE_MODEL=%WHISPER_MODEL%"
if "%WHISPER_DEVICE%"=="" set "WHISPER_DEVICE=cuda"
if "%WHISPER_COMPUTE_TYPE%"=="" set "WHISPER_COMPUTE_TYPE=float16"
if "%WHISPER_BEAM_SIZE%"=="" set "WHISPER_BEAM_SIZE=3"

if not exist "%PYTHON_EXE%" (
  echo Python virtual environment not found. Run the setup steps in README.md first.
  pause
  exit /b 1
)

if /I "%LLM_PROVIDER%"=="llama" (
  call "%CD%\start-llama-server.cmd"
  if errorlevel 1 exit /b 1
)

if exist "%PID_FILE%" (
  set /p EXISTING_PID=<"%PID_FILE%"
  tasklist /FI "PID eq %EXISTING_PID%" /NH | findstr /I "python.exe" >nul
  if not errorlevel 1 (
    echo Server already appears to be running with PID %EXISTING_PID%.
    exit /b 0
  )
  del /f /q "%PID_FILE%" >nul 2>nul
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$workdir = '%CD%';" ^
  "$python = '%PYTHON_EXE%';" ^
  "$out = '%OUT_LOG%';" ^
  "$err = '%ERR_LOG%';" ^
  "$env:LLM_PROVIDER = '%LLM_PROVIDER%';" ^
  "$env:LLAMA_BASE_URL = '%LLAMA_BASE_URL%';" ^
  "$env:LLAMA_MODEL = '%LLAMA_MODEL%';" ^
  "$env:WHISPER_MODEL = '%WHISPER_MODEL%';" ^
  "$env:WHISPER_FAST_MODEL = '%WHISPER_FAST_MODEL%';" ^
  "$env:WHISPER_WAKE_MODEL = '%WHISPER_WAKE_MODEL%';" ^
  "$env:WHISPER_DEVICE = '%WHISPER_DEVICE%';" ^
  "$env:WHISPER_COMPUTE_TYPE = '%WHISPER_COMPUTE_TYPE%';" ^
  "$env:WHISPER_BEAM_SIZE = '%WHISPER_BEAM_SIZE%';" ^
  "$env:PATH = '%LLAMA_CPP_DIR%;' + $env:PATH;" ^
  "$p = Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList @('-u','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','7860') -WorkingDirectory $workdir -RedirectStandardOutput $out -RedirectStandardError $err -PassThru;" ^
  "Set-Content -LiteralPath '%PID_FILE%' -Value $p.Id;" ^
  "Write-Host ('Server started. PID=' + $p.Id)"

exit /b %errorlevel%
