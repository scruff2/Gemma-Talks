from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import sounddevice as sd
import websockets


BASE_URL = os.getenv("GEMMA_TALKS_URL", "http://127.0.0.1:7860").rstrip("/")
WS_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
SAMPLE_RATE = int(os.getenv("GEMMA_TALKS_SAMPLE_RATE", "16000"))
CHANNELS = 1
BLOCK_SIZE = int(os.getenv("GEMMA_TALKS_BLOCK_SIZE", "4096"))
WAKE_THRESHOLD = float(os.getenv("WAKE_SENSITIVITY", "0.45"))
SILENCE_THRESHOLD = float(os.getenv("GEMMA_TALKS_SILENCE_THRESHOLD", "0.04"))
SILENCE_SECONDS = float(os.getenv("GEMMA_TALKS_SILENCE_SECONDS", "1.2"))
MIN_RECORD_SECONDS = float(os.getenv("GEMMA_TALKS_MIN_RECORD_SECONDS", "0.7"))
MAX_RECORD_SECONDS = float(os.getenv("GEMMA_TALKS_MAX_RECORD_SECONDS", "18"))
NO_SPEECH_TIMEOUT_SECONDS = float(os.getenv("GEMMA_TALKS_NO_SPEECH_TIMEOUT_SECONDS", "2.2"))
CONVERSATION_TIMEOUT_SECONDS = float(os.getenv("GEMMA_TALKS_CONVERSATION_TIMEOUT_SECONDS", "45"))
SYSTEM_PROMPT = os.getenv(
    "GEMMA_TALKS_SYSTEM_PROMPT",
    "You are a concise local voice assistant. Reply conversationally. "
    "Keep spoken answers short unless the user asks for detail. "
    "Do not use emojis, emoticons, or decorative symbols.",
)
TEMPERATURE = float(os.getenv("GEMMA_TALKS_TEMPERATURE", "0.7"))
LOCAL_WEATHER_LOCATION = os.getenv("GEMMA_TALKS_WEATHER_LOCATION", "").strip()
PID_FILE = os.getenv("GEMMA_TALKS_NATIVE_PID_FILE", "").strip()


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def write_pid_file() -> None:
    if not PID_FILE:
        return
    try:
        with open(PID_FILE, "w", encoding="utf-8") as file:
            file.write(str(os.getpid()))
    except OSError as exc:
        log(f"could not write pid file: {exc}")


def remove_pid_file() -> None:
    if not PID_FILE:
        return
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, encoding="utf-8") as file:
                existing = file.read().strip()
            if existing == str(os.getpid()):
                os.remove(PID_FILE)
    except OSError as exc:
        log(f"could not remove pid file: {exc}")


def audio_rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    values = samples.astype(np.float32, copy=False) / 32768.0
    return float(np.sqrt(np.mean(np.square(values))))


def pcm_to_wav_bytes(pcm_chunks: list[bytes]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(pcm_chunks))
    return buffer.getvalue()


def normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split()).strip(" .,!?:;-")


def merge_prefill(prefix: str, transcript: str) -> str:
    clean_prefix = " ".join((prefix or "").split()).strip()
    clean_transcript = " ".join((transcript or "").split()).strip()
    if not clean_prefix:
        return clean_transcript
    if not clean_transcript:
        return clean_prefix
    normalized_prefix = normalize_text(clean_prefix)
    normalized_transcript = normalize_text(clean_transcript)
    if normalized_prefix and normalized_prefix in normalized_transcript:
        return clean_transcript
    return f"{clean_prefix.rstrip(' .,!?:;-')} {clean_transcript}".strip()


def powershell_speak(text: str) -> None:
    spoken = " ".join((text or "").split()).strip()
    if not spoken:
        return
    encoded_text = base64.b64encode(spoken.encode("utf-16le")).decode("ascii")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        f"$bytes = [Convert]::FromBase64String('{encoded_text}'); "
        "$t = [Text.Encoding]::Unicode.GetString($bytes); "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Speak($t);"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


@dataclass
class TimerEntry:
    id: str
    kind: str
    label: str
    fire_at: float
    task: asyncio.Task[None]


class NativeVoiceClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=30)
        self.running = True
        self.messages: list[dict[str, str]] = []
        self.pending_timer_setup: dict[str, Any] | None = None
        self.timers: dict[str, TimerEntry] = {}
        self.weather_context = ""
        self.weather_fetched_at = 0.0

    async def close(self) -> None:
        self.running = False
        for timer in list(self.timers.values()):
            timer.task.cancel()
        await self.http.aclose()

    async def client_log(self, event: str, text: str = "", decision: str = "", detail: str = "") -> None:
        with contextlib.suppress(Exception):
            await self.http.post(
                f"{BASE_URL}/api/client-log",
                json={"event": event, "text": text, "decision": decision, "detail": detail},
            )

    async def refresh_weather_context(self) -> str:
        if not LOCAL_WEATHER_LOCATION:
            return ""
        if self.weather_context and time.time() - self.weather_fetched_at < 15 * 60:
            return self.weather_context
        try:
            response = await self.http.get(
                f"{BASE_URL}/api/weather/lookup",
                params={"location": LOCAL_WEATHER_LOCATION},
            )
            response.raise_for_status()
            data = response.json()
            self.weather_context = str(data.get("summary") or "")
            self.weather_fetched_at = time.time()
        except Exception as exc:
            await self.client_log("native_weather_context", decision="unavailable", detail=str(exc))
        return self.weather_context

    def active_timer_summaries(self) -> list[dict[str, Any]]:
        now = time.time()
        summaries = []
        for index, timer in enumerate(sorted(self.timers.values(), key=lambda item: item.fire_at), start=1):
            remaining_ms = max(0, int((timer.fire_at - now) * 1000))
            label = timer.label.strip()
            display = f"{label} timer" if label and not label.lower().endswith("timer") else label or "Timer"
            summaries.append(
                {
                    "id": timer.id,
                    "index": index,
                    "kind": timer.kind,
                    "label": display,
                    "rawLabel": label,
                    "aliases": [value for value in {label, display} if value],
                    "remainingMs": remaining_ms,
                    "remainingText": self.format_duration_for_speech(remaining_ms),
                }
            )
        return summaries

    @staticmethod
    def format_duration_for_speech(ms: int) -> str:
        total_seconds = max(0, round(ms / 1000))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        parts = []
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if seconds or not parts:
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        if len(parts) == 1:
            return parts[0]
        return f"{', '.join(parts[:-1])} and {parts[-1]}"

    def describe_timer(self, timer: TimerEntry) -> str:
        label = timer.label.strip()
        if not label:
            return "Reminder" if timer.kind == "reminder" else "Timer"
        if timer.kind == "reminder":
            return f"Reminder: {label}"
        return label if label.lower().endswith("timer") else f"{label} timer"

    async def add_timer(self, kind: str, duration_ms: int, label: str) -> TimerEntry:
        timer_id = uuid.uuid4().hex
        fire_at = time.time() + max(0, duration_ms) / 1000

        async def timer_task() -> None:
            try:
                await asyncio.sleep(max(0, fire_at - time.time()))
                timer = self.timers.pop(timer_id, None)
                if timer:
                    announcement = f"{self.describe_timer(timer)} done."
                    log(announcement)
                    await asyncio.to_thread(powershell_speak, announcement)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(timer_task())
        timer = TimerEntry(
            id=timer_id,
            kind="reminder" if kind == "reminder" else "timer",
            label=(label or "").strip(),
            fire_at=fire_at,
            task=task,
        )
        self.timers[timer_id] = timer
        return timer

    def cancel_timer(self, timer_id: str) -> TimerEntry | None:
        timer = self.timers.pop(timer_id, None)
        if timer:
            timer.task.cancel()
        return timer

    async def record_until_silence(self, *, no_speech_timeout: float = NO_SPEECH_TIMEOUT_SECONDS) -> bytes:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        chunks: list[bytes] = []
        started_at = time.monotonic()
        speech_seen = False
        silent_since: float | None = None

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
            del frames, time_info, status
            data = bytes(indata)
            def enqueue() -> None:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(data)

            loop.call_soon_threadsafe(enqueue)

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            callback=callback,
        ):
            while self.running:
                now = time.monotonic()
                if now - started_at > MAX_RECORD_SECONDS:
                    break
                if not speech_seen and now - started_at > no_speech_timeout:
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                chunks.append(data)
                rms = audio_rms(np.frombuffer(data, dtype=np.int16))
                if rms >= SILENCE_THRESHOLD:
                    speech_seen = True
                    silent_since = None
                elif speech_seen and now - started_at >= MIN_RECORD_SECONDS:
                    silent_since = silent_since or now
                    if now - silent_since >= SILENCE_SECONDS:
                        break
        return pcm_to_wav_bytes(chunks)

    async def transcribe_wav(self, wav_bytes: bytes) -> str:
        if not wav_bytes:
            return ""
        files = {"audio": ("native-recording.wav", wav_bytes, "audio/wav")}
        response = await self.http.post(f"{BASE_URL}/api/transcribe?mode=fast", files=files)
        response.raise_for_status()
        data = response.json()
        text = str(data.get("text") or "").strip()
        log(f"transcribed: {text!r}")
        return text

    async def assistant_turn(self, text: str) -> dict[str, Any]:
        weather_context = await self.refresh_weather_context()
        payload = {
            "text": text,
            "messages": self.messages[-8:],
            "active_timers": self.active_timer_summaries(),
            "pending_timer_setup": self.pending_timer_setup,
            "weather_context": weather_context,
            "system_prompt": SYSTEM_PROMPT,
            "temperature": TEMPERATURE,
        }
        response = await self.http.post(f"{BASE_URL}/api/assistant/turn", json=payload)
        response.raise_for_status()
        return response.json()

    async def weather_reply(self, user_text: str, location: str) -> str:
        lookup = await self.http.get(f"{BASE_URL}/api/weather/lookup", params={"location": location})
        lookup.raise_for_status()
        weather = lookup.json()
        speak_response = await self.http.post(
            f"{BASE_URL}/api/weather/speak",
            json={
                "user_text": user_text,
                "location": weather.get("location") or location,
                "weather_summary": weather.get("summary") or "",
                "system_prompt": SYSTEM_PROMPT,
                "temperature": TEMPERATURE,
            },
        )
        speak_response.raise_for_status()
        return str(speak_response.json().get("spoken") or weather.get("summary") or "")

    async def handle_assistant_result(self, user_text: str, result: dict[str, Any]) -> bool:
        intent = str(result.get("intent") or "chat")
        spoken = str(result.get("spoken") or "").strip() or "Okay."
        self.messages.append({"role": "user", "content": user_text})

        if intent == "start_timer" and int(result.get("duration_ms") or 0) > 0:
            duration_ms = int(result.get("duration_ms") or 0)
            timer = await self.add_timer(
                str(result.get("kind") or "timer"),
                duration_ms,
                str(result.get("label") or ""),
            )
            self.pending_timer_setup = None
            reply = spoken or f"{self.describe_timer(timer)} set for {self.format_duration_for_speech(duration_ms)}."
            self.messages.append({"role": "assistant", "content": reply})
            log(f"assistant: {reply}")
            await asyncio.to_thread(powershell_speak, reply)
            return False

        if intent == "ask_timer_duration":
            self.pending_timer_setup = {
                "kind": str(result.get("kind") or "timer"),
                "label": str(result.get("label") or ""),
                "sourceText": user_text,
                "promptText": spoken,
            }
            self.messages.append({"role": "assistant", "content": spoken})
            log(f"assistant: {spoken}")
            await asyncio.to_thread(powershell_speak, spoken)
            return True

        if intent == "cancel_timer":
            timer = self.cancel_timer(str(result.get("target_timer_id") or ""))
            reply = spoken if timer else "Which timer should I cancel?"
            self.pending_timer_setup = None
            self.messages.append({"role": "assistant", "content": reply})
            log(f"assistant: {reply}")
            await asyncio.to_thread(powershell_speak, reply)
            return False

        if intent == "cancel_all_timers":
            for timer in list(self.timers.values()):
                timer.task.cancel()
            self.timers.clear()
            self.pending_timer_setup = None
            reply = spoken or "All timers canceled."
            self.messages.append({"role": "assistant", "content": reply})
            log(f"assistant: {reply}")
            await asyncio.to_thread(powershell_speak, reply)
            return False

        if intent == "get_weather":
            location = str(result.get("weather_location") or "").strip()
            if location:
                try:
                    spoken = await self.weather_reply(user_text, location)
                except Exception as exc:
                    spoken = f"I could not get weather for {location}."
                    await self.client_log("native_weather_lookup", user_text, "failed", str(exc))
            self.pending_timer_setup = None

        if intent == "end_conversation":
            self.pending_timer_setup = None
            self.messages.append({"role": "assistant", "content": spoken})
            log(f"assistant: {spoken}")
            await asyncio.to_thread(powershell_speak, spoken)
            return False

        if intent == "chat":
            self.pending_timer_setup = None

        self.messages.append({"role": "assistant", "content": spoken})
        log(f"assistant: {spoken}")
        await asyncio.to_thread(powershell_speak, spoken)
        return True

    async def handle_text(self, text: str) -> bool:
        clean = " ".join((text or "").split()).strip()
        if not clean:
            return False
        log(f"user: {clean}")
        await self.client_log("native_user_text", clean, "forwarded")
        try:
            result = await self.assistant_turn(clean)
        except Exception as exc:
            log(f"assistant_turn failed: {exc}")
            await asyncio.to_thread(powershell_speak, "I had trouble reaching Gemma.")
            return False
        return await self.handle_assistant_result(clean, result)

    async def run_conversation(self, initial_text: str) -> None:
        continue_conversation = await self.handle_text(initial_text)
        while self.running and continue_conversation:
            log("conversation mode: listening for follow-up")
            wav = await self.record_until_silence(no_speech_timeout=CONVERSATION_TIMEOUT_SECONDS)
            transcript = await self.transcribe_wav(wav)
            if not transcript:
                log("conversation mode timed out")
                break
            continue_conversation = await self.handle_text(transcript)

    async def wait_for_wake(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
        wake_url = f"{WS_URL}/api/wake/listen?threshold={WAKE_THRESHOLD}&ambientContextSeconds=2.5"

        def callback(indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
            del frames, time_info, status
            data = bytes(indata)
            def enqueue() -> None:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(data)

            loop.call_soon_threadsafe(enqueue)

        async with websockets.connect(wake_url, max_size=2**20) as websocket:
            async def sender() -> None:
                while self.running:
                    data = await queue.get()
                    await websocket.send(data)

            sender_task = asyncio.create_task(sender())
            try:
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=BLOCK_SIZE,
                    callback=callback,
                ):
                    async for message in websocket:
                        data = json.loads(message)
                        message_type = data.get("type")
                        if message_type == "ready":
                            log(f"listening for {data.get('keyword')!r}")
                        elif message_type == "wake":
                            log(f"wake detected: {data.get('transcript')!r}")
                            return data
            finally:
                sender_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender_task
        return {}

    async def run(self) -> None:
        health = await self.http.get(f"{BASE_URL}/api/health")
        health.raise_for_status()
        log(f"connected to {BASE_URL}")
        log("native listener ready")

        while self.running:
            try:
                wake = await self.wait_for_wake()
                if not wake:
                    continue
                command_text = str(wake.get("command") or "").strip()
                wake_transcript = str(wake.get("transcript") or "").strip()
                prefill = wake_transcript or command_text
                wav = await self.record_until_silence(
                    no_speech_timeout=1.8 if command_text else NO_SPEECH_TIMEOUT_SECONDS
                )
                transcript = await self.transcribe_wav(wav)
                text = merge_prefill(prefill, transcript)
                await self.run_conversation(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"listener error: {exc}")
                await asyncio.sleep(2)


async def async_main() -> int:
    client = NativeVoiceClient()
    write_pid_file()

    def stop() -> None:
        client.running = False

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, stop)
        loop.add_signal_handler(signal.SIGTERM, stop)

    try:
        await client.run()
    finally:
        await client.close()
        remove_pid_file()
    return 0


def main() -> int:
    if sys.platform != "win32":
        log("This native client currently targets Windows.")
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
