# Gemma Talks

Gemma Talks is a local browser-based voice assistant for Google Gemma 4 E4B. It runs the language model through `llama.cpp` / `llama-server`, uses Faster-Whisper for local speech recognition, speaks responses with the browser text-to-speech engine, and adds local tools for timers, weather, wake-word conversation, and proactive conversation starts.

The app is designed for Windows and local use. The default configuration keeps both Gemma and Faster-Whisper on the GPU when the machine has enough VRAM.

## Current Capabilities

- Talk to Gemma from the browser by push-to-talk or wake word.
- Wake word defaults to `alexa`.
- After wake activation, follow-up conversation turns do not require repeating the wake word.
- Conversation mode exits by voice command, mic pause, mode switch, timeout, or timer completion.
- Set, list, and cancel multiple timers.
- Interpret timer requests with Gemma instead of deterministic text parsing.
- Fetch local weather from browser geolocation.
- Fetch weather for named cities and places.
- Give Gemma the current date, time, and weather context.
- Rephrase weather tool results into short conversational answers.
- Show raw Gemma responses in a dedicated UI card for debugging.
- Show speech detection status and proactive conversation countdown.
- Capture ambient speech after the first non-wake speech trigger, show the rolling context text, and use it to start a proactive conversation after a short delay.
- Log transcription, weather, timer, and Gemma timing details for troubleshooting.

## Architecture

- Frontend: static HTML, CSS, and JavaScript in `app/static`.
- Backend: FastAPI in `app/main.py`.
- Language model: `llama-server.exe` from `llama.cpp`.
- Speech recognition: Faster-Whisper.
- Text to speech: browser speech synthesis.
- Local timers: browser `localStorage` plus JavaScript timers.
- Local weather: National Weather Service endpoint using browser latitude/longitude.
- City weather lookup: Open-Meteo geocoding and forecast API.

## Prerequisites

- Windows
- Python 3.11 recommended
- A CUDA-capable GPU for the default GPU configuration
- `tools\llama-cpp\llama-server.exe`
- Access to the Gemma 4 E4B GGUF model on Hugging Face

The start script expects the CUDA Windows build of `llama.cpp` here:

```text
tools\llama-cpp\llama-server.exe
```

The default model is:

```text
google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0
```

The model may be downloaded into the Hugging Face cache the first time `llama-server` starts.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Download or build `llama-server.exe` separately and place it under:

```text
tools\llama-cpp\
```

The `tools` directory is intentionally ignored by Git because it contains local binaries.

## Run

Start the server:

```powershell
.\start-server.cmd
```

Open:

```text
http://127.0.0.1:7860
```

Stop the server:

```powershell
.\stop-server.cmd
```

`start-server.cmd` launches `llama-server` first, then the FastAPI app. `stop-server.cmd` shuts down both processes using `llama-server.pid` and `server.pid`.

## Browser Permissions

The app needs microphone permission for speech recognition.

For local weather, the browser also asks for location permission. If location permission is denied, Gemma can still answer weather requests for named cities, but local “weather here” context will not be available.

## Voice Workflow

Activation modes:

- Push to talk: click `REC`, speak, and pause or click `STOP`.
- Wake word: say `Alexa` or a configured wake phrase.

Wake-word examples:

- `Alexa, can you hear me?`
- `Alexa, set a tea timer for four minutes`
- `Alexa, what is the weather in Phoenix, Arizona?`

In wake-word mode, the wake phrase starts a conversation. Follow-up turns continue without repeating the wake word until the conversation ends.

Conversation mode can end when:

- You say `stop listening`, `we are done`, `that is all`, or similar stop phrases.
- The conversation timeout is reached.
- You pause the microphone.
- You switch activation modes.
- A timer is created and the app returns to wake-word listening.

## Proactive Conversation

When non-wake speech is detected in wake-word mode, the app starts a proactive conversation countdown. The delay is currently randomized between 30 and 60 seconds.

While the countdown runs:

- Faster-Whisper keeps a rolling ambient audio buffer.
- The app re-transcribes that buffer periodically.
- The webpage shows the `Conversation context` text that will be sent to Gemma.
- Gemma is not called until the countdown reaches zero.

When the countdown fires, Gemma receives the captured context and starts a short conversation related to it when possible.

If the wake word is detected before the countdown fires, the proactive countdown is canceled and normal wake-word interaction takes over.

## Timers

Timer setup and cancellation are interpreted by Gemma through a strict JSON response schema, then validated by the app.

Examples:

- `set a timer for 10 minutes`
- `set a tea timer for 4 minutes`
- `set a cake timer for 30 seconds`
- `remind me in 5 minutes`
- `cancel the tea timer`
- `cancel all timers`
- `list timers`

Timer notes:

- Multiple active timers are supported.
- Timers are stored in browser `localStorage`.
- The app speaks when a timer expires.
- Once a timer is created, conversation mode ends and wake-word listening resumes.

## Weather

Weather requests use live weather tools instead of relying on model memory.

Supported examples:

- `What is the weather right now?`
- `What is tomorrow's high here?`
- `What is the forecast in Chicago tomorrow?`
- `What is the humidity in Renton, Washington?`
- `How about Sunday?`

Weather flow:

1. Gemma classifies the request as `get_weather` and chooses a location.
2. The app fetches current weather and forecast data.
3. Gemma rephrases the factual tool output into a concise conversational answer.
4. The app speaks and displays the answer.

For local weather, the app uses browser geolocation and the National Weather Service. For named city lookup, it uses Open-Meteo geocoding and forecast data.

## Interface

Main UI elements:

- `REC` / `STOP`: manual recording control.
- `Speak`: toggles spoken responses.
- `Settings`: opens voice and model settings.
- `Mic On` / `Mic Paused`: controls microphone activation.
- `Restart LLM`: restarts the local model server.
- `Clear`: clears the visible chat.
- Timers panel: shows active timers and lets you clear them.
- Gemma Raw Response card: shows up to 500 characters of the raw model response.
- Speech status: shows whether speech is detected.
- Conversation countdown and context: show proactive conversation timing and the text Gemma will analyze.

## Local App Commands

These are handled locally where possible:

- `stop`
- `clear chat`
- `repeat that`
- `pause microphone`
- `resume microphone`
- `stop sending to Gemma`
- `privacy mode`
- `open settings`
- `close settings`
- `wake word mode`
- `push to talk mode`
- `stop listening`

## Configuration

Optional environment variables:

```powershell
$env:LLM_PROVIDER = "llama"
$env:LLAMA_BASE_URL = "http://127.0.0.1:8080"
$env:LLAMA_MODEL = "google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0"
$env:WHISPER_MODEL = "medium.en"
$env:WHISPER_FAST_MODEL = "medium.en"
$env:WHISPER_WAKE_MODEL = "medium.en"
$env:WHISPER_DEVICE = "cuda"
$env:WHISPER_COMPUTE_TYPE = "float16"
$env:WHISPER_BEAM_SIZE = "3"
$env:WAKE_WORDS = "alexa,computer"
$env:WAKE_SENSITIVITY = "0.45"
$env:WAKE_AMBIENT_CONTEXT_SECONDS = "2.5"
$env:WAKE_AMBIENT_PRETRIGGER_SECONDS = "6.0"
$env:WAKE_AMBIENT_MAX_SECONDS = "70.0"
```

If GPU memory is tight, try:

```powershell
$env:WHISPER_MODEL = "base.en"
$env:WHISPER_FAST_MODEL = "base.en"
$env:WHISPER_WAKE_MODEL = "base.en"
$env:WHISPER_DEVICE = "cpu"
$env:WHISPER_COMPUTE_TYPE = "int8"
```

To fall back to Ollama:

```powershell
$env:LLM_PROVIDER = "ollama"
$env:OLLAMA_URL = "http://localhost:11434"
$env:OLLAMA_MODEL = "gemma4:e4b"
```

The current project default is `llama-server`, because it was faster and more reliable than the Ollama setup used earlier in development.

## Logs And Runtime Files

Runtime logs and PID files are created locally and ignored by Git:

- `app/voice-app.log`
- `server.out.log`
- `server.err.log`
- `server.pid`
- `llama-server.out.log`
- `llama-server.err.log`
- `llama-server.pid`

These logs are useful for diagnosing:

- speech transcription time
- wake listener behavior
- proactive context capture
- Gemma intent classification
- weather tool calls
- first-token and full-response timing

## Repository Notes

This repository does not include:

- Python virtual environment files
- `llama.cpp` binaries
- downloaded model files
- local logs
- local PID files
- local session resume files

Those are machine-local runtime artifacts and should be recreated during setup.
