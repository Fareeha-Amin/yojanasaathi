# Voice + phone line

A separate process (own venv, `voice/.venv`) that reaches the agent **only** through
`POST /turn/{case_id}`, the same contract the web app uses.

```
browser mic -> SmallWebRTC -> Silero VAD -> Sarvam Saaras STT (saaras:v4, auto language)
  -> AgentBridge: one finished turn = one POST /turn {"text", "lang"?}
  -> UserTurnProcessor (end of turn after 0.8 s silence; speaking interrupts the bot)
  -> Sarvam Bulbul TTS (bulbul:v3, language picked from the reply's script) -> speaker
```

| File | What |
|---|---|
| `bot.py` | the pipeline; `bot(runner_args)` entry point for Pipecat's dev runner |
| `bridge.py` | `AgentBridge`: buffers transcripts, calls `/turn`, speaks the reply, drops stale replies on barge-in |
| `agent_client.py` | the `/turn` HTTP client |
| `lang.py` | `kn-IN -> kn` mapping, short-turn rule, reply-script -> TTS language, fixed phrases |
| `smoke.py` | round-trip test without a mic: Bulbul speaks a line, it goes through VAD -> STT -> `/turn` |
| `tests/` | `AgentBridge` in a real Pipecat pipeline with a fake `/turn` |

## Run (Windows PowerShell, repo root)
```powershell
py -3.12 -m venv voice\.venv
.\voice\.venv\Scripts\python.exe -m pip install -r voice/requirements-dev.txt
# terminal 1: the agent
.\.venv\Scripts\python.exe -m uvicorn agent.main:app --reload
# terminal 2: the voice bot, then open http://localhost:7860/client and click Connect
.\voice\.venv\Scripts\python.exe -m voice.bot -t webrtc
```
Other commands:
```powershell
.\voice\.venv\Scripts\python.exe -m voice.smoke                      # Kannada demo line
.\voice\.venv\Scripts\python.exe -m voice.smoke my-case "ಹೌದು"        # resume a paused case by voice
.\voice\.venv\Scripts\python.exe -m pytest voice/tests -q
```
`voice.smoke` uses a few seconds of Sarvam credit per run. Set `SMOKE_LOG_LEVEL=DEBUG` to see
every frame.

## Behaviour worth knowing
- **Case:** `case_id` from the client's start body (`{"body": {"case_id": ...}}`, or
  `request_data` on `/api/offer`), else `VOICE_CASE_ID` (default `demo-case-1`, the same
  case as the web app's text chat).
- **`lang` on `/turn`:** Saaras' detected language as `kn` / `hi` / `en`, only for turns of
  3+ words. One-word answers are unreliable ("हाँ" comes back as "Yeah.", `en-IN`), so
  short turns send no `lang` and the case keeps its language. Other languages: no `lang`.
- **TTS language:** decided by the reply's script (Kannada / Devanagari / Latin), because
  that is what Bulbul has to read.
- **Barge-in:** speaking while the bot talks stops the audio. If the citizen speaks again
  while `/turn` is still in flight, that reply is dropped and only the newer one is spoken
  (the agent has already processed both turns).
- **Short words:** Sarvam's default minimum speech length dropped a 0.4 s "ಹೌದು";
  `min_speech_frames=1` fixes it (`make_stt()` in `bot.py`).
- **Agent down:** the bot says a short "service not available" line in the citizen's
  language. Nothing is decided or submitted.
- **Web app (Phase 5):** the web app (`web/src/voice.js`, `@pipecat-ai/client-js` 1.13.1 +
  `small-webrtc-transport` 1.10.8) connects through Vite's `/voice` proxy with its own case in
  the `/start` body. After every turn the bot sends an RTVI server message
  `{"type": "turn", "case_id", "text", "lang", "reply", "subtitle", "pause", "ui"}`; the web
  app shows the bubbles and opens the review / OTP / schemes screens from it.
- **`say` server message:** lines the bot says on its own (the greeting, sent on RTVI
  client-ready) also go to the client as `{"type": "say", "text", "subtitle"}`.
- **`speak` client message:** `{"t": "speak", "d": {"text": ...}}` makes Bulbul read the text
  (max 600 characters) without calling `/turn`. The web app uses it to speak replies of its
  own turns (typed text, buttons, review edits) and for read-aloud / replay.

## Phone line (Phase 7)
Same `bot.py` through the same runner: `python -m voice.bot -t twilio -x <public-host>`;
case_id from the caller's number.
