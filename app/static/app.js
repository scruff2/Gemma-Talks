const statusEl = document.querySelector("#status");
const chatEl = document.querySelector("#chat");
const rawResponseOutput = document.querySelector("#rawResponseOutput");
const speechIndicator = document.querySelector("#speechIndicator");
const speechIndicatorText = document.querySelector("#speechIndicatorText");
const proactiveNudgeText = document.querySelector("#proactiveNudgeText");
const proactiveContextPanel = document.querySelector("#proactiveContextPanel");
const proactiveContextText = document.querySelector("#proactiveContextText");
const recordButton = document.querySelector("#recordButton");
const recordIcon = document.querySelector("#recordIcon");
const promptInput = document.querySelector("#promptInput");
const sendButton = document.querySelector("#sendButton");
const clearButton = document.querySelector("#clearButton");
const speakToggle = document.querySelector("#speakToggle");
const settingsButton = document.querySelector("#settingsButton");
const settingsPanel = document.querySelector("#settingsPanel");
const timersStatus = document.querySelector("#timersStatus");
const timersList = document.querySelector("#timersList");
const clearTimersButton = document.querySelector("#clearTimersButton");
const micPauseButton = document.querySelector("#micPauseButton");
const stopButton = document.querySelector("#stopButton");
const restartOllamaButton = document.querySelector("#restartOllamaButton");
const autoStopToggle = document.querySelector("#autoStopToggle");
const silenceThresholdInput = document.querySelector("#silenceThresholdInput");
const silenceDelayInput = document.querySelector("#silenceDelayInput");
const activationModeInput = document.querySelector("#activationModeInput");
const wakeThresholdInput = document.querySelector("#wakeThresholdInput");
const conversationTimeoutInput = document.querySelector("#conversationTimeoutInput");
const transcriptionModeInput = document.querySelector("#transcriptionModeInput");
const temperatureInput = document.querySelector("#temperatureInput");
const voiceSelect = document.querySelector("#voiceSelect");
const systemPromptInput = document.querySelector("#systemPromptInput");
const resetPromptButton = document.querySelector("#resetPromptButton");

const defaultSettings = {
  autoStop: true,
  silenceThreshold: 4,
  silenceDelay: 1200,
  activationMode: "pushToTalk",
  wakeThreshold: 0.45,
  conversationTimeout: 45,
  transcriptionMode: "fast",
  temperature: 0.7,
  voiceURI: "",
  speak: true,
  systemPrompt:
    "You are a concise local voice assistant. Reply conversationally. Keep spoken answers short unless the user asks for detail. Do not use emojis, emoticons, or decorative symbols.",
};

const oldDefaultSystemPrompt =
  "You are a concise local voice assistant. Reply conversationally. Keep spoken answers short unless the user asks for detail.";

let settings = loadSettings();
let mediaRecorder = null;
let audioStream = null;
let audioContext = null;
let analyser = null;
let recordedChunks = [];
let recording = false;
let messages = [];
let chatAbortController = null;
let vadFrame = null;
let recordingStartedAt = 0;
let speechDetected = false;
let silentSince = null;
let micPaused = false;
let lastAssistantText = "";
let conversationActive = false;
let skipNextTranscription = false;
let speechCompletion = null;
let wakeSocket = null;
let wakeContext = null;
let wakeSource = null;
let wakeProcessor = null;
let wakeListening = false;
let wakeKeyword = "alexa";
let firstTokenWatchdog = null;
let timerAnnouncementActive = false;
let pendingTimerSetup = null;
let wakePrefillText = "";
let wakePrefillCommand = "";
let recordingNoSpeechTimeoutMs = 0;
let proactiveNudgeTimer = null;
let proactiveNudgeContext = "";
let proactiveNudgeDueAt = 0;
let proactiveNudgeDisplayTimer = null;
let speechIndicatorResetTimer = null;
let weatherContext = "";
let weatherLocation = "";
let weatherFetchedAt = 0;
let weatherRefreshPromise = null;
const weatherRefreshMs = 15 * 60 * 1000;
const timerStorageKey = "gemmaVoiceTimers";
let timers = loadTimers();
const timerTimeouts = new Map();

const activationModes = {
  pushToTalk: {
    start: () => startRecording(),
    stop: () => stopRecording(),
  },
  wakeWord: {
    start: () => startRecording(),
    stop: () => stopRecording(),
  },
};

function loadSettings() {
  try {
    const savedSettings = JSON.parse(localStorage.getItem("gemmaVoiceSettings") || "{}");
    if (savedSettings.systemPrompt === oldDefaultSystemPrompt) {
      savedSettings.systemPrompt = defaultSettings.systemPrompt;
      localStorage.setItem("gemmaVoiceSettings", JSON.stringify(savedSettings));
    }
    return { ...defaultSettings, ...savedSettings };
  } catch {
    return { ...defaultSettings };
  }
}

function saveSettings() {
  settings.autoStop = autoStopToggle.checked;
  settings.silenceThreshold = Number(silenceThresholdInput.value);
  settings.silenceDelay = Number(silenceDelayInput.value);
  settings.activationMode = activationModeInput.value;
  settings.wakeThreshold = Number(wakeThresholdInput.value);
  settings.conversationTimeout = Number(conversationTimeoutInput.value);
  settings.transcriptionMode = transcriptionModeInput.value;
  settings.temperature = Number(temperatureInput.value);
  settings.voiceURI = voiceSelect.value;
  settings.speak = speakToggle.checked;
  settings.systemPrompt = systemPromptInput.value.trim() || defaultSettings.systemPrompt;
  localStorage.setItem("gemmaVoiceSettings", JSON.stringify(settings));
}

function applySettings() {
  autoStopToggle.checked = settings.autoStop;
  silenceThresholdInput.value = settings.silenceThreshold;
  silenceDelayInput.value = String(settings.silenceDelay);
  activationModeInput.value = settings.activationMode;
  wakeThresholdInput.value = settings.wakeThreshold;
  conversationTimeoutInput.value = String(settings.conversationTimeout);
  transcriptionModeInput.value = settings.transcriptionMode;
  temperatureInput.value = settings.temperature;
  speakToggle.checked = settings.speak;
  systemPromptInput.value = settings.systemPrompt;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setSpeechIndicator(active, text = "") {
  if (!speechIndicator || !speechIndicatorText) {
    return;
  }
  if (speechIndicatorResetTimer) {
    clearTimeout(speechIndicatorResetTimer);
    speechIndicatorResetTimer = null;
  }
  speechIndicator.classList.toggle("active", Boolean(active));
  speechIndicator.classList.toggle("paused", Boolean(micPaused));
  if (micPaused) {
    speechIndicatorText.textContent = "Microphone paused";
    return;
  }
  speechIndicatorText.textContent = text || (active ? "Speech detected" : "No speech detected");
}

function pulseSpeechIndicator(text = "Speech detected", durationMs = 1800) {
  setSpeechIndicator(true, text);
  speechIndicatorResetTimer = window.setTimeout(() => {
    speechIndicatorResetTimer = null;
    if (!recording) {
      setSpeechIndicator(false);
    }
  }, durationMs);
}

function formatClockTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatCountdown(ms) {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function updateProactiveNudgeDisplay() {
  if (!proactiveNudgeText) {
    return;
  }
  if (!proactiveNudgeDueAt) {
    proactiveNudgeText.textContent = "";
    return;
  }
  const remainingMs = proactiveNudgeDueAt - Date.now();
  if (remainingMs <= 0) {
    proactiveNudgeText.textContent = "Conversation starting now";
    return;
  }
  proactiveNudgeText.textContent = `Conversation starts in ${formatCountdown(remainingMs)} at ${formatClockTime(proactiveNudgeDueAt)}`;
}

function updateProactiveContextDisplay() {
  if (!proactiveContextPanel || !proactiveContextText) {
    return;
  }
  const context = String(proactiveNudgeContext || "").trim();
  proactiveContextPanel.hidden = !context;
  proactiveContextText.textContent = context;
}

function startProactiveNudgeDisplay() {
  updateProactiveNudgeDisplay();
  updateProactiveContextDisplay();
  if (proactiveNudgeDisplayTimer) {
    clearInterval(proactiveNudgeDisplayTimer);
  }
  proactiveNudgeDisplayTimer = window.setInterval(updateProactiveNudgeDisplay, 1000);
}

function stopProactiveNudgeDisplay() {
  if (proactiveNudgeDisplayTimer) {
    clearInterval(proactiveNudgeDisplayTimer);
    proactiveNudgeDisplayTimer = null;
  }
  proactiveNudgeDueAt = 0;
  updateProactiveNudgeDisplay();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function addMessage(role, content = "") {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = content;
  chatEl.appendChild(node);
  chatEl.scrollTop = chatEl.scrollHeight;
  return node;
}

function updateMessage(node, content) {
  node.textContent = content;
  chatEl.scrollTop = chatEl.scrollHeight;
}

function updateRawResponse(text) {
  if (!rawResponseOutput) {
    return;
  }
  const raw = String(text || "").trim();
  rawResponseOutput.textContent = raw ? raw.slice(0, 500) : "No raw response.";
}

function getBrowserPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Browser location is unavailable."));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: false,
      timeout: 10000,
      maximumAge: 10 * 60 * 1000,
    });
  });
}

async function refreshWeatherContext(force = false) {
  if (!force && weatherContext && Date.now() - weatherFetchedAt < weatherRefreshMs) {
    return weatherContext;
  }
  if (weatherRefreshPromise) {
    return weatherRefreshPromise;
  }

  weatherRefreshPromise = (async () => {
    try {
      const position = await getBrowserPosition();
      const { latitude, longitude } = position.coords;
      const response = await fetch(
        `/api/weather?lat=${encodeURIComponent(latitude)}&lon=${encodeURIComponent(longitude)}`,
      );
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const result = await response.json();
      weatherContext = String(result.summary || "").trim();
      weatherLocation = String(result.location || "").trim();
      weatherFetchedAt = Date.now();
      void logClientEvent("weather_context", {
        decision: weatherContext ? "available" : "empty",
        detail: weatherLocation || "unknown location",
      });
      return weatherContext;
    } catch (error) {
      void logClientEvent("weather_context", {
        decision: "unavailable",
        detail: error.message || String(error),
      });
      return weatherContext;
    } finally {
      weatherRefreshPromise = null;
    }
  })();

  return weatherRefreshPromise;
}

async function lookupWeatherByLocation(location) {
  const cleanLocation = String(location || "").trim();
  if (!cleanLocation) {
    throw new Error("No weather location was provided.");
  }
  const response = await fetch(`/api/weather/lookup?location=${encodeURIComponent(cleanLocation)}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const result = await response.json();
  return {
    summary: String(result.summary || "").trim(),
    location: String(result.location || cleanLocation).trim(),
  };
}

async function speakWeatherSummary({ userText, location, summary }) {
  const response = await fetch("/api/weather/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_text: userText,
      location,
      weather_summary: summary,
      system_prompt: settings.systemPrompt,
      temperature: settings.temperature,
    }),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const result = await response.json();
  return {
    spoken: String(result.spoken || "").trim(),
    rawResponse: String(result.raw_response || "").slice(0, 500),
    seconds: Number(result.seconds || 0),
  };
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
}

function isWakeMode() {
  return settings.activationMode === "wakeWord";
}

function beginConversation() {
  cancelProactiveNudge();
  conversationActive = true;
  setStatus("Conversation mode: listening...");
}

function endConversation(message = "Conversation mode ended.") {
  conversationActive = false;
  pendingTimerSetup = null;
  if (recording) {
    stopRecording({ skipTranscription: true, status: message });
  }
  setStatus(message);
  restartWakeListenerIfNeeded();
}

function maybeContinueConversation() {
  if (
    timerAnnouncementActive ||
    !conversationActive ||
    !isWakeMode() ||
    micPaused ||
    recording ||
    chatAbortController
  ) {
    return;
  }
  startRecording({ status: "Conversation mode: listening..." }).catch((error) => {
    addMessage("system", `Conversation listener failed: ${error.message}`);
    endConversation("Conversation listener failed.");
  });
}

function setMicPaused(isPaused) {
  micPaused = isPaused;
  if (micPaused) {
    conversationActive = false;
    pendingTimerSetup = null;
    cancelProactiveNudge();
  }
  if (micPaused && recording) {
    stopRecording({ skipTranscription: true, status: "Microphone paused." });
  }
  if (micPaused) {
    stopWakeListener();
  }
  micPauseButton.textContent = micPaused ? "Mic Paused" : "Mic On";
  micPauseButton.title = micPaused ? "Resume microphone" : "Pause microphone";
  recordButton.classList.toggle("paused", micPaused);
  recordButton.disabled = micPaused;
  setSpeechIndicator(false);
  setStatus(micPaused ? "Microphone paused." : "Ready.");
  if (!micPaused) {
    restartWakeListenerIfNeeded();
  }
}

function stopOutput() {
  if (firstTokenWatchdog) {
    clearTimeout(firstTokenWatchdog);
    firstTokenWatchdog = null;
  }
  if (chatAbortController) {
    chatAbortController.abort();
    chatAbortController = null;
  }
  window.speechSynthesis?.cancel();
  if (speechCompletion) {
    speechCompletion();
    speechCompletion = null;
  }
}

function loadTimers() {
  try {
    const savedTimers = JSON.parse(localStorage.getItem(timerStorageKey) || "[]");
    if (!Array.isArray(savedTimers)) {
      return [];
    }
    return savedTimers
      .filter((timer) => timer && typeof timer === "object" && typeof timer.fireAt === "number")
      .map((timer) => ({
        id: String(timer.id || `${Date.now()}-${Math.random().toString(36).slice(2)}`),
        kind: timer.kind === "reminder" ? "reminder" : "timer",
        fireAt: Number(timer.fireAt),
        createdAt: Number(timer.createdAt || Date.now()),
        label: String(timer.label || "").trim(),
      }))
      .filter((timer) => Number.isFinite(timer.fireAt));
  } catch {
    return [];
  }
}

function saveTimers() {
  localStorage.setItem(timerStorageKey, JSON.stringify(timers));
}

function describeTimer(timer) {
  const label = String(timer.label || "").trim();
  if (!label) {
    return timer.kind === "reminder" ? "Reminder" : "Timer";
  }
  if (timer.kind === "reminder") {
    return `Reminder: ${label}`;
  }
  return /\btimer$/i.test(label) ? label : `${label} timer`;
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }
  return `${seconds}s`;
}

function formatDurationForSpeech(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const parts = [];

  if (hours > 0) {
    parts.push(`${hours} hour${hours === 1 ? "" : "s"}`);
  }
  if (minutes > 0) {
    parts.push(`${minutes} minute${minutes === 1 ? "" : "s"}`);
  }
  if (seconds > 0 || parts.length === 0) {
    parts.push(`${seconds} second${seconds === 1 ? "" : "s"}`);
  }

  if (parts.length === 1) {
    return parts[0];
  }
  if (parts.length === 2) {
    return `${parts[0]} and ${parts[1]}`;
  }
  return `${parts.slice(0, -1).join(", ")}, and ${parts[parts.length - 1]}`;
}

async function parseTimerWithAI(text, context = "") {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20000);

  try {
    const response = await fetch("/api/timer/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context }),
      signal: controller.signal,
    });

    if (!response.ok) {
      return null;
    }

    const result = await response.json();
    return {
      action: ["create_timer", "ask_duration"].includes(result.action)
        ? result.action
        : "continue",
      kind: result.kind === "reminder" ? "reminder" : "timer",
      durationMs: Math.max(0, Math.round(Number(result.duration_ms || 0))),
      label: String(result.label || "").trim(),
      confidence: Number(result.confidence || 0),
      reason: String(result.reason || ""),
      seconds: Number(result.seconds || 0),
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

function activeTimerSummaries() {
  return timers
    .slice()
    .sort((a, b) => a.fireAt - b.fireAt)
    .map((timer, index) => {
      const rawLabel = String(timer.label || "").trim();
      const displayLabel = describeTimer(timer);
      const aliases = Array.from(
        new Set(
          [rawLabel, displayLabel, rawLabel.replace(/\b(timer|reminder)$/i, "").trim()]
            .map((value) => value.trim())
            .filter(Boolean),
        ),
      );
      return {
        id: timer.id,
        index: index + 1,
        kind: timer.kind === "reminder" ? "reminder" : "timer",
        label: displayLabel,
        rawLabel,
        aliases,
        remainingMs: Math.max(0, timer.fireAt - Date.now()),
        remainingText: formatDurationForSpeech(Math.max(0, timer.fireAt - Date.now())),
      };
    });
}

function commandReferencesActiveTimer(command) {
  if (!timers.length) {
    return false;
  }

  return activeTimerSummaries().some((timer) => {
    const label = normalizeCommand(timer.label);
    const rawLabel = normalizeCommand(timer.rawLabel);
    return (label && command.includes(label)) || (rawLabel && command.includes(rawLabel));
  });
}

function hasTimerSetupVerb(command) {
  return ["set", "setup", "start", "create", "make", "remind"].some((word) =>
    command.includes(word),
  );
}

async function parseTimerCancelWithAI(text) {
  if (!timers.length) {
    return null;
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20000);

  try {
    const response = await fetch("/api/timer/cancel/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, active_timers: activeTimerSummaries() }),
      signal: controller.signal,
    });

    if (!response.ok) {
      return null;
    }

    const result = await response.json();
    return {
      action: ["cancel_timer", "cancel_all_timers", "ask_clarification"].includes(result.action)
        ? result.action
        : "continue",
      targetId: String(result.target_id || ""),
      confidence: Number(result.confidence || 0),
      reason: String(result.reason || ""),
      seconds: Number(result.seconds || 0),
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function assistantTurn(text, signal) {
  await refreshWeatherContext();

  const controller = signal ? null : new AbortController();
  const timeoutId = window.setTimeout(() => {
    if (controller) {
      controller.abort();
    }
  }, 30000);

  try {
    const response = await fetch("/api/assistant/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        messages,
        active_timers: activeTimerSummaries(),
        pending_timer_setup: pendingTimerSetup,
        weather_context: weatherContext,
        system_prompt: settings.systemPrompt,
        temperature: settings.temperature,
      }),
      signal: signal || controller.signal,
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const result = await response.json();
    return {
      intent: String(result.intent || "chat"),
      kind: result.kind === "reminder" ? "reminder" : "timer",
      durationMs: Math.max(0, Math.round(Number(result.duration_ms || 0))),
      label: String(result.label || "").trim(),
      targetTimerId: String(result.target_timer_id || ""),
      weatherLocation: String(result.weather_location || "").trim(),
      spoken: String(result.spoken || "").trim(),
      rawResponse: String(result.raw_response || "").slice(0, 500),
      confidence: Number(result.confidence || 0),
      reason: String(result.reason || ""),
      seconds: Number(result.seconds || 0),
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

function cancelProactiveNudge() {
  if (proactiveNudgeTimer) {
    clearTimeout(proactiveNudgeTimer);
  }
  proactiveNudgeTimer = null;
  proactiveNudgeContext = "";
  updateProactiveContextDisplay();
  stopProactiveNudgeDisplay();
}

function appendProactiveContext(text) {
  const clean = String(text || "").trim();
  if (!clean) {
    return;
  }

  const existing = proactiveNudgeContext.trim();
  if (!existing) {
    proactiveNudgeContext = clean.slice(-800);
    updateProactiveContextDisplay();
    return;
  }

  const existingWords = existing.split(/\s+/);
  const newWords = clean.split(/\s+/);
  const existingLower = existingWords.map((word) => normalizeCommand(word));
  const newLower = newWords.map((word) => normalizeCommand(word));
  const maxOverlap = Math.min(existingWords.length, newWords.length, 12);
  let overlap = 0;
  for (let count = maxOverlap; count > 0; count -= 1) {
    const tail = existingLower.slice(-count).join(" ");
    const head = newLower.slice(0, count).join(" ");
    if (tail && tail === head) {
      overlap = count;
      break;
    }
  }

  const addition = newWords.slice(overlap).join(" ");
  if (addition) {
    proactiveNudgeContext = `${existing} ${addition}`.trim().slice(-800);
  }
  updateProactiveContextDisplay();
}

function replaceProactiveContext(text) {
  const clean = String(text || "").trim();
  if (!clean) {
    return;
  }
  proactiveNudgeContext = clean.slice(-800);
  updateProactiveContextDisplay();
}

function scheduleProactiveNudge(text, options = {}) {
  if (!isWakeMode() || micPaused || conversationActive || recording) {
    return;
  }

  if (options.replace) {
    replaceProactiveContext(text);
  } else {
    appendProactiveContext(text);
  }
  if (proactiveNudgeTimer) {
    return;
  }

  const delayMs = 30000 + Math.floor(Math.random() * 30001);
  proactiveNudgeDueAt = Date.now() + delayMs;
  proactiveNudgeTimer = window.setTimeout(() => {
    proactiveNudgeTimer = null;
    void fireProactiveNudge();
  }, delayMs);
  startProactiveNudgeDisplay();

  void logClientEvent("proactive_nudge_scheduled", {
    text: proactiveNudgeContext,
    decision: "scheduled",
    detail: `delayMs=${delayMs}`,
  });
}

async function proactiveOpen(context) {
  await refreshWeatherContext();

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch("/api/proactive/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        context,
        weather_context: weatherContext,
        system_prompt: settings.systemPrompt,
        temperature: settings.temperature,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const result = await response.json();
    return {
      spoken: String(result.spoken || "").trim(),
      rawResponse: String(result.raw_response || "").slice(0, 500),
      seconds: Number(result.seconds || 0),
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fireProactiveNudge() {
  if (!isWakeMode() || micPaused) {
    cancelProactiveNudge();
    return;
  }

  if (recording || chatAbortController || conversationActive || timerAnnouncementActive) {
    proactiveNudgeTimer = window.setTimeout(() => {
      proactiveNudgeTimer = null;
      void fireProactiveNudge();
    }, 15000);
    proactiveNudgeDueAt = Date.now() + 15000;
    startProactiveNudgeDisplay();
    return;
  }

  const context = proactiveNudgeContext;
  proactiveNudgeContext = "";
  updateProactiveContextDisplay();
  stopProactiveNudgeDisplay();
  stopWakeListener();
  setBusy(true);
  setStatus("Gemma is starting a conversation...");

  try {
    const result = await proactiveOpen(context);
    const spoken = result.spoken || "Anything interesting you want to talk about?";
    updateRawResponse(result.rawResponse);
    messages.push({ role: "assistant", content: spoken });
    lastAssistantText = spoken;
    addMessage("assistant", spoken);
    await speak(spoken);
    beginConversation();
    setStatus(`Proactive opener ${result.seconds || "?"}s. Conversation mode: listening...`);
    maybeContinueConversation();
  } catch (error) {
    addMessage("system", `Proactive opener failed: ${error.message}`);
    setStatus("Proactive opener failed.");
    restartWakeListenerIfNeeded();
  } finally {
    setBusy(false);
  }
}

async function logClientEvent(event, { text = "", decision = "", detail = "" } = {}) {
  try {
    await fetch("/api/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, text, decision, detail }),
      keepalive: true,
    });
  } catch {
    // Logging must never block the main flow.
  }
}

function scheduleTimer(timer) {
  const existing = timerTimeouts.get(timer.id);
  if (existing) {
    clearTimeout(existing);
  }

  const delay = Math.max(0, timer.fireAt - Date.now());
  const timeoutId = window.setTimeout(() => {
    timerTimeouts.delete(timer.id);
    void fireTimer(timer.id);
  }, delay);
  timerTimeouts.set(timer.id, timeoutId);
}

function renderTimers() {
  if (!timersList || !timersStatus || !clearTimersButton) {
    return;
  }

  const activeTimers = [...timers].sort((a, b) => a.fireAt - b.fireAt);
  timersList.innerHTML = "";

  if (!activeTimers.length) {
    timersStatus.textContent = "No active timers.";
    clearTimersButton.disabled = true;
    const empty = document.createElement("p");
    empty.className = "timers-empty";
    empty.textContent = "No active timers.";
    timersList.appendChild(empty);
    return;
  }

  timersStatus.textContent =
    activeTimers.length === 1 ? "1 active timer." : `${activeTimers.length} active timers.`;
  clearTimersButton.disabled = false;

  for (const timer of activeTimers) {
    const row = document.createElement("div");
    row.className = "timer-item";

    const main = document.createElement("div");
    main.className = "timer-main";

    const label = document.createElement("div");
    label.className = "timer-label";
    label.textContent = describeTimer(timer);

    const meta = document.createElement("div");
    meta.className = "timer-meta";
    meta.textContent = `Due in ${formatDuration(Math.max(0, timer.fireAt - Date.now()))}`;

    main.append(label, meta);

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
      const removed = cancelTimer(timer.id);
      if (removed) {
        addLocalNotice(`${label.textContent} canceled.`);
      }
    });

    row.append(main, cancel);
    timersList.appendChild(row);
  }
}

function addTimer(kind, durationMs, label = "") {
  const now = Date.now();
  const timer = {
    id: crypto.randomUUID ? crypto.randomUUID() : `${now}-${Math.random().toString(36).slice(2)}`,
    kind,
    fireAt: now + durationMs,
    createdAt: now,
    label: String(label || "").trim(),
  };

  timers = [...timers, timer].sort((a, b) => a.fireAt - b.fireAt);
  saveTimers();
  scheduleTimer(timer);
  renderTimers();
  return timer;
}

function cancelTimer(timerId) {
  const index = timers.findIndex((timer) => timer.id === timerId);
  if (index < 0) {
    return null;
  }

  const [timer] = timers.splice(index, 1);
  const timeoutId = timerTimeouts.get(timerId);
  if (timeoutId) {
    clearTimeout(timeoutId);
  }
  timerTimeouts.delete(timerId);
  saveTimers();
  renderTimers();
  return timer;
}

function clearAllTimers() {
  for (const timeoutId of timerTimeouts.values()) {
    clearTimeout(timeoutId);
  }
  timerTimeouts.clear();
  timers = [];
  saveTimers();
  renderTimers();
}

function cancelMostRecentTimer() {
  if (!timers.length) {
    addLocalNotice("No active timers.");
    return false;
  }

  const timer = [...timers].sort((a, b) => b.createdAt - a.createdAt)[0];
  const removed = cancelTimer(timer.id);
  if (!removed) {
    return false;
  }

  pendingTimerSetup = null;
  addLocalNotice(`${removed.kind === "reminder" ? "Reminder" : "Timer"} canceled.`);
  return true;
}

async function cancelTimerWithAI(text) {
  if (!timers.length) {
    addLocalNotice("No active timers.");
    return true;
  }

  setStatus("Checking which timer to cancel...");
  const aiRequest = await parseTimerCancelWithAI(text);
  if (!aiRequest || aiRequest.action === "continue" || aiRequest.confidence < 0.55) {
    return false;
  }

  if (aiRequest.action === "cancel_all_timers") {
    if (aiRequest.confidence < 0.75) {
      addLocalNotice("Which timer should I cancel?");
      return true;
    }
    clearAllTimers();
    pendingTimerSetup = null;
    addLocalNotice("All timers cleared.");
    return true;
  }

  if (aiRequest.action === "ask_clarification") {
    addLocalNotice("Which timer should I cancel?");
    return true;
  }

  const timer = timers.find((item) => item.id === aiRequest.targetId);
  if (!timer) {
    addLocalNotice("Which timer should I cancel?");
    return true;
  }

  const description = describeTimer(timer);
  const removed = cancelTimer(timer.id);
  if (removed) {
    pendingTimerSetup = null;
    addLocalNotice(`${description} canceled.`);
  }
  return true;
}

async function fireTimer(timerId) {
  const timer = cancelTimer(timerId);
  if (!timer) {
    return;
  }

  const announcement = `${describeTimer(timer)} done.`;
  timerAnnouncementActive = true;

  try {
    stopWakeListener();
    if (recording) {
      stopRecording({ skipTranscription: true, status: announcement });
    }
    stopOutput();
    addMessage("system", announcement);
    setStatus(announcement);
    await speak(announcement);
  } finally {
    timerAnnouncementActive = false;
    if (conversationActive) {
      maybeContinueConversation();
    } else {
      restartWakeListenerIfNeeded();
    }
  }
}

async function announceTimerMessage(message) {
  timerAnnouncementActive = true;

  try {
    stopWakeListener();
    if (recording) {
      stopRecording({ skipTranscription: true, status: message });
    }
    stopOutput();
    addLocalNotice(message);
    if (settings.speak) {
      await speak(message);
    }
  } finally {
    timerAnnouncementActive = false;
    if (conversationActive) {
      maybeContinueConversation();
    } else {
      restartWakeListenerIfNeeded();
    }
  }
}

function announceTimerSet(timer) {
  conversationActive = false;
  pendingTimerSetup = null;
  const message = `${describeTimer(timer)} set for ${formatDurationForSpeech(
    Math.max(0, timer.fireAt - Date.now()),
  )}.`;
  void announceTimerMessage(message);
}

async function promptForTimerSetup(timerSetup) {
  pendingTimerSetup = {
    kind: timerSetup.kind || "timer",
    label: String(timerSetup.label || "").trim(),
    sourceText: String(timerSetup.sourceText || "").trim(),
    promptText: timerSetupPrompt(timerSetup),
  };
  await announceTimerMessage(pendingTimerSetup.promptText);
}

function restoreTimers() {
  for (const timer of timers) {
    scheduleTimer(timer);
  }
  renderTimers();
}

async function handleTimerCommand(text) {
  const command = stripWakePrefix(normalizeCommand(text));
  const cancelSetupCommands = new Set([
    "cancel timer setup",
    "stop timer setup",
    "forget it",
    "never mind",
    "nevermind",
  ]);

  if (pendingTimerSetup && cancelSetupCommands.has(command)) {
    pendingTimerSetup = null;
    await announceTimerMessage("Timer setup canceled.");
    return true;
  }

  if (pendingTimerSetup) {
    if (timers.length && isTimerCancelCommand(command)) {
      const handled = await cancelTimerWithAI(text);
      pendingTimerSetup = null;
      if (handled) {
        return true;
      }
    }

    if (isTimerPromptEcho(command, pendingTimerSetup)) {
      return true;
    }

    const context = buildTimerContext(pendingTimerSetup);
    setStatus("Checking timer details...");
    const aiRequest = await parseTimerWithAI(text, context);
    if (aiRequest && aiRequest.action === "create_timer" && aiRequest.durationMs > 0) {
      const timer = addTimer(
        aiRequest.kind,
        aiRequest.durationMs,
        aiRequest.label || pendingTimerSetup.label || "",
      );
      pendingTimerSetup = null;
      announceTimerSet(timer);
      return true;
    }

    if (aiRequest && aiRequest.action === "ask_duration") {
      await promptForTimerSetup({
        kind: aiRequest.kind,
        label: aiRequest.label || pendingTimerSetup.label || "",
        sourceText: pendingTimerSetup.sourceText || text,
      });
      return true;
    }

    setStatus("Waiting for timer duration...");
    return true;
  }

  setStatus("Checking timer details...");
  const aiRequest = await parseTimerWithAI(text);
  if (aiRequest && aiRequest.action === "create_timer" && aiRequest.durationMs > 0) {
    const timer = addTimer(aiRequest.kind, aiRequest.durationMs, aiRequest.label || "");
    pendingTimerSetup = null;
    announceTimerSet(timer);
    return true;
  }

  if (aiRequest && aiRequest.action === "ask_duration") {
    if (timers.length && commandReferencesActiveTimer(command) && !hasTimerSetupVerb(command)) {
      return false;
    }
    await promptForTimerSetup({
      kind: aiRequest.kind,
      label: aiRequest.label || "",
      sourceText: text,
    });
    return true;
  }

  return false;
}

function listTimers() {
  if (!timers.length) {
    addLocalNotice("No active timers.");
    return true;
  }

  const summary = timers
    .slice()
    .sort((a, b) => a.fireAt - b.fireAt)
    .map((timer, index) => {
      const label = timer.kind === "reminder" ? "Reminder" : "Timer";
      return `${index + 1}. ${label} in ${formatDuration(Math.max(0, timer.fireAt - Date.now()))}`;
    })
    .join(" ");
  addLocalNotice(summary);
  return true;
}

async function handleAssistantTurnResult(text, result) {
  const spoken = result.spoken || "Okay.";
  messages.push({ role: "user", content: text });
  addMessage("user", text);

  if (result.intent === "start_timer" && result.durationMs > 0) {
    const timer = addTimer(result.kind, result.durationMs, result.label || "");
    pendingTimerSetup = null;
    const reply = spoken || `${describeTimer(timer)} set for ${formatDurationForSpeech(result.durationMs)}.`;
    messages.push({ role: "assistant", content: reply });
    lastAssistantText = reply;
    addMessage("assistant", reply);
    await speak(reply);
    conversationActive = false;
    setStatus(`Assistant turn ${result.seconds || "?"}s. Listening for wake word.`);
    restartWakeListenerIfNeeded();
    return;
  }

  if (result.intent === "ask_timer_duration") {
    pendingTimerSetup = {
      kind: result.kind || "timer",
      label: result.label || "",
      sourceText: text,
      promptText: spoken || "For how long?",
    };
    messages.push({ role: "assistant", content: pendingTimerSetup.promptText });
    lastAssistantText = pendingTimerSetup.promptText;
    addMessage("assistant", pendingTimerSetup.promptText);
    await speak(pendingTimerSetup.promptText);
    setStatus(`Assistant turn ${result.seconds || "?"}s.`);
    return;
  }

  if (result.intent === "cancel_timer") {
    const timer = timers.find((item) => item.id === result.targetTimerId);
    const description = timer ? describeTimer(timer) : "";
    const removed = timer ? cancelTimer(timer.id) : null;
    const reply = removed ? spoken || `${description} canceled.` : "Which timer should I cancel?";
    pendingTimerSetup = null;
    messages.push({ role: "assistant", content: reply });
    lastAssistantText = reply;
    addMessage("assistant", reply);
    await speak(reply);
    conversationActive = false;
    setStatus(`Assistant turn ${result.seconds || "?"}s. Listening for wake word.`);
    restartWakeListenerIfNeeded();
    return;
  }

  if (result.intent === "cancel_all_timers") {
    clearAllTimers();
    pendingTimerSetup = null;
    const reply = spoken || "All timers canceled.";
    messages.push({ role: "assistant", content: reply });
    lastAssistantText = reply;
    addMessage("assistant", reply);
    await speak(reply);
    conversationActive = false;
    setStatus(`Assistant turn ${result.seconds || "?"}s. Listening for wake word.`);
    restartWakeListenerIfNeeded();
    return;
  }

  if (result.intent === "get_weather") {
    pendingTimerSetup = null;
    if (!result.weatherLocation) {
      const reply = spoken || "Which city should I check?";
      messages.push({ role: "assistant", content: reply });
      lastAssistantText = reply;
      addMessage("assistant", reply);
      await speak(reply);
      setStatus(`Assistant turn ${result.seconds || "?"}s.`);
      return;
    }

    setStatus(`Looking up weather for ${result.weatherLocation}...`);
    try {
      const weather = await lookupWeatherByLocation(result.weatherLocation);
      let reply = weather.summary || `I could not find weather for ${result.weatherLocation}.`;
      try {
        setStatus(`Summarizing weather for ${weather.location}...`);
        const weatherSpeech = await speakWeatherSummary({
          userText: text,
          location: weather.location,
          summary: weather.summary,
        });
        updateRawResponse(weatherSpeech.rawResponse);
        reply = weatherSpeech.spoken || reply;
      } catch (speechError) {
        void logClientEvent("weather_speech", {
          text,
          decision: "fallback",
          detail: speechError.message || String(speechError),
        });
      }
      messages.push({ role: "assistant", content: reply });
      lastAssistantText = reply;
      addMessage("assistant", reply);
      await speak(reply);
      setStatus(`Weather for ${weather.location}.`);
    } catch (error) {
      const reply = `I could not get weather for ${result.weatherLocation}.`;
      messages.push({ role: "assistant", content: reply });
      lastAssistantText = reply;
      addMessage("assistant", reply);
      await speak(reply);
      setStatus(`Weather lookup failed: ${error.message}`);
    }
    return;
  }

  if (result.intent === "end_conversation") {
    pendingTimerSetup = null;
    conversationActive = false;
    const reply = spoken || "Okay.";
    messages.push({ role: "assistant", content: reply });
    lastAssistantText = reply;
    addMessage("assistant", reply);
    await speak(reply);
    setStatus("Conversation mode ended. Listening for wake word.");
    restartWakeListenerIfNeeded();
    return;
  }

  if (!pendingTimerSetup || !result.reason.toLowerCase().includes("invalid json")) {
    pendingTimerSetup = null;
  }
  messages.push({ role: "assistant", content: spoken });
  lastAssistantText = spoken;
  addMessage("assistant", spoken);
  await speak(spoken);
  setStatus(`Assistant turn ${result.seconds || "?"}s.`);
}

async function restartOllama() {
  stopOutput();
  setStatus("Restarting LLM...");
  addMessage("system", "Restarting LLM...");
  restartOllamaButton.disabled = true;

  try {
    const response = await fetch("/api/ollama/restart", { method: "POST" });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Restart failed");
    }
    addMessage("system", result.message || "LLM restarted.");
    setStatus(result.message || "LLM restarted.");
  } catch (error) {
    addMessage("system", `LLM restart failed: ${error.message}`);
    setStatus("LLM restart failed.");
  } finally {
    restartOllamaButton.disabled = false;
    restartWakeListenerIfNeeded();
  }
}

function downsampleTo16k(float32Samples, sourceRate) {
  if (sourceRate === 16000) {
    return float32Samples;
  }

  const ratio = sourceRate / 16000;
  const outputLength = Math.floor(float32Samples.length / ratio);
  const output = new Float32Array(outputLength);

  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.floor((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end && j < float32Samples.length; j += 1) {
      sum += float32Samples[j];
      count += 1;
    }
    output[i] = count > 0 ? sum / count : 0;
  }

  return output;
}

function floatToInt16Pcm(float32Samples) {
  const output = new Int16Array(float32Samples.length);
  for (let i = 0; i < float32Samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, float32Samples[i]));
    output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

async function startWakeListener() {
  if (
    wakeListening ||
    (wakeSocket && wakeSocket.readyState <= WebSocket.OPEN) ||
    !isWakeMode() ||
    micPaused ||
    recording
  ) {
    return;
  }

  await ensureRecorder();
  wakeContext = wakeContext || new AudioContext();
  if (wakeContext.state === "suspended") {
    await wakeContext.resume();
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const threshold = encodeURIComponent(settings.wakeThreshold);
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/api/wake/listen?threshold=${threshold}&ambientContextSeconds=2.5`,
  );
  wakeSocket = socket;
  socket.binaryType = "arraybuffer";

  socket.addEventListener("open", () => {
    if (wakeSocket !== socket) {
      socket.close();
      return;
    }
    wakeListening = true;
    wakeSource = wakeContext.createMediaStreamSource(audioStream);
    wakeProcessor = wakeContext.createScriptProcessor(4096, 1, 1);
    wakeProcessor.onaudioprocess = (event) => {
      if (wakeSocket !== socket || socket.readyState !== WebSocket.OPEN || recording || micPaused) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const downsampled = downsampleTo16k(input, wakeContext.sampleRate);
      const pcm = floatToInt16Pcm(downsampled);
      socket.send(pcm.buffer);
    };
    wakeSource.connect(wakeProcessor);
    wakeProcessor.connect(wakeContext.destination);
    setStatus(`Listening for "${wakeKeyword}"...`);
  });

  socket.addEventListener("message", async (event) => {
    if (wakeSocket !== socket) {
      return;
    }
    const data = JSON.parse(event.data);
    if (data.type === "ready") {
      wakeKeyword = data.keyword;
      setStatus(`Listening for "${wakeKeyword}"...`);
    }
    if (data.type === "score" && wakeListening) {
      if (Number(data.score || 0) >= 0.02) {
        pulseSpeechIndicator("Speech detected");
      }
      setStatus(`Listening for "${wakeKeyword}"... score ${data.score.toFixed(2)}`);
    }
    if (data.type === "ambient_conversation") {
      pulseSpeechIndicator("Speech detected");
      scheduleProactiveNudge(String(data.transcript || ""), { replace: Boolean(data.cumulative) });
    }
    if (data.type === "wake") {
      pulseSpeechIndicator("Wake word detected");
      cancelProactiveNudge();
      setStatus(`Wake word detected: "${data.keyword}"`);
      stopWakeListener();
      beginConversation();
      const commandText = String(data.command || "").trim();
      const wakeTranscript = String(data.transcript || "").trim();
      wakePrefillText = wakeTranscript || commandText;
      wakePrefillCommand = commandText;
      await startRecording({
        status: "Wake word detected; listening for your request...",
        noSpeechTimeoutMs: commandText ? 1800 : 0,
      });
    }
  });

  socket.addEventListener("close", () => {
    if (wakeSocket !== socket) {
      return;
    }
    wakeListening = false;
    wakeSocket = null;
    disconnectWakeAudio();
    if (isWakeMode() && !micPaused && !recording) {
      setStatus("Wake listener disconnected.");
    }
  });

  socket.addEventListener("error", () => {
    if (wakeSocket !== socket) {
      return;
    }
    setStatus("Wake listener error.");
  });
}

function disconnectWakeAudio() {
  if (wakeProcessor) {
    wakeProcessor.disconnect();
    wakeProcessor.onaudioprocess = null;
    wakeProcessor = null;
  }
  if (wakeSource) {
    wakeSource.disconnect();
    wakeSource = null;
  }
}

function stopWakeListener() {
  wakeListening = false;
  disconnectWakeAudio();
  const socket = wakeSocket;
  wakeSocket = null;
  if (socket && socket.readyState <= WebSocket.OPEN) {
    socket.close();
  }
}

function restartWakeListenerIfNeeded() {
  if (timerAnnouncementActive || !isWakeMode() || micPaused || recording || conversationActive) {
    return;
  }
  startWakeListener().catch((error) => {
    addMessage("system", `Wake listener failed: ${error.message}`);
    setStatus("Wake listener failed.");
  });
}

function normalizeCommand(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[.,!?;:]+$/g, "")
    .replace(/\s+/g, " ");
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stripWakePrefix(command) {
  const phrases = new Set([wakeKeyword, "alexa"]);
  for (const phrase of phrases) {
    const normalizedPhrase = normalizeCommand(phrase);
    if (!normalizedPhrase) {
      continue;
    }
    const pattern = new RegExp(`^${escapeRegExp(normalizedPhrase)}[\\s,.:;!?-]+(.+)$`, "i");
    const match = command.match(pattern);
    if (match) {
      return match[1].trim();
    }
  }
  return command;
}

function buildTimerContext(timerSetup) {
  const parts = [];
  if (timerSetup.kind) {
    parts.push(`kind=${timerSetup.kind}`);
  }
  if (timerSetup.label) {
    parts.push(`label=${timerSetup.label}`);
  }
  if (timerSetup.sourceText) {
    parts.push(`source=${timerSetup.sourceText}`);
  }
  return parts.join("; ");
}

function timerSetupPrompt(timerSetup) {
  if (timerSetup.kind === "reminder") {
    return timerSetup.label
      ? `How long should I set the reminder for: ${timerSetup.label}?`
      : "How long should I set the reminder for?";
  }
  if (timerSetup.label) {
    return `How long should I set the ${timerSetup.label} timer for?`;
  }
  return "For how long?";
}

function isTimerPromptEcho(command, timerSetup) {
  const prompt = normalizeCommand(timerSetup.promptText || "");
  if (!prompt) {
    return false;
  }

  if (command === prompt) {
    return true;
  }

  return (
    command.startsWith("how long should i set") ||
    command === "for how long" ||
    command === "for how long please" ||
    command === "what for how long" ||
    (timerSetup.label && command.includes(timerSetup.label) && command.includes("timer"))
  );
}

function isTimerCancelCommand(command) {
  const cancellationWords = [
    "cancel",
    "canceled",
    "cancelled",
    "council",
    "counsel",
    "castle",
    "stop",
    "delete",
    "remove",
    "dismiss",
    "turn off",
    "clear",
  ];
  const timerWords = ["timer", "alarm", "reminder"];
  if (!cancellationWords.some((word) => command.includes(word))) {
    return false;
  }
  return timerWords.some((word) => command.includes(word)) || timers.length > 0;
}

function addLocalNotice(text) {
  addMessage("system", text);
  setStatus(text);
}

function clearConversation(showNotice = true) {
  messages = [];
  lastAssistantText = "";
  chatEl.innerHTML = "";
  pendingTimerSetup = null;
  cancelProactiveNudge();
  stopOutput();
  if (showNotice) {
    addLocalNotice("Chat cleared.");
  } else {
    setStatus("Ready.");
  }
}

async function runLocalCommand(text) {
  const command = stripWakePrefix(normalizeCommand(text));

  if (
    [
      "stop sending to gemma",
      "stop sending text to gemma",
      "stop sending to the engine",
      "privacy mode",
      "go private",
      "stop listening and pause microphone",
    ].includes(command)
  ) {
    stopOutput();
    conversationActive = false;
    setMicPaused(true);
    addLocalNotice("Privacy mode enabled. Microphone paused; nothing will be sent to Gemma.");
    return true;
  }

  if (["stop speaking", "quiet"].includes(command)) {
    stopOutput();
    addLocalNotice("Stopped.");
    return true;
  }

  if (["list timers", "timer status", "what timers are running", "how much time is left"].includes(command)) {
    listTimers();
    return true;
  }

  if (["clear chat", "clear conversation", "reset chat"].includes(command)) {
    clearConversation(true);
    return true;
  }

  if (["repeat", "repeat that", "say that again"].includes(command)) {
    if (lastAssistantText) {
      speak(lastAssistantText);
      addLocalNotice("Repeating last answer.");
    } else {
      addLocalNotice("Nothing to repeat yet.");
    }
    return true;
  }

  if (["pause microphone", "pause mic", "mute microphone", "mute mic"].includes(command)) {
    setMicPaused(true);
    addLocalNotice("Microphone paused.");
    return true;
  }

  if (["resume microphone", "resume mic", "unmute microphone", "unmute mic"].includes(command)) {
    setMicPaused(false);
    addLocalNotice("Microphone resumed.");
    return true;
  }

  if (["open settings", "show settings", "settings"].includes(command)) {
    settingsPanel.hidden = false;
    addLocalNotice("Settings opened.");
    return true;
  }

  if (["close settings", "hide settings"].includes(command)) {
    settingsPanel.hidden = true;
    addLocalNotice("Settings closed.");
    return true;
  }

  if (["wake word mode", "use wake word", "start wake word"].includes(command)) {
    activationModeInput.value = "wakeWord";
    saveSettings();
    conversationActive = false;
    restartWakeListenerIfNeeded();
    addLocalNotice(`Wake word mode enabled. Say "${wakeKeyword}" to start recording.`);
    return true;
  }

  if (["push to talk mode", "use push to talk"].includes(command)) {
    activationModeInput.value = "pushToTalk";
    saveSettings();
    conversationActive = false;
    stopWakeListener();
    addLocalNotice("Push to talk mode enabled.");
    return true;
  }

  return false;
}

async function shouldEndConversationByIntent(text) {
  if (!conversationActive || !isWakeMode()) {
    return false;
  }

  if (!mayBeConversationStopCommand(text)) {
    return false;
  }

  setStatus("Checking whether to end conversation...");
  const startedAt = performance.now();

  try {
    const response = await fetch("/api/intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (!response.ok) {
      return false;
    }

    const result = await response.json();
    const elapsedSeconds = ((performance.now() - startedAt) / 1000).toFixed(2);
    console.log(
      `Intent check: ${result.seconds ?? elapsedSeconds}s server, ${elapsedSeconds}s browser, intent: ${result.intent}, confidence: ${result.confidence}, reason: ${result.reason}`,
    );
    return result.intent === "end_conversation" && Number(result.confidence || 0) >= 0.55;
  } catch {
    return false;
  }
}

function mayBeConversationStopCommand(text) {
  const command = normalizeCommand(text);
  const stopPatterns = [
    "all done",
    "back to standby",
    "cancel",
    "done",
    "end",
    "enough",
    "exit",
    "go away",
    "goodbye",
    "i am done",
    "leave me alone",
    "never mind",
    "no more",
    "pause",
    "private",
    "quit",
    "shut up",
    "standby",
    "stop",
    "that is all",
    "that's all",
    "we are done",
    "we're done",
  ];

  return stopPatterns.some((pattern) => command.includes(pattern));
}

function populateVoices() {
  if (!("speechSynthesis" in window)) {
    voiceSelect.innerHTML = '<option value="">Browser TTS unavailable</option>';
    voiceSelect.disabled = true;
    return;
  }

  const voices = window.speechSynthesis.getVoices();
  voiceSelect.innerHTML = '<option value="">Default voice</option>';
  for (const voice of voices) {
    const option = document.createElement("option");
    option.value = voice.voiceURI;
    option.textContent = `${voice.name} (${voice.lang})`;
    voiceSelect.appendChild(option);
  }
  voiceSelect.value = settings.voiceURI;
}

function speak(text) {
  if (!settings.speak || !("speechSynthesis" in window)) {
    return Promise.resolve();
  }

  window.speechSynthesis.cancel();
  return new Promise((resolve) => {
    speechCompletion = resolve;
    const utterance = new SpeechSynthesisUtterance(text);
    const selectedVoice = window.speechSynthesis
      .getVoices()
      .find((voice) => voice.voiceURI === settings.voiceURI);
    if (selectedVoice) {
      utterance.voice = selectedVoice;
    }
    utterance.rate = 1;
    utterance.pitch = 1;
    utterance.onend = () => {
      speechCompletion = null;
      resolve();
    };
    utterance.onerror = () => {
      speechCompletion = null;
      resolve();
    };
    window.speechSynthesis.speak(utterance);
  });
}

async function loadConfig() {
  try {
    const [configResponse, healthResponse] = await Promise.all([
      fetch("/api/config"),
      fetch("/api/health"),
    ]);
    const config = await configResponse.json();
    const health = await healthResponse.json();
    wakeKeyword = config.wakeWord || wakeKeyword;
    const provider = config.llmProvider || "llama";
    const model = config.llmModel || config.ollamaModel;
    const unavailable = provider === "llama" ? "llama-server not ready" : "not found in Ollama";
    setStatus(
      `${provider} ${model} | Whisper ${config.whisperFastModel || config.whisperModel}/${config.whisperDevice} | wake ${config.whisperWakeModel || config.whisperFastModel || ""} | ${
        health.modelAvailable ? "ready" : unavailable
      }`,
    );
    void refreshWeatherContext();
    restartWakeListenerIfNeeded();
  } catch (error) {
    setStatus(`Backend ready; LLM check failed: ${error.message}`);
  }
}

async function ensureRecorder() {
  if (mediaRecorder) {
    return;
  }

  audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(audioStream);

  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) {
      recordedChunks.push(event.data);
    }
  });

  mediaRecorder.addEventListener("stop", async () => {
    stopVad();
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    recordedChunks = [];
    if (skipNextTranscription) {
      skipNextTranscription = false;
      wakePrefillText = "";
      wakePrefillCommand = "";
      restartWakeListenerIfNeeded();
      return;
    }
    await transcribeBlob(blob);
  });
}

function ensureAnalyser() {
  if (analyser || !audioStream) {
    return;
  }

  audioContext = new AudioContext();
  const source = audioContext.createMediaStreamSource(audioStream);
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
}

function getVolume() {
  if (!analyser) {
    return 0;
  }

  const data = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(data);
  let sum = 0;
  for (const value of data) {
    const centered = value - 128;
    sum += centered * centered;
  }
  return Math.sqrt(sum / data.length) / 128;
}

function wordCount(text) {
  return normalizeCommand(text).split(" ").filter(Boolean).length;
}

function mergeWakePrefill(prefix, command, transcript) {
  const cleanPrefix = String(prefix || "").trim();
  const cleanCommand = String(command || "").trim();
  const cleanTranscript = String(transcript || "").trim();
  if (!cleanPrefix) {
    return cleanTranscript;
  }
  if (!cleanTranscript) {
    return cleanPrefix;
  }

  const normalizedTranscript = normalizeCommand(cleanTranscript);
  const normalizedPrefix = normalizeCommand(cleanPrefix);
  const normalizedCommand = normalizeCommand(cleanCommand);
  if (
    (normalizedPrefix && normalizedTranscript.includes(normalizedPrefix)) ||
    (normalizedCommand && normalizedTranscript.includes(normalizedCommand))
  ) {
    return cleanTranscript;
  }

  const wake = normalizeCommand(wakeKeyword);
  if (wake && normalizedTranscript.startsWith(wake)) {
    return cleanTranscript;
  }

  if (cleanCommand && wordCount(cleanTranscript) >= wordCount(cleanCommand) + 2) {
    return cleanTranscript;
  }

  return `${cleanPrefix.replace(/[.!?…]+$/u, "")} ${cleanTranscript}`.trim();
}

function startVad() {
  stopVad();
  if (!settings.autoStop && !conversationActive) {
    return;
  }

  ensureAnalyser();
  recordingStartedAt = performance.now();
  speechDetected = false;
  silentSince = null;
  recordButton.classList.add("listening");

  const tick = () => {
    if (!recording) {
      return;
    }

    const now = performance.now();
    const volume = getVolume();
    const threshold = settings.silenceThreshold / 100;
    const minRecordingMs = 700;
    const conversationTimeoutMs = settings.conversationTimeout * 1000;

    if (volume >= threshold) {
      speechDetected = true;
      silentSince = null;
      setSpeechIndicator(true);
    } else if (!speechDetected && recordingNoSpeechTimeoutMs && now - recordingStartedAt >= recordingNoSpeechTimeoutMs) {
      stopRecording({ status: "Processing wake phrase..." });
      return;
    } else if (conversationActive && !speechDetected && now - recordingStartedAt >= conversationTimeoutMs) {
      conversationActive = false;
      stopRecording({ skipTranscription: true, status: "Conversation timed out." });
      return;
    } else if (speechDetected && now - recordingStartedAt > minRecordingMs) {
      silentSince ??= now;
      setSpeechIndicator(false);
      if (now - silentSince >= settings.silenceDelay) {
        stopRecording();
        return;
      }
    } else if (!speechDetected) {
      setSpeechIndicator(false);
    }

    vadFrame = requestAnimationFrame(tick);
  };

  vadFrame = requestAnimationFrame(tick);
}

function stopVad() {
  if (vadFrame) {
    cancelAnimationFrame(vadFrame);
    vadFrame = null;
  }
  recordButton.classList.remove("listening");
  setSpeechIndicator(false);
}

async function startRecording(options = {}) {
  if (micPaused) {
    setStatus("Microphone paused.");
    return;
  }

  cancelProactiveNudge();
  stopWakeListener();
  stopOutput();
  await ensureRecorder();
  recording = true;
  recordedChunks = [];
  recordButton.classList.add("recording");
  recordIcon.textContent = "STOP";
  recordingNoSpeechTimeoutMs = Number(options.noSpeechTimeoutMs || 0);
  setStatus(
    options.status ||
      (settings.autoStop ? "Recording; pauses will stop automatically." : "Recording..."),
  );
  mediaRecorder.start();
  startVad();
}

function stopRecording(options = {}) {
  if (!mediaRecorder || mediaRecorder.state === "inactive") {
    return;
  }
  skipNextTranscription = Boolean(options.skipTranscription);
  recording = false;
  recordingNoSpeechTimeoutMs = 0;
  stopVad();
  recordButton.classList.remove("recording");
  recordIcon.textContent = "REC";
  setStatus(options.status || "Transcribing...");
  mediaRecorder.stop();
}

async function transcribeBlob(blob) {
  setBusy(true);
  try {
    const form = new FormData();
    form.append("audio", blob, "recording.webm");
    const mode = encodeURIComponent(settings.transcriptionMode);
    const response = await fetch(`/api/transcribe?mode=${mode}`, {
      method: "POST",
      body: form,
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    const data = await response.json();
    const transcribedText = String(data.text || "").trim();
    const finalText = mergeWakePrefill(wakePrefillText, wakePrefillCommand, transcribedText);
    const hadWakePrefill = Boolean(wakePrefillText || wakePrefillCommand);
    wakePrefillText = "";
    wakePrefillCommand = "";
    promptInput.value = finalText;
    void logClientEvent("transcription", {
      text: finalText || "",
      decision: finalText ? "speech" : "empty",
      detail: `model=${data.model || ""}; total=${data.totalSeconds || ""}; raw=${transcribedText ? "speech" : "empty"}; wakePrefix=${hadWakePrefill ? "yes" : "no"}`,
    });
    if (finalText) {
      setStatus(
        `Transcribed with ${data.model} in ${data.totalSeconds}s.`,
      );
      await sendPrompt();
    } else {
      setStatus(`No speech detected. Transcription took ${data.totalSeconds}s.`);
    }
  } catch (error) {
    addMessage("system", `Transcription failed: ${error.message}`);
    setStatus("Transcription failed.");
  } finally {
    setBusy(false);
    if (conversationActive) {
      maybeContinueConversation();
    } else {
      restartWakeListenerIfNeeded();
    }
  }
}

async function sendPrompt() {
  const text = promptInput.value.trim();
  if (!text) {
    return;
  }

  stopOutput();
  promptInput.value = "";

  const localHandled = await runLocalCommand(text);
  void logClientEvent("local_command", {
    text,
    decision: localHandled ? "handled" : "forwarded",
    detail: localHandled ? "handled locally" : "sent onward",
  });
  if (localHandled) {
    restartWakeListenerIfNeeded();
    return;
  }

  stopWakeListener();
  setBusy(true);
  setStatus("Sending to Gemma...");
  chatAbortController = new AbortController();

  try {
    const result = await assistantTurn(text, chatAbortController.signal);
    updateRawResponse(result.rawResponse);
    console.log(
      `Assistant turn: ${result.seconds}s, intent: ${result.intent}, confidence: ${result.confidence}, reason: ${result.reason}`,
    );
    await handleAssistantTurnResult(text, result);
  } catch (error) {
    if (error.name === "AbortError") {
      addMessage("system", "Gemma took too long to respond.");
      setStatus("Gemma timed out.");
    } else {
      addMessage("system", `Chat failed: ${error.message}`);
      setStatus("Chat failed.");
    }
  } finally {
    chatAbortController = null;
    setBusy(false);
    if (conversationActive) {
      maybeContinueConversation();
    } else {
      restartWakeListenerIfNeeded();
    }
  }
}

recordButton.addEventListener("click", async () => {
  const activationMode = activationModes[settings.activationMode] || activationModes.pushToTalk;

  if (recording) {
    activationMode.stop();
    return;
  }

  try {
    await activationMode.start();
  } catch (error) {
    addMessage("system", `Microphone failed: ${error.message}`);
    setStatus("Microphone unavailable.");
  }
});

sendButton.addEventListener("click", sendPrompt);

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendPrompt();
  }
});

settingsButton.addEventListener("click", () => {
  settingsPanel.hidden = !settingsPanel.hidden;
});

for (const input of [
  speakToggle,
  autoStopToggle,
  silenceThresholdInput,
  silenceDelayInput,
  activationModeInput,
  wakeThresholdInput,
  conversationTimeoutInput,
  transcriptionModeInput,
  temperatureInput,
  voiceSelect,
  systemPromptInput,
]) {
  input.addEventListener("input", saveSettings);
  input.addEventListener("change", saveSettings);
}

activationModeInput.addEventListener("change", () => {
  conversationActive = false;
  cancelProactiveNudge();
  stopWakeListener();
  restartWakeListenerIfNeeded();
});

wakeThresholdInput.addEventListener("change", () => {
  if (isWakeMode()) {
    stopWakeListener();
    restartWakeListenerIfNeeded();
  }
});

clearButton.addEventListener("click", () => {
  conversationActive = false;
  clearConversation(false);
});

stopButton.addEventListener("click", () => {
  stopOutput();
  setStatus("Stopped.");
});

restartOllamaButton.addEventListener("click", restartOllama);

clearTimersButton.addEventListener("click", () => {
  clearAllTimers();
  addLocalNotice("All timers cleared.");
});

micPauseButton.addEventListener("click", () => {
  setMicPaused(!micPaused);
});

resetPromptButton.addEventListener("click", () => {
  systemPromptInput.value = defaultSettings.systemPrompt;
  saveSettings();
  setStatus("System prompt reset.");
});

applySettings();
setMicPaused(false);
restoreTimers();
window.setInterval(renderTimers, 1000);
populateVoices();
window.speechSynthesis?.addEventListener("voiceschanged", populateVoices);
loadConfig();
