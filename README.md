# Local Gemma Voice Chat

Local voice chat for Gemma 4 E4B through `llama.cpp` / `llama-server`.

## Prerequisites

- Python 3.11 recommended
- `tools\llama-cpp\llama-server.exe`
- Google Gemma 4 E4B GGUF access through Hugging Face

The checked-in start script expects the CUDA Windows build of `llama.cpp` under:

```text
tools\llama-cpp\llama-server.exe
```

The first model start may download the default GGUF into the Hugging Face cache:

```text
google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0
```

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run

```powershell
.\start-server.cmd
```

Open:

```text
http://127.0.0.1:7860
```

On Windows, use `start-server.cmd` and `stop-server.cmd` in the project root. The start script launches `llama-server` first, then the FastAPI app. The stop script shuts down both processes using `llama-server.pid` and `server.pid`.

## Controls

- `REC`: start recording. Click `STOP` to end manually.
- Auto-stop on silence: when enabled, recording stops after you pause.
- `Stop`: cancels current speech and aborts an in-progress Gemma reply.
- `Settings`: tune silence detection, response temperature, TTS voice, and system prompt.
- `Transcription`: uses the configured Faster-Whisper model. The default is `medium.en`.
- `Mic On` / `Mic Paused`: temporarily disables microphone activation.
- Gemma timing is logged after each response: first token and complete response time.
- Stop-intent classification only runs for stop-like phrases; ordinary follow-ups go straight to Gemma.
- Activation mode can be `Push to talk` or `Wake word`.
- Wake-word mode uses the fast Whisper model on short overlapping local audio windows, then matches configured wake phrases such as `alexa`.
- You can say the wake phrase alone, or include a command in the same utterance, such as `Alexa, set a timer for three minutes`.
- In conversation mode, follow-up turns do not require the wake word.
- Conversation mode exits when you say `stop listening`, pause the mic, switch modes, or hit the conversation timeout.
- In conversation mode, Gemma also classifies broad stop-like phrases before normal chat and can return the app to wake-word standby.

Local app-control commands are handled without sending them to Gemma:

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

Timer examples:

- `set a timer for 10 minutes`
- `set a timer for 1 hour 30 minutes`
- `remind me in 5 minutes`
- `set a tea timer for 4 minutes`
- `list timers`
- `cancel timer`
- `clear timers`

Timer setup phrases are interpreted by Gemma as strict JSON, then the app validates the returned timer parameters and schedules the timer locally. The app does not parse timer wording directly. If Gemma decides a timer request is missing a duration, the app asks for the missing time and sends the next reply back to Gemma with timer context. Timers are stored in browser localStorage and the app speaks when a timer expires. Once a timer is created, the conversation ends and wake-word listening resumes.

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
$env:WAKE_WORDS = "alexa,computer"
$env:WAKE_SENSITIVITY = "0.45"
```

If GPU memory is tight, try:

```powershell
$env:WHISPER_MODEL = "base.en"
$env:WHISPER_DEVICE = "cpu"
$env:WHISPER_COMPUTE_TYPE = "int8"
```

If CUDA dependencies are not installed or GPU memory is tight, keep Whisper on CPU and leave the GPU for `llama-server`.

To fall back to Ollama, set these before running `start-server.cmd`:

```powershell
$env:LLM_PROVIDER = "ollama"
$env:OLLAMA_URL = "http://localhost:11434"
$env:OLLAMA_MODEL = "gemma4:e4b"
```
