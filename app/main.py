from __future__ import annotations

import json
import asyncio
import logging
import os
import tempfile
import time
import uuid
import sys
import shutil
import subprocess
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SERVER_PID_FILE = BASE_DIR.parent / "server.pid"
LLAMA_EXE = BASE_DIR.parent / "tools" / "llama-cpp" / "llama-server.exe"
LLAMA_CPP_DIR = LLAMA_EXE.parent
LLAMA_PID_FILE = BASE_DIR.parent / "llama-server.pid"
LLAMA_OUT_LOG = BASE_DIR.parent / "llama-server.out.log"
LLAMA_ERR_LOG = BASE_DIR.parent / "llama-server.err.log"
LLAMA_START_SCRIPT = BASE_DIR.parent / "start-llama-server.cmd"

if LLAMA_CPP_DIR.exists():
    try:
        os.add_dll_directory(str(LLAMA_CPP_DIR))
    except (AttributeError, OSError):
        pass

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "llama").strip().lower()
LLAMA_BASE_URL = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "google/gemma-4-E4B-it-qat-q4_0-gguf:Q4_0")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
OLLAMA_BINARY = os.getenv("OLLAMA_BINARY") or shutil.which("ollama") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium.en")
WHISPER_WAKE_MODEL = os.getenv("WHISPER_WAKE_MODEL", WHISPER_MODEL)
WHISPER_FAST_MODEL = os.getenv("WHISPER_FAST_MODEL", WHISPER_MODEL)
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "3"))
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WAKE_PHRASES = tuple(
    phrase.strip().lower()
    for phrase in os.getenv("WAKE_WORDS", os.getenv("WAKE_WORD", "alexa")).split(",")
    if phrase.strip()
)
WAKE_WORD = WAKE_PHRASES[0] if WAKE_PHRASES else "alexa"
WAKE_SENSITIVITY = float(os.getenv("WAKE_SENSITIVITY", "0.45"))
WAKE_CHUNK_SECONDS = float(os.getenv("WAKE_CHUNK_SECONDS", "2.0"))
WAKE_OVERLAP_SECONDS = float(os.getenv("WAKE_OVERLAP_SECONDS", "0.8"))
WAKE_COOLDOWN_SECONDS = float(os.getenv("WAKE_COOLDOWN_SECONDS", "2.0"))
WAKE_COMMAND_GRACE_SECONDS = float(os.getenv("WAKE_COMMAND_GRACE_SECONDS", "2.0"))
WAKE_INCOMPLETE_COMMAND_MAX_SECONDS = float(os.getenv("WAKE_INCOMPLETE_COMMAND_MAX_SECONDS", "5.0"))
WAKE_AMBIENT_CONTEXT_SECONDS = float(os.getenv("WAKE_AMBIENT_CONTEXT_SECONDS", "2.5"))
WAKE_AMBIENT_PRETRIGGER_SECONDS = float(os.getenv("WAKE_AMBIENT_PRETRIGGER_SECONDS", "6.0"))
WAKE_AMBIENT_MAX_SECONDS = float(os.getenv("WAKE_AMBIENT_MAX_SECONDS", "70.0"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "-1")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
MAX_CHAT_MESSAGES = int(os.getenv("MAX_CHAT_MESSAGES", "8"))
MAX_CHAT_CHARS = int(os.getenv("MAX_CHAT_CHARS", "6000"))

SYSTEM_PROMPT = (
    "You are a concise local voice assistant. Your name is Alexa. Reply conversationally. "
    "Keep spoken answers short unless the user asks for detail. "
    "Do not use emojis, emoticons, or decorative symbols."
)

app = FastAPI(title="Local Gemma Voice Chat")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
logger = logging.getLogger("gemma_voice")
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    file_handler = logging.FileHandler(BASE_DIR / "voice-app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

_whisper_models: dict[str, WhisperModel] = {}


def log_excerpt(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def current_datetime_context() -> str:
    now = datetime.now().astimezone()
    return (
        f"Local date: {now.strftime('%A, %B %d, %Y')}. "
        f"Local time: {now.strftime('%I:%M %p %Z').lstrip('0')}. "
        f"ISO timestamp: {now.isoformat(timespec='seconds')}."
    )


def weather_user_agent() -> str:
    return "GemmaVoiceLocal/1.0 (local app)"


def format_weather_period(period: dict[str, Any]) -> str:
    name = str(period.get("name") or "").strip()
    temperature = period.get("temperature")
    unit = str(period.get("temperatureUnit") or "").strip()
    forecast = " ".join(str(period.get("shortForecast") or "").split())
    wind = " ".join(
        f"{period.get('windSpeed') or ''} {period.get('windDirection') or ''}".split()
    )
    parts = []
    if name:
        parts.append(name)
    if temperature is not None and unit:
        parts.append(f"{temperature}{unit}")
    if forecast:
        parts.append(forecast)
    if wind:
        parts.append(f"wind {wind}")
    return ": ".join(parts[:1] + [", ".join(parts[1:])]) if parts else ""


WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

US_STATE_NAMES = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
    "dc": "district of columbia",
}


def weather_code_description(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "Unknown conditions"
    return WEATHER_CODE_DESCRIPTIONS.get(code, "Unknown conditions")


def format_temperature(value: Any) -> str:
    try:
        return f"{round(float(value))}F"
    except (TypeError, ValueError):
        return ""


def format_percent(value: Any) -> str:
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return ""


def location_search_parts(location: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in re.split(r"[,;]", location) if part.strip()]
    if len(parts) < 2:
        return location, []
    city = parts[0]
    region_terms = []
    for part in parts[1:]:
        normalized = re.sub(r"[^a-zA-Z ]+", "", part).strip().lower()
        if not normalized:
            continue
        region_terms.append(US_STATE_NAMES.get(normalized, normalized))
    return city, region_terms


def select_geocode_result(results: list[dict[str, Any]], region_terms: list[str]) -> dict[str, Any] | None:
    if not results:
        return None
    if not region_terms:
        return results[0]

    for result in results:
        admin1 = str(result.get("admin1") or "").strip().lower()
        admin2 = str(result.get("admin2") or "").strip().lower()
        country = str(result.get("country") or "").strip().lower()
        country_code = str(result.get("country_code") or "").strip().lower()
        haystack = " ".join([admin1, admin2, country, country_code])
        if any(term and term in haystack for term in region_terms):
            return result
    return results[0]


async def open_meteo_weather_lookup(location: str) -> dict[str, str]:
    clean_location = " ".join(location.split())[:160]
    if not clean_location:
        raise HTTPException(status_code=400, detail="Location is required")

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        city_query, region_terms = location_search_parts(clean_location)
        search_queries = [clean_location]
        if city_query != clean_location:
            search_queries.append(city_query)

        results: list[dict[str, Any]] = []
        for query in search_queries:
            geocode_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": query,
                    "count": 10 if region_terms else 1,
                    "language": "en",
                    "format": "json",
                },
            )
            geocode_response.raise_for_status()
            results = geocode_response.json().get("results") or []
            if results:
                break
        if not results:
            raise HTTPException(status_code=404, detail=f"Location not found: {clean_location}")

        place = select_geocode_result(results, region_terms)
        if not place:
            raise HTTPException(status_code=404, detail=f"Location not found: {clean_location}")
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if latitude is None or longitude is None:
            raise HTTPException(status_code=404, detail=f"Location not found: {clean_location}")

        display_location = ", ".join(
            part
            for part in [
                str(place.get("name") or "").strip(),
                str(place.get("admin1") or "").strip(),
                str(place.get("country_code") or place.get("country") or "").strip(),
            ]
            if part
        )

        forecast_response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "forecast_days": 4,
                "timezone": "auto",
            },
        )
        forecast_response.raise_for_status()
        forecast = forecast_response.json()

    current = forecast.get("current") or {}
    current_parts = [
        weather_code_description(current.get("weather_code")),
        format_temperature(current.get("temperature_2m")),
    ]
    humidity = format_percent(current.get("relative_humidity_2m"))
    if humidity:
        current_parts.append(f"humidity {humidity}")
    try:
        current_parts.append(f"wind {round(float(current.get('wind_speed_10m')))} mph")
    except (TypeError, ValueError):
        pass

    daily = forecast.get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_probability_max") or []
    forecast_parts = []
    for index, date_text in enumerate(dates[:4]):
        day_name = "Today" if index == 0 else "Tomorrow" if index == 1 else str(date_text)
        day_parts = [day_name, weather_code_description(codes[index] if index < len(codes) else None)]
        high = format_temperature(highs[index] if index < len(highs) else None)
        low = format_temperature(lows[index] if index < len(lows) else None)
        rain_chance = format_percent(precip[index] if index < len(precip) else None)
        if high:
            day_parts.append(f"high {high}")
        if low:
            day_parts.append(f"low {low}")
        if rain_chance:
            day_parts.append(f"precipitation chance {rain_chance}")
        forecast_parts.append(": ".join([day_parts[0], ", ".join(day_parts[1:])]))

    summary_parts = [
        f"Location: {display_location or clean_location}",
        f"Current: {', '.join(part for part in current_parts if part)}",
    ]
    if forecast_parts:
        summary_parts.append(f"Forecast: {' | '.join(forecast_parts)}")
    summary = ". ".join(part for part in summary_parts if part)
    logger.info("weather_lookup location=%r resolved=%r summary=%r", clean_location, display_location, log_excerpt(summary))
    return {"summary": summary[:1200], "location": display_location or clean_location}


def parse_keep_alive(value: str) -> int | str:
    stripped = value.strip()
    try:
        return int(stripped)
    except ValueError:
        return stripped


OLLAMA_KEEP_ALIVE_VALUE = parse_keep_alive(OLLAMA_KEEP_ALIVE)


def active_model_name() -> str:
    return LLAMA_MODEL if LLM_PROVIDER == "llama" else OLLAMA_MODEL


def active_base_url() -> str:
    return LLAMA_BASE_URL if LLM_PROVIDER == "llama" else OLLAMA_URL


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    think: bool | str = False
    system_prompt: str | None = None
    weather_context: str = ""
    temperature: float = 0.7


class IntentRequest(BaseModel):
    text: str


class IntentResponse(BaseModel):
    intent: str
    confidence: float = 0.0
    reason: str = ""
    seconds: float = 0.0


class TimerParseRequest(BaseModel):
    text: str
    context: str = ""


class TimerParseResponse(BaseModel):
    action: str = "continue"
    kind: str = "timer"
    duration_ms: int = 0
    label: str = ""
    confidence: float = 0.0
    seconds: float = 0.0


class TimerCancelParseRequest(BaseModel):
    text: str
    active_timers: list[dict[str, Any]] = Field(default_factory=list)


class TimerCancelParseResponse(BaseModel):
    action: str = "continue"
    target_id: str = ""
    confidence: float = 0.0
    reason: str = ""
    seconds: float = 0.0


class AssistantTurnRequest(BaseModel):
    text: str
    messages: list[ChatMessage] = Field(default_factory=list)
    active_timers: list[dict[str, Any]] = Field(default_factory=list)
    pending_timer_setup: dict[str, Any] | None = None
    weather_context: str = ""
    system_prompt: str | None = None
    temperature: float = 0.7


class AssistantTurnResponse(BaseModel):
    intent: str = "chat"
    kind: str = ""
    duration_ms: int = 0
    label: str = ""
    target_timer_id: str = ""
    weather_location: str = ""
    spoken: str = ""
    raw_response: str = ""
    confidence: float = 0.0
    reason: str = ""
    seconds: float = 0.0


class ProactiveOpenRequest(BaseModel):
    context: str = ""
    weather_context: str = ""
    system_prompt: str | None = None
    temperature: float = 0.7


class ProactiveOpenResponse(BaseModel):
    spoken: str = ""
    raw_response: str = ""
    seconds: float = 0.0


class WeatherSpeakRequest(BaseModel):
    user_text: str = ""
    location: str = ""
    weather_summary: str = ""
    system_prompt: str | None = None
    temperature: float = 0.4


class WeatherSpeakResponse(BaseModel):
    spoken: str = ""
    raw_response: str = ""
    seconds: float = 0.0


def parse_assistant_turn_data(content: str) -> tuple[dict[str, Any] | None, bool]:
    try:
        return json.loads(content), False
    except json.JSONDecodeError:
        pass

    recovered: dict[str, Any] = {}
    string_fields = {
        "intent",
        "kind",
        "timer_kind",
        "label",
        "target_timer_id",
        "weather_location",
        "spoken",
        "reason",
        "duration_unit",
    }
    for field in string_fields:
        match = re.search(rf'"{field}"\s*:\s*"([^"]*)"', content)
        if match:
            recovered[field] = match.group(1)

    for field in {"duration_ms", "duration_value", "confidence"}:
        match = re.search(rf'"{field}"\s*:\s*(-?\d+(?:\.\d+)?)', content)
        if match:
            recovered[field] = match.group(1)

    if "intent" not in recovered:
        return None, False

    recovered.setdefault("kind", "")
    recovered.setdefault("timer_kind", "")
    recovered.setdefault("duration_ms", 0)
    recovered.setdefault("duration_value", 0)
    recovered.setdefault("duration_unit", "")
    recovered.setdefault("label", "")
    recovered.setdefault("target_timer_id", "")
    recovered.setdefault("spoken", "")
    recovered.setdefault("confidence", 0)
    recovered.setdefault("reason", "recovered partial JSON")
    return recovered, True


def duration_fields_to_ms(data: dict[str, Any]) -> int:
    try:
        value = float(data.get("duration_value", 0) or 0)
    except (TypeError, ValueError):
        value = 0

    unit = str(data.get("duration_unit", "") or "").lower().strip()
    unit_multipliers = {
        "second": 1000,
        "seconds": 1000,
        "sec": 1000,
        "secs": 1000,
        "minute": 60_000,
        "minutes": 60_000,
        "min": 60_000,
        "mins": 60_000,
        "hour": 3_600_000,
        "hours": 3_600_000,
        "hr": 3_600_000,
        "hrs": 3_600_000,
    }
    multiplier = unit_multipliers.get(unit, 0)
    if value > 0 and multiplier:
        return int(round(value * multiplier))

    try:
        return int(float(data.get("duration_ms", 0) or 0))
    except (TypeError, ValueError):
        return 0


def simple_duration_value_from_text(text: str, unit: str) -> float:
    normalized = " ".join((text or "").lower().split())
    normalized_unit = (unit or "").lower().strip()
    if not normalized or not normalized_unit:
        return 0

    unit_roots = {
        "second": "second",
        "seconds": "second",
        "sec": "second",
        "secs": "second",
        "minute": "minute",
        "minutes": "minute",
        "min": "minute",
        "mins": "minute",
        "hour": "hour",
        "hours": "hour",
        "hr": "hour",
        "hrs": "hour",
    }
    root = unit_roots.get(normalized_unit)
    if not root:
        return 0

    digit_match = re.search(rf"\b(\d+(?:\.\d+)?)\s+{root}s?\b", normalized)
    if digit_match:
        return float(digit_match.group(1))

    number_words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "thirty": 30,
    }
    word_match = re.search(rf"\b({'|'.join(number_words)})\s+{root}s?\b", normalized)
    if word_match:
        return float(number_words[word_match.group(1)])

    return 0


def recover_tool_call_assistant_turn(content: str, text: str) -> dict[str, Any] | None:
    if "tool_call" not in content and "call:" not in content:
        return None

    call_match = re.search(r"call:([A-Za-z_][A-Za-z0-9_]*)", content)
    if not call_match:
        return None

    action = call_match.group(1)
    action_to_intent = {
        "start_timer": "start_timer",
        "cancel_timer": "cancel_timer",
        "cancel_all_timers": "cancel_all_timers",
        "end_conversation": "end_conversation",
        "ask_timer_duration": "ask_timer_duration",
    }
    intent = action_to_intent.get(action)
    if not intent:
        return None

    normalized = content.replace("<|\"|>", '"')
    data: dict[str, Any] = {
        "intent": intent,
        "timer_kind": "timer" if intent in {"start_timer", "ask_timer_duration"} else "",
        "duration_value": 0,
        "duration_unit": "",
        "label": "",
        "target_timer_id": "",
        "spoken": "",
        "confidence": 0.85,
        "reason": "recovered tool-call output",
    }

    for field in {"kind", "timer_kind", "duration_unit", "label", "target_timer_id", "spoken"}:
        match = re.search(rf"{field}\s*:\s*\"([^\"]*)\"", normalized)
        if not match:
            match = re.search(rf"{field}\s*:\s*([^,}}<]+)", normalized)
        if match:
            data[field] = match.group(1).strip()

    if data.get("kind") and not data.get("timer_kind"):
        data["timer_kind"] = data["kind"]

    for field in {"duration_value", "duration_ms", "confidence"}:
        match = re.search(rf"{field}\s*:\s*(-?\d+(?:\.\d+)?)", normalized)
        if match:
            data[field] = match.group(1)

    if intent == "start_timer" and not float(data.get("duration_value", 0) or 0):
        inferred_value = simple_duration_value_from_text(
            f"{data.get('spoken', '')} {text}",
            str(data.get("duration_unit", "")),
        )
        if inferred_value:
            data["duration_value"] = inferred_value

    return data


def recover_plain_assistant_turn(content: str, text: str) -> dict[str, Any] | None:
    spoken = " ".join((content or "").split()).strip()
    if not spoken or spoken.startswith("{"):
        return None

    normalized_spoken = spoken.lower().strip(" .!?:;-")
    normalized_text = " ".join((text or "").lower().split())
    cancel_all_markers = {
        "cancel all timers",
        "cancelling all timers",
        "canceling all timers",
        "all timers cancelled",
        "all timers canceled",
        "all timers have been cancelled",
        "all timers have been canceled",
    }
    if any(marker in normalized_spoken for marker in cancel_all_markers) or (
        "all" in normalized_text
        and "timer" in normalized_text
        and any(word in normalized_text for word in {"cancel", "canceling", "cancelling", "council", "clear", "stop"})
    ):
        return {
            "intent": "cancel_all_timers",
            "timer_kind": "",
            "duration_ms": 0,
            "label": "",
            "target_timer_id": "",
            "spoken": spoken,
            "confidence": 0.9,
            "reason": "recovered plain cancel_all_timers",
        }

    return {
        "intent": "chat",
        "timer_kind": "",
        "duration_ms": 0,
        "label": "",
        "target_timer_id": "",
        "spoken": spoken,
        "confidence": 0.7,
        "reason": "recovered plain response",
    }


def location_from_weather_context(weather_context: str) -> str:
    match = re.search(r"(?:^|\b)Location:\s*([^.;|]+)", weather_context or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return " ".join(match.group(1).split()).strip()[:160]


def weather_location_from_checking_spoken(spoken: str) -> str:
    match = re.search(
        r"\bchecking\s+(?:the\s+)?weather\s+for\s+(.+?)\s*[.!?]*$",
        spoken or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    location = re.sub(r"\b(?:right\s+now|today|tomorrow)\b", "", match.group(1), flags=re.IGNORECASE)
    return " ".join(location.split()).strip(" ,.;:")[:160]


class ClientLogRequest(BaseModel):
    event: str
    text: str = ""
    decision: str = ""
    detail: str = ""


class OllamaRestartResponse(BaseModel):
    ok: bool
    message: str


async def ensure_llama_server_ready(reason: str, timeout_seconds: float = 90) -> bool:
    if LLM_PROVIDER != "llama":
        return True

    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{LLAMA_BASE_URL}/health")
            if response.status_code == 200:
                return True
    except httpx.HTTPError:
        pass

    if not LLAMA_EXE.exists():
        logger.warning("llama-server executable missing reason=%s path=%s", reason, LLAMA_EXE)
        return False

    started_at = time.perf_counter()
    logger.warning("llama-server recovery start reason=%s", reason)
    try:
        with LLAMA_OUT_LOG.open("ab") as stdout, LLAMA_ERR_LOG.open("ab") as stderr:
            process = subprocess.Popen(
                [
                    str(LLAMA_EXE),
                    "--hf-repo",
                    LLAMA_MODEL,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8080",
                    "--ctx-size",
                    "4096",
                    "--n-gpu-layers",
                    "999",
                    "--jinja",
                    "--reasoning",
                    "off",
                ],
                cwd=str(BASE_DIR.parent),
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        LLAMA_PID_FILE.write_text(str(process.pid), encoding="utf-8")
        logger.warning("llama-server recovery process started reason=%s pid=%s", reason, process.pid)
    except OSError as exc:
        logger.warning("llama-server recovery failed to start reason=%s error=%s", reason, exc)
        return False

    deadline = time.perf_counter() + timeout_seconds
    last_error = ""
    while time.perf_counter() < deadline:
        if process.poll() is not None:
            logger.warning(
                "llama-server recovery process exited reason=%s code=%s seconds=%.2f",
                reason,
                process.returncode,
                time.perf_counter() - started_at,
            )
            return False
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{LLAMA_BASE_URL}/health")
                if response.status_code == 200:
                    logger.info(
                        "llama-server recovery ready reason=%s seconds=%.2f pid=%s",
                        reason,
                        time.perf_counter() - started_at,
                        process.pid,
                    )
                    return True
        except httpx.HTTPError as exc:
            last_error = str(exc)
        await asyncio.sleep(1)

    logger.warning(
        "llama-server recovery timed out reason=%s seconds=%.2f last_error=%s",
        reason,
        time.perf_counter() - started_at,
        last_error,
    )
    return False

async def warm_ollama_model(reason: str) -> None:
    if LLM_PROVIDER == "llama":
        payload = {
            "model": LLAMA_MODEL,
            "messages": [
                {"role": "user", "content": "Reply with exactly one word: ready"},
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 8,
        }
        started_at = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{LLAMA_BASE_URL}/v1/chat/completions", json=payload)
                response.raise_for_status()
            logger.info(
                "llm warmup complete provider=llama reason=%s seconds=%.2f model=%s",
                reason,
                time.perf_counter() - started_at,
                LLAMA_MODEL,
            )
        except httpx.HTTPError as exc:
            logger.warning("llm warmup failed provider=llama reason=%s error=%s", reason, exc)
        return

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "user", "content": "Reply with exactly one word: ready"},
        ],
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE_VALUE,
        "think": False,
        "options": {
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 64,
            "num_ctx": OLLAMA_NUM_CTX,
        },
    }

    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
        logger.info(
            "ollama warmup complete reason=%s seconds=%.2f model=%s",
            reason,
            time.perf_counter() - started_at,
            OLLAMA_MODEL,
        )
    except httpx.HTTPError as exc:
        logger.warning("ollama warmup failed reason=%s error=%s", reason, exc)


@app.on_event("startup")
async def startup_warmup() -> None:
    try:
        SERVER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to write server pid file path=%s error=%s", SERVER_PID_FILE, exc)
    asyncio.create_task(warm_ollama_model("startup"))


def get_whisper_model(model_name: str) -> WhisperModel:
    if model_name not in _whisper_models:
        _whisper_models[model_name] = WhisperModel(
            model_name,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _whisper_models[model_name]


def trim_chat_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    if not messages:
        return []

    selected: list[ChatMessage] = []
    total_chars = 0

    for message in reversed(messages):
        content = message.content or ""
        message_cost = len(content)
        if selected and (
            len(selected) >= MAX_CHAT_MESSAGES or total_chars + message_cost > MAX_CHAT_CHARS
        ):
            break
        selected.append(message)
        total_chars += message_cost

    selected.reverse()
    return selected


def wake_sensitivity_to_energy_threshold(sensitivity: float) -> float:
    bounded = max(0.2, min(0.9, sensitivity))
    return 0.002 + (0.9 - bounded) * (0.018 / 0.7)


def audio_rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples.astype(np.float32, copy=False)))))


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    tokens = [re.escape(token) for token in " ".join(phrase.split()).split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(tokens) + r"(?!\w)", flags=re.IGNORECASE)


WAKE_PATTERNS = {phrase: phrase_pattern(phrase) for phrase in WAKE_PHRASES}


def ambient_conversation_candidate(transcript: str) -> bool:
    normalized = " ".join((transcript or "").split()).strip(" ,.!?:;-")
    if not normalized:
        return False
    words = normalized.split()
    if len(words) < 4:
        return False
    lowered = normalized.lower()
    filler_phrases = {
        "thank you",
        "thanks for watching",
        "you",
        "yeah",
        "okay",
        "hmm",
    }
    return lowered not in filler_phrases


def detect_wake_phrase(transcript: str) -> tuple[str, str] | None:
    normalized = " ".join(transcript.split())
    if not normalized:
        return None

    for phrase, pattern in WAKE_PATTERNS.items():
        match = pattern.search(normalized)
        if match:
            command_text = normalized[match.end() :].strip(" ,!?:;-")
            return phrase, command_text
    return None


def wake_command_is_incomplete(command_text: str) -> bool:
    raw = " ".join(command_text.lower().split())
    if raw.endswith("...") or raw.endswith("…"):
        return True

    normalized = raw.strip(" ,.!?:;-")
    if not normalized:
        return True

    words = normalized.split()
    if len(words) <= 1:
        return True

    incomplete_endings = {
        "a",
        "an",
        "are",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "is",
        "of",
        "should",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "would",
    }
    if words[-1] in incomplete_endings:
        return True

    incomplete_starts = {
        "can",
        "can't",
        "could",
        "would",
        "will",
        "please",
        "set",
        "start",
        "make",
        "create",
        "cancel",
        "stop",
        "remove",
        "delete",
        "kill",
        "council",
        "counsel",
        "castle",
    }
    return len(words) <= 2 and words[0] in incomplete_starts


def wake_command_should_wait_for_continuation(command_text: str) -> bool:
    normalized = " ".join(command_text.lower().split()).strip(" ,.!?:;-")
    if wake_command_is_incomplete(normalized):
        return True

    timer_setup_words = {"set", "setup", "start", "create", "make"}
    timer_words = {"timer", "timers", "time", "reminder", "reminders"}
    duration_units = {
        "second",
        "seconds",
        "sec",
        "secs",
        "minute",
        "minutes",
        "min",
        "mins",
        "hour",
        "hours",
    }
    number_words = {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "twenty",
        "thirty",
    }
    words = normalized.split()
    has_setup = any(word in timer_setup_words for word in words)
    has_timer = any(word in timer_words for word in words)
    has_duration = any(word in duration_units for word in words) and (
        bool(re.search(r"\b\d+(\.\d+)?\b", normalized)) or any(word in number_words for word in words)
    )
    return has_setup and has_timer and not has_duration


def transcribe_wake_samples(samples: np.ndarray) -> tuple[str, float | None]:
    model = get_whisper_model(WHISPER_WAKE_MODEL)
    segments, _info = model.transcribe(
        samples.astype(np.float32, copy=False),
        language="en",
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )

    texts: list[str] = []
    log_probs: list[float] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            texts.append(text)
        avg_logprob = getattr(segment, "avg_logprob", None)
        if avg_logprob is not None:
            log_probs.append(float(avg_logprob))

    confidence = sum(log_probs) / len(log_probs) if log_probs else None
    return " ".join(texts).strip(), confidence


def transcribe_ambient_context_samples(samples: np.ndarray) -> tuple[str, float | None]:
    if samples.size < 16000:
        return "", None

    model = get_whisper_model(WHISPER_WAKE_MODEL)
    segments, _info = model.transcribe(
        samples.astype(np.float32, copy=False),
        language="en",
        beam_size=max(WHISPER_BEAM_SIZE, 3),
        vad_filter=True,
        condition_on_previous_text=False,
        without_timestamps=True,
    )

    texts: list[str] = []
    log_probs: list[float] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            texts.append(text)
        avg_logprob = getattr(segment, "avg_logprob", None)
        if avg_logprob is not None:
            log_probs.append(float(avg_logprob))

    confidence = sum(log_probs) / len(log_probs) if log_probs else None
    return " ".join(texts).strip(), confidence


def openai_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


async def llm_chat_once(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0,
    max_tokens: int = 256,
    json_mode: bool = False,
    timeout: float = 30,
    num_ctx: int | None = None,
) -> tuple[str, dict[str, Any]]:
    if LLM_PROVIDER == "llama":
        payload: dict[str, Any] = {
            "model": LLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(f"{LLAMA_BASE_URL}/v1/chat/completions", json=payload)
                    response.raise_for_status()
                data = response.json()
                return openai_message_content(data), data
            except httpx.HTTPError as exc:
                if attempt == 0:
                    logger.warning("llm one-shot failed; attempting llama-server recovery error=%s", exc)
                    if await ensure_llama_server_ready("llm_chat_once_retry"):
                        continue
                raise

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE_VALUE,
        "options": {
            "temperature": temperature,
            "top_p": 0.95,
            "top_k": 64,
            "num_ctx": num_ctx or OLLAMA_NUM_CTX,
            "num_predict": max_tokens,
        },
    }
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        response.raise_for_status()
    data = response.json()
    return str(data.get("message", {}).get("content", "")), data


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def config() -> dict[str, str]:
    return {
        "llmProvider": LLM_PROVIDER,
        "llmUrl": active_base_url(),
        "llmModel": active_model_name(),
        "ollamaUrl": OLLAMA_URL,
        "ollamaModel": OLLAMA_MODEL,
        "whisperModel": WHISPER_MODEL,
        "whisperFastModel": WHISPER_FAST_MODEL,
        "whisperWakeModel": WHISPER_WAKE_MODEL,
        "whisperDevice": WHISPER_DEVICE,
        "whisperComputeType": WHISPER_COMPUTE_TYPE,
        "wakeWord": WAKE_WORD,
        "wakePhrases": ", ".join(WAKE_PHRASES),
        "wakeDetector": "faster-whisper",
        "wakeThreshold": f"{WAKE_SENSITIVITY:.2f}",
    }


@app.get("/api/weather")
async def weather(lat: float, lon: float) -> dict[str, str]:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid latitude or longitude")

    headers = {
        "User-Agent": weather_user_agent(),
        "Accept": "application/geo+json",
    }
    try:
        async with httpx.AsyncClient(timeout=12, headers=headers, follow_redirects=True) as client:
            points_response = await client.get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
            points_response.raise_for_status()
            points = points_response.json().get("properties", {})
            forecast_url = points.get("forecast")
            stations_url = points.get("observationStations")
            location = points.get("relativeLocation", {}).get("properties", {})
            place = ", ".join(
                part
                for part in [str(location.get("city") or "").strip(), str(location.get("state") or "").strip()]
                if part
            )

            current_text = ""
            if stations_url:
                stations_response = await client.get(stations_url)
                stations_response.raise_for_status()
                stations = stations_response.json().get("features", [])
                station_id = ""
                if stations:
                    station_id = str(stations[0].get("properties", {}).get("stationIdentifier") or "")
                if station_id:
                    obs_response = await client.get(f"https://api.weather.gov/stations/{station_id}/observations/latest")
                    if obs_response.status_code == 200:
                        obs = obs_response.json().get("properties", {})
                        description = " ".join(str(obs.get("textDescription") or "").split())
                        temp_c = obs.get("temperature", {}).get("value")
                        temp_text = ""
                        if isinstance(temp_c, (int, float)):
                            temp_text = f"{round(temp_c * 9 / 5 + 32)}F"
                        current_parts = [part for part in [description, temp_text] if part]
                        if current_parts:
                            current_text = ", ".join(current_parts)

            forecast_text = ""
            if forecast_url:
                forecast_response = await client.get(str(forecast_url))
                forecast_response.raise_for_status()
                periods = forecast_response.json().get("properties", {}).get("periods", [])
                forecast_parts = [format_weather_period(period) for period in periods[:4]]
                forecast_text = " | ".join(part for part in forecast_parts if part)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Weather service failed: {exc}") from exc

    summary_parts = []
    if place:
        summary_parts.append(f"Location: {place}")
    if current_text:
        summary_parts.append(f"Current: {current_text}")
    if forecast_text:
        summary_parts.append(f"Forecast: {forecast_text}")
    summary = ". ".join(summary_parts)
    if not summary:
        summary = "Weather unavailable for this location."
    logger.info("weather lat=%.4f lon=%.4f summary=%r", lat, lon, log_excerpt(summary))
    return {"summary": summary[:1200], "location": place}


@app.get("/api/weather/lookup")
async def weather_lookup(location: str) -> dict[str, str]:
    try:
        return await open_meteo_weather_lookup(location)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Weather lookup failed: {exc}") from exc


@app.post("/api/weather/speak")
async def weather_speak(request: WeatherSpeakRequest) -> WeatherSpeakResponse:
    started_at = time.perf_counter()
    weather_summary = " ".join((request.weather_summary or "").split())[:1200]
    location = " ".join((request.location or "").split())[:160]
    user_text = " ".join((request.user_text or "").split())[:300]
    if not weather_summary:
        raise HTTPException(status_code=400, detail="Weather summary is required")

    system_guidance = (request.system_prompt or SYSTEM_PROMPT).strip()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a local voice assistant. Turn the supplied weather tool data into a natural spoken answer. "
                "Use only the supplied weather data; do not add or guess facts. "
                "Keep it to one or two short conversational sentences. "
                "Mention the current condition and the most useful forecast detail. "
                "Do not read labels like Location, Current, or Forecast verbatim. Do not use emojis."
            ),
        },
        {
            "role": "system",
            "content": f"PERSONALITY_AND_STYLE: {system_guidance}",
        },
        {
            "role": "system",
            "content": f"CURRENT_LOCAL_DATETIME: {current_datetime_context()}",
        },
        {
            "role": "user",
            "content": (
                f"User request: {user_text or 'Weather request'}\n"
                f"Requested location: {location or 'unknown'}\n"
                f"Weather tool data: {weather_summary}"
            ),
        },
    ]
    try:
        content, _raw = await llm_chat_once(
            messages,
            temperature=max(0.0, min(1.0, request.temperature)),
            max_tokens=90,
            json_mode=False,
            timeout=20,
            num_ctx=1024,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Weather speech failed: {exc}") from exc

    spoken = " ".join(content.split()).strip().strip('"')
    if not spoken:
        spoken = weather_summary
    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "weather_speak location=%r seconds=%.3f spoken=%r",
        log_excerpt(location, 80),
        elapsed,
        log_excerpt(spoken, 180),
    )
    return WeatherSpeakResponse(
        spoken=spoken[:400],
        raw_response=content[:500],
        seconds=elapsed,
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    if LLM_PROVIDER == "llama":
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{LLAMA_BASE_URL}/health")
                response.raise_for_status()
                models_response = await client.get(f"{LLAMA_BASE_URL}/v1/models")
                models_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"llama-server is not reachable: {exc}") from exc

        model_ids = {
            item.get("id") or item.get("model") or item.get("name")
            for item in models_response.json().get("data", [])
        }
        return {
            "ok": True,
            "llmProvider": LLM_PROVIDER,
            "llmModel": LLAMA_MODEL,
            "ollamaModel": LLAMA_MODEL,
            "modelAvailable": not model_ids or LLAMA_MODEL in model_ids,
        }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Ollama is not reachable: {exc}") from exc

    models = response.json().get("models", [])
    model_names = {model.get("name") for model in models}
    return {
        "ok": True,
        "llmProvider": LLM_PROVIDER,
        "llmModel": OLLAMA_MODEL,
        "ollamaModel": OLLAMA_MODEL,
        "modelAvailable": OLLAMA_MODEL in model_names,
    }


@app.post("/api/ollama/restart")
async def restart_ollama() -> OllamaRestartResponse:
    if LLM_PROVIDER == "llama":
        stop_script = BASE_DIR.parent / "stop-llama-server.cmd"
        start_script = BASE_DIR.parent / "start-llama-server.cmd"
        if not start_script.exists():
            raise HTTPException(status_code=500, detail=f"llama-server start script not found: {start_script}")

        subprocess.run(["cmd", "/c", str(stop_script)], capture_output=True, text=True, check=False)
        started = subprocess.run(["cmd", "/c", str(start_script)], capture_output=True, text=True, check=False)
        if started.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to start llama-server: {started.stderr or started.stdout}")

        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(f"{LLAMA_BASE_URL}/health")
                    if response.status_code == 200:
                        await warm_ollama_model("restart")
                        logger.info("llama-server restart complete")
                        return OllamaRestartResponse(ok=True, message="llama-server restarted.")
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)

        return OllamaRestartResponse(ok=True, message="llama-server restart requested; waiting for service to come back.")

    ollama_path = Path(OLLAMA_BINARY)
    if not ollama_path.exists():
        raise HTTPException(status_code=500, detail=f"Ollama binary not found at {ollama_path}")

    subprocess.run(
        ["taskkill", "/im", "ollama.exe", "/f"],
        capture_output=True,
        text=True,
        check=False,
    )

    try:
        subprocess.Popen(
            [str(ollama_path)],
            cwd=str(ollama_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start Ollama: {exc}") from exc

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{OLLAMA_URL}/api/tags")
                if response.status_code == 200:
                    await warm_ollama_model("restart")
                    logger.info("ollama restart complete path=%s", ollama_path)
                    return OllamaRestartResponse(ok=True, message="Ollama restarted.")
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)

    return OllamaRestartResponse(ok=True, message="Ollama restart requested; waiting for service to come back.")


@app.websocket("/api/wake/listen")
async def wake_listen(websocket: WebSocket) -> None:
    await websocket.accept()
    sensitivity = float(websocket.query_params.get("threshold", WAKE_SENSITIVITY))
    ambient_context_seconds = max(
        0.5,
        float(websocket.query_params.get("ambientContextSeconds", WAKE_AMBIENT_CONTEXT_SECONDS)),
    )
    ambient_pretrigger_samples = max(1, int(16000 * WAKE_AMBIENT_PRETRIGGER_SECONDS))
    ambient_max_samples = max(ambient_pretrigger_samples, int(16000 * WAKE_AMBIENT_MAX_SECONDS))
    energy_threshold = wake_sensitivity_to_energy_threshold(sensitivity)
    chunk_samples = max(1, int(16000 * WAKE_CHUNK_SECONDS))
    hop_samples = max(1, int(16000 * (WAKE_CHUNK_SECONDS - WAKE_OVERLAP_SECONDS)))
    audio_buffer = np.empty(0, dtype=np.float32)
    recent_audio_buffer = np.empty(0, dtype=np.float32)
    ambient_context_audio = np.empty(0, dtype=np.float32)
    ambient_context_active = False
    last_detection_at = 0.0
    pending_wake: dict[str, Any] | None = None
    pending_wake_at = 0.0
    frames_seen = 0
    last_ambient_at = 0.0
    last_ambient_transcript = ""

    async def send_wake_event(wake: dict[str, Any]) -> None:
        nonlocal last_detection_at
        last_detection_at = time.monotonic()
        logger.info(
            "wake_detect phrase=%r command=%r transcript=%r delayed=%s",
            wake["phrase"],
            log_excerpt(wake["command_text"]),
            log_excerpt(wake["transcript"]),
            wake.get("delayed", False),
        )
        await websocket.send_json(
            {
                "type": "wake",
                "keyword": wake["phrase"],
                "score": wake["rms"],
                "transcript": wake["transcript"],
                "command": wake["command_text"],
            }
        )

    async def send_ambient_context(now: float, rms: float) -> None:
        nonlocal last_ambient_at, last_ambient_transcript
        if now - last_ambient_at < ambient_context_seconds:
            return
        context_audio = ambient_context_audio.copy()
        if context_audio.size < 16000:
            return
        last_ambient_at = now

        started = time.perf_counter()
        transcript, confidence = await asyncio.to_thread(
            transcribe_ambient_context_samples,
            context_audio,
        )
        elapsed = time.perf_counter() - started
        if not ambient_conversation_candidate(transcript):
            return

        normalized = " ".join(transcript.split())
        if normalized == last_ambient_transcript:
            return

        last_ambient_transcript = normalized
        await websocket.send_json(
            {
                "type": "ambient_conversation",
                "transcript": normalized,
                "cumulative": True,
                "score": rms,
                "confidence": confidence,
            }
        )
        logger.info(
            "ambient_conversation cumulative=true audio_seconds=%.1f transcript=%r rms=%.4f confidence=%s seconds=%.2f",
            context_audio.size / 16000,
            log_excerpt(normalized),
            rms,
            f"{confidence:.3f}" if confidence is not None else "unknown",
            elapsed,
        )

    try:
        await websocket.send_json(
            {
                "type": "ready",
                "keyword": WAKE_WORD,
                "phrases": list(WAKE_PHRASES),
                "detector": "faster-whisper",
                "sensitivity": sensitivity,
                "energyThreshold": energy_threshold,
            }
        )

        while True:
            payload = await websocket.receive_bytes()
            if not payload:
                continue

            audio = np.frombuffer(payload, dtype=np.int16)
            if audio.size == 0:
                continue

            samples = audio.astype(np.float32) / 32768.0
            audio_buffer = np.concatenate((audio_buffer, samples))
            recent_audio_buffer = np.concatenate((recent_audio_buffer, samples))
            if recent_audio_buffer.size > ambient_pretrigger_samples:
                recent_audio_buffer = recent_audio_buffer[-ambient_pretrigger_samples:]
            if ambient_context_active:
                ambient_context_audio = np.concatenate((ambient_context_audio, samples))
                if ambient_context_audio.size > ambient_max_samples:
                    ambient_context_audio = ambient_context_audio[-ambient_max_samples:]
            frames_seen += 1

            while audio_buffer.size >= chunk_samples:
                chunk = audio_buffer[:chunk_samples].copy()
                audio_buffer = audio_buffer[hop_samples:]
                rms = audio_rms(chunk)
                now = time.monotonic()

                if pending_wake is not None and now - pending_wake_at >= WAKE_COMMAND_GRACE_SECONDS:
                    pending_command = str(pending_wake.get("command_text", ""))
                    if wake_command_is_incomplete(pending_command):
                        started = float(pending_wake.get("started_at", pending_wake_at))
                        if now - started >= WAKE_INCOMPLETE_COMMAND_MAX_SECONDS:
                            logger.info(
                                "wake_detect dropped incomplete command=%r transcript=%r",
                                log_excerpt(pending_command),
                                log_excerpt(str(pending_wake.get("transcript", ""))),
                            )
                            pending_wake = None
                        continue
                    pending_wake["delayed"] = True
                    await send_wake_event(pending_wake)
                    pending_wake = None

                if frames_seen % 10 == 0:
                    await websocket.send_json({"type": "score", "score": rms})

                if rms < energy_threshold:
                    if ambient_context_active:
                        await send_ambient_context(now, rms)
                    continue

                started_at = time.perf_counter()
                transcript, confidence = await asyncio.to_thread(transcribe_wake_samples, chunk)
                elapsed = time.perf_counter() - started_at
                logger.info(
                    "wake_transcribe text=%r rms=%.4f confidence=%s seconds=%.2f",
                    log_excerpt(transcript),
                    rms,
                    f"{confidence:.3f}" if confidence is not None else "unknown",
                    elapsed,
                )

                detection = detect_wake_phrase(transcript)
                if detection is None:
                    if (
                        pending_wake is None
                        and ambient_conversation_candidate(transcript)
                    ):
                        if not ambient_context_active:
                            ambient_context_active = True
                            ambient_context_audio = recent_audio_buffer.copy()
                        logger.info(
                            "ambient_conversation_trigger transcript=%r rms=%.4f confidence=%s",
                            log_excerpt(transcript),
                            rms,
                            f"{confidence:.3f}" if confidence is not None else "unknown",
                        )
                        await send_ambient_context(now, rms)
                    if pending_wake is not None:
                        continuation = transcript.strip(" ,.!?:;-")
                        if continuation:
                            pending_wake = {
                                "phrase": str(pending_wake.get("phrase", WAKE_WORD)),
                                "command_text": continuation,
                                "transcript": f"{pending_wake.get('transcript', '')} {transcript}".strip(),
                                "rms": max(float(pending_wake.get("rms", 0.0)), rms),
                                "started_at": pending_wake.get("started_at", now),
                            }
                            pending_wake_at = now
                            logger.info(
                                "wake_detect pending continuation command=%r transcript=%r",
                                log_excerpt(continuation),
                                log_excerpt(str(pending_wake.get("transcript", ""))),
                            )
                            if not wake_command_should_wait_for_continuation(continuation):
                                await send_wake_event(pending_wake)
                                pending_wake = None
                    continue

                if now - last_detection_at < WAKE_COOLDOWN_SECONDS:
                    logger.info("wake_detect ignored cooldown transcript=%r", log_excerpt(transcript))
                    continue

                phrase, command_text = detection
                wake = {
                    "phrase": phrase,
                    "command_text": command_text,
                    "transcript": transcript,
                    "rms": rms,
                    "started_at": now,
                }
                if pending_wake is not None:
                    pending_command = str(pending_wake.get("command_text", ""))
                    if len(command_text) > len(pending_command):
                        wake["started_at"] = pending_wake.get("started_at", now)
                        pending_wake = wake
                        pending_wake_at = now
                        logger.info(
                            "wake_detect pending updated command=%r transcript=%r",
                            log_excerpt(command_text),
                            log_excerpt(transcript),
                        )
                    if not wake_command_should_wait_for_continuation(command_text):
                        await send_wake_event(pending_wake)
                        pending_wake = None
                    continue

                if wake_command_should_wait_for_continuation(command_text):
                    pending_wake = wake
                    pending_wake_at = now
                    logger.info(
                        "wake_detect pending command=%r transcript=%r",
                        log_excerpt(command_text),
                        log_excerpt(transcript),
                    )
                    continue

                await send_wake_event(wake)
    except WebSocketDisconnect:
        return


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...), mode: str = "fast") -> dict[str, str]:
    suffix = Path(audio.filename or "").suffix or ".webm"
    temp_path = Path(tempfile.gettempdir()) / f"voice-chat-{uuid.uuid4().hex}{suffix}"
    started_at = time.perf_counter()
    model_name = WHISPER_FAST_MODEL if mode == "fast" else WHISPER_MODEL
    beam_size = WHISPER_BEAM_SIZE if mode == "fast" else max(WHISPER_BEAM_SIZE, 5)

    try:
        audio_bytes = await audio.read()
        temp_path.write_bytes(audio_bytes)
        saved_at = time.perf_counter()
        model = get_whisper_model(model_name)
        loaded_at = time.perf_counter()
        segments, info = model.transcribe(
            str(temp_path),
            beam_size=beam_size,
            vad_filter=True,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        finished_at = time.perf_counter()
        logger.info(
            "transcribe mode=%s model=%s beam=%s upload_bytes=%s save=%.2fs load=%.2fs transcribe=%.2fs total=%.2fs text_chars=%s text=%r",
            mode,
            model_name,
            beam_size,
            len(audio_bytes),
            saved_at - started_at,
            loaded_at - saved_at,
            finished_at - loaded_at,
            finished_at - started_at,
            len(text),
            log_excerpt(text),
        )
        return {
            "text": text,
            "language": info.language or "",
            "languageProbability": f"{info.language_probability:.3f}",
            "mode": mode,
            "model": model_name,
            "beamSize": str(beam_size),
            "uploadBytes": str(len(audio_bytes)),
            "saveSeconds": f"{saved_at - started_at:.2f}",
            "loadSeconds": f"{loaded_at - saved_at:.2f}",
            "transcribeSeconds": f"{finished_at - loaded_at:.2f}",
            "totalSeconds": f"{finished_at - started_at:.2f}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/intent")
async def classify_intent(request: IntentRequest) -> IntentResponse:
    started_at = time.perf_counter()
    text = request.text.strip()
    if not text:
        return IntentResponse(intent="continue", confidence=0.0, reason="empty input")

    messages = [
        {
            "role": "system",
            "content": (
                "Classify whether the user wants to end the current voice conversation and return "
                "to wake-word standby. Return only JSON with keys intent, confidence, and reason. "
                "intent must be one of: end_conversation, continue. "
                "Choose end_conversation for broad phrases like stop listening, that's all, "
                "we're done, never mind, go back to standby, stop the conversation, no more questions, "
                "enough for now, or similar. Choose continue for normal questions, requests, dictation, "
                "or discussion about stopping that is not a command to stop this assistant."
            ),
        },
        {"role": "user", "content": text},
    ]
    try:
        content, _raw = await llm_chat_once(
            messages,
            temperature=0,
            max_tokens=120,
            json_mode=True,
            timeout=20,
            num_ctx=1024,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Intent classification failed: {exc}") from exc

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return IntentResponse(intent="continue", confidence=0.0, reason="invalid classifier JSON")

    intent = data.get("intent", "continue")
    if intent not in {"end_conversation", "continue"}:
        intent = "continue"

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence <= 0 and intent in {
        "start_timer",
        "ask_timer_duration",
        "cancel_timer",
        "cancel_all_timers",
        "end_conversation",
    }:
        confidence = 0.9

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "intent text_chars=%s text=%r intent=%s confidence=%.2f seconds=%.3f reason=%r",
        len(text),
        log_excerpt(text),
        intent,
        confidence,
        elapsed,
        log_excerpt(str(data.get("reason", "")), 120),
    )

    return IntentResponse(
        intent=intent,
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(data.get("reason", ""))[:200],
        seconds=elapsed,
    )


@app.post("/api/timer/parse")
async def parse_timer(request: TimerParseRequest) -> TimerParseResponse:
    started_at = time.perf_counter()
    text = request.text.strip()
    if not text:
        return TimerParseResponse(action="continue", reason="empty input")

    context = request.context.strip()

    messages = [
        {
            "role": "system",
            "content": (
                "Return compact JSON only. Keys: action, kind, duration_ms, label, confidence. "
                "action is create_timer, ask_duration, or continue. kind is timer or reminder. "
                "duration_ms is integer milliseconds. label is a short label or empty. "
                "Use create_timer only with a clear duration. Use ask_duration when the user wants "
                "a timer/reminder but omitted the duration. Use continue when this is not a "
                "timer/reminder setup. Use CONTEXT to complete follow-up replies like '4 minutes'."
            ),
        },
        *(
            [
                {
                    "role": "system",
                    "content": f"CONTEXT: {context}",
                }
            ]
            if context
            else []
        ),
        {"role": "user", "content": text},
    ]
    try:
        content, _raw = await llm_chat_once(
            messages,
            temperature=0,
            max_tokens=80,
            json_mode=True,
            timeout=20,
            num_ctx=1024,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Timer parsing failed: {exc}") from exc

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return TimerParseResponse(action="continue")

    action = str(data.get("action", "continue"))
    if action not in {"create_timer", "ask_duration", "continue"}:
        action = "continue"

    kind = str(data.get("kind", "timer"))
    if kind not in {"timer", "reminder"}:
        kind = "timer"

    try:
        duration_ms = int(float(data.get("duration_ms", 0)))
    except (TypeError, ValueError):
        duration_ms = 0

    label = str(data.get("label", ""))[:120]
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "timer_parse text_chars=%s text=%r context_chars=%s context=%r action=%s kind=%s duration_ms=%s confidence=%.2f seconds=%.3f reason=%r",
        len(text),
        log_excerpt(text),
        len(context),
        log_excerpt(context),
        action,
        kind,
        duration_ms,
        confidence,
        elapsed,
        log_excerpt(str(data.get("reason", "")), 120),
    )

    if action != "create_timer" or duration_ms <= 0:
        return TimerParseResponse(
            action="ask_duration" if action == "ask_duration" else "continue",
            kind=kind,
            duration_ms=0,
            label=label,
            confidence=max(0.0, min(1.0, confidence)),
            seconds=elapsed,
        )

    return TimerParseResponse(
        action="create_timer",
        kind=kind,
        duration_ms=duration_ms,
        label=label,
        confidence=max(0.0, min(1.0, confidence)),
        seconds=elapsed,
    )


@app.post("/api/timer/cancel/parse")
async def parse_timer_cancel(request: TimerCancelParseRequest) -> TimerCancelParseResponse:
    started_at = time.perf_counter()
    text = request.text.strip()
    active_timers = request.active_timers[:20]
    if not text or not active_timers:
        return TimerCancelParseResponse(action="continue", reason="empty input or no active timers")

    messages = [
        {
            "role": "system",
            "content": (
                "Return compact JSON only. Keys: action, target_id, confidence, reason. "
                "The user may be asking to cancel, stop, delete, clear, dismiss, or remove a timer/reminder. "
                "action is cancel_timer, cancel_all_timers, ask_clarification, or continue. "
                "For cancel_timer, target_id must exactly match one id from ACTIVE_TIMERS. "
                "Use cancel_all_timers only when the user clearly asks to cancel every timer. "
                "Use ask_clarification when the user wants cancellation but the target is ambiguous. "
                "Use continue when this is not a timer/reminder cancellation request."
            ),
        },
        {
            "role": "system",
            "content": f"ACTIVE_TIMERS: {json.dumps(active_timers, ensure_ascii=True, separators=(',', ':'))}",
        },
        {"role": "user", "content": text},
    ]
    try:
        content, _raw = await llm_chat_once(
            messages,
            temperature=0,
            max_tokens=120,
            json_mode=True,
            timeout=20,
            num_ctx=1024,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Timer cancellation parsing failed: {exc}") from exc

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return TimerCancelParseResponse(action="continue", reason="invalid JSON")

    action = str(data.get("action", "continue"))
    if action not in {"cancel_timer", "cancel_all_timers", "ask_clarification", "continue"}:
        action = "continue"

    valid_ids = {str(timer.get("id", "")) for timer in active_timers}
    target_id = str(data.get("target_id", ""))
    if action == "cancel_timer" and target_id not in valid_ids:
        action = "ask_clarification"
        target_id = ""

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    elapsed = round(time.perf_counter() - started_at, 3)
    reason = str(data.get("reason", ""))[:200]
    logger.info(
        "timer_cancel_parse text_chars=%s text=%r active_timers=%s action=%s target_id=%r confidence=%.2f seconds=%.3f reason=%r",
        len(text),
        log_excerpt(text),
        len(active_timers),
        action,
        target_id,
        confidence,
        elapsed,
        log_excerpt(reason, 120),
    )

    return TimerCancelParseResponse(
        action=action,
        target_id=target_id,
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        seconds=elapsed,
    )


@app.post("/api/assistant/turn")
async def assistant_turn(request: AssistantTurnRequest) -> AssistantTurnResponse:
    started_at = time.perf_counter()
    text = request.text.strip()
    if not text:
        return AssistantTurnResponse(intent="noop", spoken="", reason="empty input")

    active_timers = request.active_timers[:20]
    valid_timer_ids = {str(timer.get("id", "")) for timer in active_timers if timer.get("id")}
    pending_timer_setup = request.pending_timer_setup or {}
    trimmed_messages = trim_chat_messages(request.messages)
    system_guidance = (request.system_prompt or SYSTEM_PROMPT).strip()
    weather_context = " ".join((request.weather_context or "").split())[:1200]
    wake_words = ", ".join(WAKE_PHRASES)

    assistant_schema = {
        "intent": "chat | start_timer | ask_timer_duration | cancel_timer | cancel_all_timers | get_weather | end_conversation",
        "timer_kind": "timer | reminder only for timer/reminder setup; otherwise empty string",
        "duration_value": "number from the user's spoken duration, or 0 when not applicable",
        "duration_unit": "seconds | minutes | hours, or empty string when not applicable",
        "label": "short timer/reminder label, or empty string",
        "target_timer_id": "exact id from ACTIVE_TIMERS for cancel_timer, otherwise empty string",
        "weather_location": "city/place name for get_weather, otherwise empty string",
        "spoken": "natural short reply to say to the user",
        "confidence": "number 0.0 to 1.0",
    }
    output_template = {
        "intent": "chat",
        "timer_kind": "",
        "duration_value": 0,
        "duration_unit": "",
        "label": "",
        "target_timer_id": "",
        "weather_location": "",
        "spoken": "",
        "confidence": 0.0,
    }
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Return exactly one compact JSON object and no other text. Do not return tool calls or markup. "
                f"Use this template: {json.dumps(output_template, ensure_ascii=True, separators=(',', ':'))}. "
                f"Field meanings: {json.dumps(assistant_schema, ensure_ascii=True, separators=(',', ':'))}. "
                "You are a local general-purpose voice assistant with timer/reminder and weather tools. "
                "Use chat for general user assistance. "
                "Use get_weather when the user asks for live weather, current conditions, humidity, temperature, high, low, or forecast. "
                "For get_weather, do not write weather conditions in spoken; use a short phrase like 'Checking weather for Chicago.' "
                "For local weather requests like here, outside, nearby, my location, or local follow-ups, use get_weather with the Location from CURRENT_LOCAL_WEATHER. "
                "For weather follow-ups like 'tomorrow', 'how about tomorrow', 'what is the high', or 'confirm the forecast', use get_weather with the last weather location or CURRENT_LOCAL_WEATHER location. "
                "Do not answer weather forecast details directly from CURRENT_LOCAL_WEATHER; route them through get_weather so the app can fetch and format the weather data. "
                "Use start_timer only when a timer/reminder duration is clear. Keep duration simple: use the user's number and unit. "
                "Any positive duration in seconds, minutes, or hours is valid, including short durations like 30 seconds. "
                "Timer labels are arbitrary user words. Do not refuse a timer because the label names an object, topic, or activity. "
                "Use ask_timer_duration when a timer/reminder is requested without duration. "
                "Use timer_kind only for start_timer or ask_timer_duration; use an empty timer_kind for chat, cancellation, and end_conversation. "
                "Use PENDING_TIMER_SETUP to complete follow-ups like '4 minutes'. "
                "Use cancel_timer only when cancelling one active timer; target_timer_id must match ACTIVE_TIMERS. "
                "Use cancel_all_timers only for all timers. Cancel words include cancel, stop, clear, remove, delete, and turn off. "
                "Use end_conversation for stop listening. "
                f"Your name is {WAKE_WORD.title()}. If the user says {WAKE_WORD.title()!r} at the start of the request, "
                "treat it as them addressing you. Use the clearest complete request that follows. "
                "Voice recognition hints: council/counsel/castle/counted can mean cancel; time can mean timer; cake may sound like cape; "
                "Alzheimer's, Alzheimers, or volume Alzheimer's can mean all timers when the user is cancelling or turning off timers. "
                "Examples: 'set a cookie timer for three minutes' -> start_timer, timer_kind timer, duration_value 3, duration_unit minutes, label cookie. "
                "'set a timer for 30 seconds called anything' -> start_timer, timer_kind timer, duration_value 30, duration_unit seconds, label anything. "
                "'what is the weather in Chicago?' -> get_weather, weather_location Chicago. "
                "'what is tomorrow's high here?' -> get_weather, weather_location from CURRENT_LOCAL_WEATHER Location. "
                "'turn off Alzheimer's' with active timers -> cancel_all_timers. "
                "'what is the square root?' -> chat, timer_kind empty, spoken 'Of what number?'."
            ),
        },
        {
            "role": "system",
            "content": f"WAKE_WORDS: {json.dumps(list(WAKE_PHRASES), ensure_ascii=True)}",
        },
        {
            "role": "system",
            "content": f"PERSONALITY_AND_STYLE: {system_guidance}",
        },
        {
            "role": "system",
            "content": f"CURRENT_LOCAL_DATETIME: {current_datetime_context()}",
        },
        *(
            [
                {
                    "role": "system",
                    "content": f"CURRENT_LOCAL_WEATHER: {weather_context}",
                }
            ]
            if weather_context
            else []
        ),
        {
            "role": "system",
            "content": f"ACTIVE_TIMERS: {json.dumps(active_timers, ensure_ascii=True, separators=(',', ':'))}",
        },
        {
            "role": "system",
            "content": f"PENDING_TIMER_SETUP: {json.dumps(pending_timer_setup, ensure_ascii=True, separators=(',', ':'))}",
        },
    ]
    for message in trimmed_messages:
        role = message.role if message.role in {"user", "assistant"} else "user"
        messages.append({"role": role, "content": message.content})
    messages.append({"role": "user", "content": text})

    data: dict[str, Any] | None = None
    content = ""
    for attempt in range(2):
        try:
            content, _raw = await llm_chat_once(
                messages,
                temperature=0,
                max_tokens=120,
                json_mode=False,
                timeout=20,
                num_ctx=2048,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"Assistant turn failed: {exc}") from exc

        data, recovered = parse_assistant_turn_data(content)
        if data is not None:
            if recovered:
                logger.warning(
                    "assistant_turn recovered_partial_json attempt=%s content=%r",
                    attempt + 1,
                    log_excerpt(content, 500),
                )
            elif attempt:
                logger.info("assistant_turn json_retry succeeded attempt=%s", attempt + 1)
            break
        data = recover_tool_call_assistant_turn(content, text)
        if data is not None:
            logger.warning(
                "assistant_turn recovered_tool_call attempt=%s content=%r",
                attempt + 1,
                log_excerpt(content, 500),
            )
            break
        data = recover_plain_assistant_turn(content, text)
        if data is not None:
            logger.warning(
                "assistant_turn recovered_plain attempt=%s content=%r",
                attempt + 1,
                log_excerpt(content, 500),
            )
            break
        logger.warning(
            "assistant_turn invalid_json attempt=%s content=%r",
            attempt + 1,
            log_excerpt(content, 500),
        )

    if data is None:
        data = {
            "intent": "chat",
            "timer_kind": "",
            "duration_value": 0,
            "duration_unit": "",
            "label": "",
            "target_timer_id": "",
            "spoken": "Sorry, I had trouble deciding what to do. Could you say that again?",
            "confidence": 0,
            "reason": "invalid JSON",
        }

    intent = str(data.get("intent", "chat"))
    valid_intents = {
        "chat",
        "start_timer",
        "ask_timer_duration",
            "cancel_timer",
            "cancel_all_timers",
            "get_weather",
            "end_conversation",
            "noop",
        }
    if intent not in valid_intents:
        intent = "chat"

    kind = str(data.get("timer_kind", data.get("kind", "")) or "").strip()
    if kind not in {"timer", "reminder"}:
        kind = "timer" if intent in {"start_timer", "ask_timer_duration"} else ""

    duration_ms = duration_fields_to_ms(data)

    target_timer_id = str(data.get("target_timer_id", "") or "")
    if intent == "cancel_timer" and target_timer_id not in valid_timer_ids:
        intent = "chat"
        target_timer_id = ""
        data["spoken"] = "Which timer should I cancel?"
        data["reason"] = "invalid or ambiguous timer id"

    if intent == "start_timer" and duration_ms <= 0:
        intent = "ask_timer_duration"
        duration_ms = 0
        if not str(data.get("spoken", "")).strip():
            data["spoken"] = "For how long?"

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    spoken = str(data.get("spoken", "") or "").strip()
    if not spoken:
        spoken = "Okay." if intent != "chat" else "I'm not sure how to respond to that."

    label = str(data.get("label", "") or "").strip()[:120]
    weather_location = str(data.get("weather_location", "") or "").strip()[:160]
    recovered_weather_location = weather_location_from_checking_spoken(spoken)
    if intent == "chat" and recovered_weather_location:
        intent = "get_weather"
        weather_location = recovered_weather_location
        reason = str(data.get("reason", "") or "")
        data["reason"] = (
            f"{reason}; recovered plain weather tool request".strip("; ")
        )
    elif intent == "chat" and weather_location:
        intent = "get_weather"
        data["reason"] = (
            f"{str(data.get('reason', '') or '')}; recovered weather location".strip("; ")
        )
    if intent == "get_weather" and not weather_location:
        weather_location = location_from_weather_context(weather_context)
        if weather_location:
            data["reason"] = (
                f"{str(data.get('reason', '') or '')}; recovered local weather location".strip("; ")
            )
        else:
            intent = "chat"
            if not str(data.get("spoken", "")).strip():
                data["spoken"] = "Which city should I check?"
    pending_label = str(pending_timer_setup.get("label", "") or "").strip()[:120]
    if intent == "start_timer" and pending_timer_setup:
        label_looks_like_duration = bool(
            re.search(r"\b(second|seconds|minute|minutes|hour|hours)\b", label, flags=re.IGNORECASE)
        )
        if pending_label and (not label or label_looks_like_duration):
            label = pending_label
        elif not pending_label and label_looks_like_duration:
            label = ""
    reason = str(data.get("reason", "") or "")[:200]
    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "assistant_turn text_chars=%s text=%r timers=%s pending=%s intent=%s kind=%s duration_ms=%s label=%r target_timer_id=%r weather_location=%r confidence=%.2f seconds=%.3f spoken=%r reason=%r",
        len(text),
        log_excerpt(text),
        len(active_timers),
        bool(pending_timer_setup),
        intent,
        kind,
        duration_ms,
        log_excerpt(label, 80),
        target_timer_id,
        log_excerpt(weather_location, 80),
        confidence,
        elapsed,
        log_excerpt(spoken, 160),
        log_excerpt(reason, 120),
    )

    return AssistantTurnResponse(
        intent=intent,
        kind=kind,
        duration_ms=duration_ms,
        label=label,
        target_timer_id=target_timer_id,
        weather_location=weather_location,
        spoken=spoken,
        raw_response=content[:500],
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        seconds=elapsed,
    )


@app.post("/api/client-log")
async def client_log(request: ClientLogRequest) -> dict[str, str]:
    logger.info(
        "client_log event=%s decision=%s text=%r detail=%r",
        request.event,
        request.decision,
        log_excerpt(request.text),
        log_excerpt(request.detail),
    )
    return {"status": "ok"}


@app.post("/api/proactive/open")
async def proactive_open(request: ProactiveOpenRequest) -> ProactiveOpenResponse:
    started_at = time.perf_counter()
    context = " ".join((request.context or "").split())[:800]
    weather_context = " ".join((request.weather_context or "").split())[:1200]
    system_guidance = (request.system_prompt or SYSTEM_PROMPT).strip()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a local voice assistant starting a conversation with the user. "
                "Return only the exact words to speak. Keep it to one short sentence. "
                "If the overheard context is clear, ask a friendly follow-up related to it. "
                "If the context is unclear, choose a broadly useful or interesting topic. "
                "Do not mention that you were passively listening. Do not use emojis."
            ),
        },
        {
            "role": "system",
            "content": f"PERSONALITY_AND_STYLE: {system_guidance}",
        },
        {
            "role": "system",
            "content": f"CURRENT_LOCAL_DATETIME: {current_datetime_context()}",
        },
        *(
            [
                {
                    "role": "system",
                    "content": f"CURRENT_LOCAL_WEATHER: {weather_context}",
                }
            ]
            if weather_context
            else []
        ),
        {
            "role": "user",
            "content": (
                f"Overheard conversation context: {context}"
                if context
                else "No clear overheard context."
            ),
        },
    ]
    try:
        content, _raw = await llm_chat_once(
            messages,
            temperature=request.temperature,
            max_tokens=60,
            json_mode=False,
            timeout=20,
            num_ctx=1024,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Proactive opener failed: {exc}") from exc

    spoken = " ".join(content.split()).strip().strip('"')
    if not spoken:
        spoken = "Anything interesting you want to talk about?"
    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "proactive_open context_chars=%s context=%r seconds=%.3f spoken=%r",
        len(context),
        log_excerpt(context),
        elapsed,
        log_excerpt(spoken, 160),
    )
    return ProactiveOpenResponse(
        spoken=spoken[:300],
        raw_response=content[:500],
        seconds=elapsed,
    )


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    request_started_at = time.perf_counter()
    system_prompt = (request.system_prompt or SYSTEM_PROMPT).strip()
    weather_context = " ".join((request.weather_context or "").split())[:1200]
    trimmed_messages = trim_chat_messages(request.messages)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"CURRENT_LOCAL_DATETIME: {current_datetime_context()}"},
        *(
            [{"role": "system", "content": f"CURRENT_LOCAL_WEATHER: {weather_context}"}]
            if weather_context
            else []
        ),
    ]
    messages.extend(message.model_dump() for message in trimmed_messages)

    async def stream_response():
        first_token_at: float | None = None
        output_chars = 0
        assistant_text_parts: list[str] = []

        def event(data: dict[str, Any]) -> str:
            return json.dumps(data, separators=(",", ":")) + "\n"

        def seconds_from_ns(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                return float(value) / 1_000_000_000
            except (TypeError, ValueError):
                return None

        try:
            logger.info(
                "chat start provider=%s model=%s input_messages=%s kept_messages=%s kept_chars=%s max_messages=%s max_chars=%s num_ctx=%s temperature=%.2f user=%r",
                LLM_PROVIDER,
                active_model_name(),
                max(0, len(request.messages)),
                len(trimmed_messages),
                sum(len(message.content or "") for message in trimmed_messages),
                MAX_CHAT_MESSAGES,
                MAX_CHAT_CHARS,
                OLLAMA_NUM_CTX,
                request.temperature,
                log_excerpt(trimmed_messages[-1].content if trimmed_messages else ""),
            )

            if LLM_PROVIDER == "llama":
                payload = {
                    "model": LLAMA_MODEL,
                    "messages": messages,
                    "stream": True,
                    "temperature": request.temperature,
                    "max_tokens": 512,
                }
                for attempt in range(2):
                    try:
                        async with httpx.AsyncClient(timeout=None) as client:
                            async with client.stream("POST", f"{LLAMA_BASE_URL}/v1/chat/completions", json=payload) as response:
                                response.raise_for_status()
                                async for line in response.aiter_lines():
                                    if not line:
                                        continue
                                    if line.startswith("data: "):
                                        line = line[6:]
                                    if line.strip() == "[DONE]":
                                        break

                                    data = json.loads(line)
                                    choices = data.get("choices") or []
                                    if not choices:
                                        continue
                                    delta = choices[0].get("delta") or {}
                                    content = delta.get("content", "")
                                    if content:
                                        now = time.perf_counter()
                                        if first_token_at is None:
                                            first_token_at = now
                                            first_token_seconds = first_token_at - request_started_at
                                            logger.info(
                                                "chat first_token=%.2fs provider=%s model=%s input_messages=%s kept_messages=%s kept_chars=%s max_messages=%s max_chars=%s",
                                                first_token_seconds,
                                                LLM_PROVIDER,
                                                LLAMA_MODEL,
                                                max(0, len(request.messages)),
                                                len(trimmed_messages),
                                                sum(len(message.content or "") for message in trimmed_messages),
                                                MAX_CHAT_MESSAGES,
                                                MAX_CHAT_CHARS,
                                            )
                                            yield event(
                                                {
                                                    "type": "timing",
                                                    "phase": "first_token",
                                                    "seconds": round(first_token_seconds, 3),
                                                }
                                            )
                                        assistant_text_parts.append(content)
                                        output_chars += len(content)
                                        yield event({"type": "token", "content": content})
                        break
                    except httpx.HTTPError as exc:
                        if attempt == 0 and first_token_at is None and output_chars == 0:
                            logger.warning("chat stream failed before first token; attempting llama-server recovery error=%s", exc)
                            if await ensure_llama_server_ready("chat_stream_retry"):
                                continue
                        raise

                total_seconds = time.perf_counter() - request_started_at
                logger.info(
                    "chat complete=%.2fs first_token=%.2fs output_chars=%s provider=%s model=%s kept_messages=%s kept_chars=%s assistant=%r",
                    total_seconds,
                    (first_token_at - request_started_at) if first_token_at else -1,
                    output_chars,
                    LLM_PROVIDER,
                    LLAMA_MODEL,
                    len(trimmed_messages),
                    sum(len(message.content or "") for message in trimmed_messages),
                    log_excerpt("".join(assistant_text_parts)),
                )
                yield event(
                    {
                        "type": "timing",
                        "phase": "complete",
                        "seconds": round(total_seconds, 3),
                        "outputChars": output_chars,
                        "llmProvider": LLM_PROVIDER,
                    }
                )
                return

            payload = {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": True,
                "think": request.think,
                "keep_alive": OLLAMA_KEEP_ALIVE_VALUE,
                "options": {
                    "temperature": request.temperature,
                    "top_p": 0.95,
                    "top_k": 64,
                    "num_ctx": OLLAMA_NUM_CTX,
                },
            }
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        content = data.get("message", {}).get("content", "")
                        if content:
                            now = time.perf_counter()
                            if first_token_at is None:
                                first_token_at = now
                                first_token_seconds = first_token_at - request_started_at
                                logger.info(
                                    "chat first_token=%.2fs provider=%s model=%s input_messages=%s kept_messages=%s kept_chars=%s max_messages=%s max_chars=%s",
                                    first_token_seconds,
                                    LLM_PROVIDER,
                                    OLLAMA_MODEL,
                                    max(0, len(request.messages)),
                                    len(trimmed_messages),
                                    sum(len(message.content or "") for message in trimmed_messages),
                                    MAX_CHAT_MESSAGES,
                                    MAX_CHAT_CHARS,
                                )
                                yield event(
                                    {
                                        "type": "timing",
                                        "phase": "first_token",
                                        "seconds": round(first_token_seconds, 3),
                                    }
                                )
                            assistant_text_parts.append(content)
                            output_chars += len(content)
                            yield event({"type": "token", "content": content})
                        if data.get("done"):
                            total_seconds = time.perf_counter() - request_started_at
                            ollama_load_seconds = seconds_from_ns(data.get("load_duration"))
                            ollama_prompt_eval_seconds = seconds_from_ns(data.get("prompt_eval_duration"))
                            ollama_eval_seconds = seconds_from_ns(data.get("eval_duration"))
                            ollama_total_seconds = seconds_from_ns(data.get("total_duration"))
                            logger.info(
                                "chat complete=%.2fs first_token=%.2fs output_chars=%s provider=%s model=%s kept_messages=%s kept_chars=%s ollama_total=%.2fs ollama_load=%.2fs ollama_prompt_eval=%.2fs ollama_eval=%.2fs eval_count=%s prompt_eval_count=%s assistant=%r",
                                total_seconds,
                                (first_token_at - request_started_at) if first_token_at else -1,
                                output_chars,
                                LLM_PROVIDER,
                                OLLAMA_MODEL,
                                len(trimmed_messages),
                                sum(len(message.content or "") for message in trimmed_messages),
                                ollama_total_seconds if ollama_total_seconds is not None else -1,
                                ollama_load_seconds if ollama_load_seconds is not None else -1,
                                ollama_prompt_eval_seconds if ollama_prompt_eval_seconds is not None else -1,
                                ollama_eval_seconds if ollama_eval_seconds is not None else -1,
                                data.get("eval_count", -1),
                                data.get("prompt_eval_count", -1),
                                log_excerpt("".join(assistant_text_parts)),
                            )
                            yield event(
                                {
                                    "type": "timing",
                                    "phase": "complete",
                                    "seconds": round(total_seconds, 3),
                                    "outputChars": output_chars,
                                    "ollamaTotalSeconds": round(ollama_total_seconds, 3) if ollama_total_seconds is not None else None,
                                    "ollamaLoadSeconds": round(ollama_load_seconds, 3) if ollama_load_seconds is not None else None,
                                    "ollamaPromptEvalSeconds": round(ollama_prompt_eval_seconds, 3) if ollama_prompt_eval_seconds is not None else None,
                                    "ollamaEvalSeconds": round(ollama_eval_seconds, 3) if ollama_eval_seconds is not None else None,
                                }
                            )
                            break
        except httpx.HTTPError as exc:
            yield event({"type": "error", "message": f"{LLM_PROVIDER} error: {exc}"})

    return StreamingResponse(stream_response(), media_type="application/x-ndjson")
