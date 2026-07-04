# Lessons Learned

This app evolved from a simple push-to-talk voice chat into a usable local voice assistant. These are the practical things that mattered.

## 1. Start with push-to-talk

Push-to-talk was the right first version. It reduced the problem to a known sequence:

`record -> transcribe -> route -> generate -> speak`

That made it much easier to isolate failures in audio capture, Whisper, Gemma, or TTS.

## 2. Keep wake word separate from conversation

The best interaction model was not “say the keyword before every turn.” It was:

`wake word once -> conversation mode -> follow-up turns without repeating the keyword`

That made the app feel natural while still preserving a clear entry point and privacy boundary.

## 3. Local commands should not go through the model

Commands like `stop`, `clear chat`, `pause microphone`, and `privacy mode` should be handled locally.

That avoids unnecessary model calls and removes latency from control actions that need to feel immediate.

## 4. Instrument first-token timing early

The most useful performance metric was not total response time. It was time to first token.

That exposed the actual user-visible stall. In this app, the delay often came from model load or prompt setup, not generation itself.

## 5. Ollama residency matters more than expected

The biggest latency issue was cold-loading the model.

The useful fixes were:

- keep the model resident
- use a long or indefinite `keep_alive`
- warm the model after startup
- verify residency with `ollama ps`

Also, Ollama expects `keep_alive` as a numeric `-1` if you want indefinite residency, not the string `"-1"`.

## 6. Cold-start behavior can look like a hang

When the frontend says `Sent to Gemma; waiting for first token...`, the app can appear stuck even though the backend is still alive.

That status is useful, but it needs a hard timeout and a recovery path so the UI does not wait forever on a stalled model server.

## 7. Be careful warming models during app startup

Warm-up is useful, but it should not block the web server from starting.

The better pattern was:

- start the server immediately
- warm Gemma in the background
- let the UI come up even if Ollama is still loading

## 8. Keep prompt size under control

Long conversation history increased latency.

Trimming to the most recent messages and capping prompt characters reduced unnecessary prompt growth and made timing more stable.

## 9. Use a smaller Whisper model first

`tiny.en` was the right default for quick turnaround on this machine.

It gave fast transcription, which kept the rest of the pipeline from feeling slow when Gemma was already the expensive part.

## 10. Make recovery actions explicit

When Ollama stalls, the correct behavior is not to hide the problem.

A visible `Restart Ollama` action is better than stretching the wait or pretending the model is healthy.

## 11. Keep the UI honest

Status text should reflect what the system is actually doing:

- listening for wake word
- recording
- transcribing
- sending to Gemma
- waiting for first token
- stalled before first token

Those states were more useful than a single generic “working” indicator.

## 12. Log where the time goes

The final useful logs were:

- transcription duration
- chat start
- first token latency
- total completion time
- Ollama load, prompt eval, and eval timing

Without those, the app felt slow but the failure mode stayed ambiguous.

## 13. Small defaults matter

The defaults that helped most were:

- short spoken answers
- no emojis in the system prompt
- auto-stop on silence
- wake word as an entry trigger, not a repeated requirement
- indefinite Ollama residency for the main model

Those choices made the assistant feel less chatty, less fragile, and less repetitive.

## 14. The model server is part of the product

The app is not just frontend code.

If Ollama is unstable, slow to load, or not resident, the assistant experience degrades immediately. Treat the model server as a first-class runtime dependency and measure it directly.

