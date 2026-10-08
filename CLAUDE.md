# YojanaSaathi

Voice-first AI agent that helps Indian citizens go from "Am I eligible?" to
"Application submitted" for welfare schemes, in Kannada, Hindi and English.
It interviews the citizen, decides eligibility with deterministic rules, builds a
document checklist, pre-fills the application on a portal with a browser agent,
**stops for the citizen's explicit yes**, submits, then tracks status and follows up.
Citizens use it through a web app or by **dialing a phone number** (same agent).

Built by Team AURA for HackerRing 26' (Open Innovation track). 36-hour MVP.
Full background, MVP feature list, UI screens and demo script: see `docs/PROJECT_BRIEF.md`
(read it before starting a new feature).

## Team ownership
- Fareeha: agent orchestrator (LangGraph graph, nodes, `/turn` API)
- Aryan: voice layer (Pipecat + Sarvam) and phone line (Twilio)
- Darshan: React web app screens
- Ayush: mock government portal, separate repo `ayush81233/mock` (OTP + application flow)

## Non-negotiable design rules
1. **The LLM understands and speaks; code decides and acts.** The LLM extracts profile
   facts, routes intent and explains results. Eligibility is decided by JSON Logic rules,
   never by the LLM. Every eligibility result carries `source_url` and `effective_date`.
2. **Human gate in code.** The `confirm` node calls LangGraph `interrupt()`. Nothing may
   click the portal's final Submit until the graph is resumed with an explicit approval.
3. **Never bypass user-only verification.** OTP / CAPTCHA => `interrupt({"type": "otp"})`,
   the citizen supplies the code. The agent never reads SMS or guesses codes.
4. **Safe-stop.** If an expected portal element is missing, stop and hand control back
   (`interrupt({"type": "safe_stop", ...})`). Never guess, never fail silently.
5. **Browser automation is scripted, not LLM-driven.** One Playwright script per portal
   flow, exposed as a tool (e.g. `fill_pension_form(profile)`). Select elements by
   `data-testid` only. Screenshot after each step for the review screen.
6. **Only the mock portal.** Never automate a real government website.
7. **Privacy.** Document images are never sent to the LLM. Aadhaar is shown and logged
   as last 4 digits only. Files are AES-256-GCM encrypted at rest; Postgres stores
   metadata only. Explicit consent before saving a profile or document. Every
   consequential action goes through `log_event()` into the append-only audit log.
8. **Replies are spoken.** Keep agent replies short (1-3 sentences), in the user's
   language (`kn` / `hi` / `en`). Read back names, numbers and amounts before submit.

## Final tech stack (do not add or swap components without asking)
| Layer | Choice |
|---|---|
| Web app | React (Vite), installable PWA, voice + text in one UI |
| Voice | Pipecat (streaming, turn-taking, barge-in); Sarvam Saaras STT, Sarvam Bulbul TTS |
| Phone line | Twilio number -> Pipecat -> same `/turn` endpoint; caller number maps to case |
| Orchestrator | LangGraph on FastAPI; Postgres checkpointer (one thread per case = case memory) |
| LLM | Frontier model with tool calling, structured output via Pydantic |
| Eligibility | JSON Logic rules in `rules/` (one file per scheme) |
| Scheme KB | Official rule text + source URL + effective date per scheme (no RAG/vector DB) |
| Browser agent | Playwright (async), scripted, against the mock portal |
| Database | PostgreSQL: citizens, profiles, schemes, cases, case_events, documents (metadata), audit_log |
| Documents | AES-256-GCM encrypted files on disk |
| Jobs & alerts | APScheduler inside FastAPI; web push notifications; spoken update in app |
| Security | TLS (hosting), JWT, audit log |

Explicitly NOT used (decided): Docker, MinIO, Redis/Celery, Flutter, OAuth2, RAG/pgvector,
real SMS (India needs DLT registration; simulate or use WhatsApp sandbox).

## The one API contract (web, voice and phone all use it)
```
POST /turn/{case_id}
  request:  { "text": "I'm 62, can I get a pension?", "lang"?: "kn" | "hi" | "en" }
  response: { "reply": "...", "pause": null | { "type": "confirm" | "otp" | "safe_stop", ...data } }
```
If the graph is paused, the incoming text resumes it (`Command(resume=text)`).
`lang` is optional (added 2026-10-08, decision 1 below): voice sends the STT-detected
language; when present it updates the case's `lang`, when absent the case keeps its
previous `lang`. Any other value is rejected with 422. Old clients sending only `text` work.
Do not change this shape without telling the whole team.

## Agent graph (target)
Nodes: router -> interview -> eligibility -> document -> respond;
proceed: planner -> browser -> confirm -> submit -> track. Follow-up agent is
triggered by the scheduler on status change. Shared state: `lang`, `profile`,
`missing`, `eligible`, `checklist`, `preview`, `app_id`, `status`, `reply`.
`interview` asks only for fields in `missing` = union of `required_fields` of schemes
still possible, minus fields already known.

## Mock portal (separate repo: github.com/ayush81233/mock)
Django + DRF + React. Planned API: `POST /api/otp/send/`, `POST /api/otp/verify/`,
`GET /api/demo-inbox/`, `POST /api/applications/` (multipart), `GET /api/applications/<id>/`.
Status is changed live from Django admin for the follow-up demo. Labelled
"Demo portal, not a government website". YojanaSaathi reaches it ONLY via Playwright
(and the status endpoint for polling).

## Repo layout
```
agent/   FastAPI app + LangGraph graph, nodes, tools (Python)
  main.py    FastAPI: /health, /turn (per-case lock)
  graph.py   CaseState + nodes + build_graph(checkpointer)
  gate.py    deterministic yes/no/unclear parser for the confirm pause
  config.py  env settings (loads repo-root .env)
  cli.py     text REPL against /turn (use for kn/hi on Windows instead of curl)
rules/   one JSON file per scheme: rule, required_fields, documents, source_url, effective_date
voice/   Pipecat + Sarvam bot (own venv voice/.venv; separate process on :7860, talks only to /turn)
  bot.py     pipeline + bot() entry for Pipecat's dev runner (webrtc now, twilio in Phase 7)
  bridge.py  AgentBridge: finished turn -> POST /turn with lang -> speak reply
  lang.py    STT code -> lang, short-turn rule, reply script -> TTS language (no Pipecat import)
  smoke.py   live round-trip without a mic (Bulbul REST -> VAD -> Saaras -> /turn)
  tests/     bridge tests (run with the voice venv)
web/     React (Vite) app
tests/   pytest (deterministic parts: gate, rules engine, checklist, /turn contract)
docs/    PROJECT_BRIEF.md
```

## Commands
Dev machine is Windows (PowerShell). Python 3.12.
```powershell
# backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r agent/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn agent.main:app --reload   # http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m pytest -q                          # tests
.\.venv\Scripts\python.exe -m agent.cli my-case                  # chat with /turn by text

# voice (separate venv; agent must be running)
py -3.12 -m venv voice\.venv
.\voice\.venv\Scripts\python.exe -m pip install -r voice/requirements-dev.txt
.\voice\.venv\Scripts\python.exe -m voice.bot -t webrtc            # http://localhost:7860/client
.\voice\.venv\Scripts\python.exe -m voice.smoke [case] ["text"]    # live round-trip, no mic
.\voice\.venv\Scripts\python.exe -m pytest voice/tests -q          # bridge tests

# frontend
cd web; npm install; npm run dev       # http://localhost:5173
```
Secrets live in `.env` (see `.env.example`); never commit keys. `.gitignore` keeps `.env`,
`.venv/`, `node_modules/`, `__pycache__/` and `screenshots/` out of git; `tests/test_gitignore.py`
checks this.

Env vars (all loaded in `agent/config.py`):
| Var | Used by | Notes |
|---|---|---|
| `CORS_ORIGINS` | agent | comma-separated web origins |
| `AGENT_URL` | `agent.cli`, voice | default http://127.0.0.1:8000 |
| `SARVAM_API_KEY` | voice | Saaras STT + Bulbul TTS |
| `VOICE_CASE_ID` | voice | case when the client sends none; default `demo-case-1` (= web app) |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | Phase 2 | config only; no provider package yet |
| `MOCK_PORTAL_URL` | Phase 4 | site Playwright drives (public URL) |
| `MOCK_PORTAL_API` | Phase 4/6 | portal API base for status polling (public URL) |
| `DATABASE_URL`, `MASTER_KEY`, `TWILIO_*` | later phases | |

## Team decisions (2026-10-08)
1. **`lang` on `/turn`:** optional `"kn" | "hi" | "en"`; old clients keep working.
2. **LLM:** provider not chosen. Only config exists (`LLM_PROVIDER`, `LLM_MODEL`,
   `LLM_API_KEY`). Do not install a provider or `langchain-*` package until Fareeha confirms
   (before Phase 2).
3. **Ports:** agent `8000`, our web app `5173`, voice bot `7860` (added Phase 1). The mock portal runs on Ayush's laptop and is
   reached over a **public URL**: never assume localhost for it; always read
   `MOCK_PORTAL_URL` (site) and `MOCK_PORTAL_API` (API) from `.env`.
   Demo-day fallback: portal runs locally on this laptop at `http://127.0.0.1:5174` (site)
   and `http://127.0.0.1:8001/api` (API); just change the two env vars.
4. **Git:** repo initialised on `main`; first commit "Phase 0: foundation".
Approved dependencies beyond the stack: `python-dotenv` (requirements), `pytest` (requirements-dev).
Voice (`voice/requirements.txt`): `pipecat-ai[sarvam,silero,webrtc,runner]==1.12.0` (Pipecat's
own extras: Sarvam services, Silero VAD, SmallWebRTC, dev runner + prebuilt client), `httpx`.

## Gotchas
- **Windows curl + Kannada/Hindi:** `curl.exe` receives arguments in the ANSI code page, so
  non-Latin text (even `\u` escapes typed through some shells) reaches the server as `????`
  and looks like the agent ignored a "ಹೌದು". The agent is fine. Test kn/hi with
  `python -m agent.cli`, pytest, httpx, or the web app; use curl only with ASCII text.
- **PowerShell piping adds a BOM** (U+FEFF) to stdin; `agent/gate.py` drops invisible
  format characters (BOM, ZWJ/ZWNJ) for this reason.
- **Pipecat 1.x frame order:** `SystemFrame`s (UserStopped/StartedSpeaking, interruptions)
  overtake queued data frames (transcripts). That is why `AgentBridge` sits *before* the
  `UserTurnProcessor` (transcripts pass it first) and why `voice/tests` sleep before stop frames.
- **Pipecat TTS without an output transport** never finishes (waits for playback), so
  `EndFrame` hangs. `voice.smoke` synthesises its test clip with Sarvam's REST TTS instead.
- **Windows event loops (Phases 3-4):** async psycopg needs `SelectorEventLoop`, async
  Playwright needs `ProactorEventLoop`, and `uvicorn --reload` can break Playwright
  subprocesses. Decide deliberately when adding Postgres and Playwright.

## Implementation decisions (Phase 0)
- Versions pinned in `agent/requirements.txt` (langgraph 1.2.14, fastapi 0.143.0). Check the
  installed source before using an API; bump deliberately.
- `/turn` uses `graph.invoke(..., version="v2")` -> `GraphOutput(value, interrupts)`; paused
  = `graph.get_state(cfg).interrupts` non-empty (only unanswered interrupts).
- Resume value is always the citizen's raw text. The confirm node parses it with
  `agent.gate.parse_decision` -> yes / no / unclear. **Default-deny:** only an explicit yes
  (no negation word anywhere) reaches `submit`; unclear re-interrupts (`goto="confirm"`).
- `submit` is its own node after `confirm`, so the portal's final Submit is structurally
  unreachable without passing the gate.
- One turn at a time per case (in-process lock in `main.py`).
- `lang` on a paused case is applied with `Command(resume=text, update={"lang": ...})`.
- Phase 4 plan: an `interrupt()` re-runs its node from the top on resume, so the browser
  flow is split (`browser_login -> otp -> browser_fill`) with the Playwright session held
  outside graph state, keyed by case_id.

## Implementation decisions (Phase 1, voice)
- Pipecat 1.12 API (verified from installed source): `PipelineWorker` + `WorkerRunner`
  (`PipelineTask`/`PipelineRunner` are deprecated), VAD is a `VADProcessor`, turn-taking is a
  standalone `UserTurnProcessor`. No LLM in the voice pipeline; the agent is the brain.
- Pipecat's dev runner (`pipecat.runner.run.main`) serves `/client`, `/start`, `/api/offer`; the
  same `bot()` will take `-t twilio` in Phase 7 (`create_transport`).
- STT: `SarvamSTTService` saaras:v4, language auto-detect, `min_speech_frames=1` (default
  dropped a 0.4 s "ಹೌದು"). Turn end: `SpeechTimeoutUserTurnStopStrategy`, 0.8 s, not the
  smart-turn model (not trained on Kannada, extra download).
- `lang` sent to /turn only for kn/hi/en and only for turns of 3+ words: Saaras hears a lone
  "हाँ" as "Yeah." (en-IN). The gate already accepts "yeah" as yes.
- TTS language per reply from its script (`TTSUpdateSettingsFrame` before each `TTSSpeakFrame`).
- Barge-in: VAD start interrupts TTS; a `/turn` reply that arrives after the citizen started
  speaking again is dropped (agent state already advanced; the newer reply wins).
- Bot -> client UI: RTVI server message `{"type": "turn", ..., "reply", "pause"}` per turn.
- Measured (2026-10-09, live Sarvam, stub graph): end of speech -> bot audio ~1.0 s, of which
  0.8 s is the deliberate pause window.

## Build phases (one at a time; stop after each for review)
Each phase: short plan, build, unit tests for deterministic parts, then report what was
built + commands + a hand acceptance test, update this file, and stop.
0. Foundation: layout, env, pinned deps, `/health` + `/turn` on a stub graph (DONE 2026-10-08)
1. Voice layer: Pipecat + Sarvam STT/TTS (browser), POSTs to `/turn` with `lang`, barge-in (DONE 2026-10-09)
2. Agent brain: router, interview, eligibility (JSON Logic), checklist, respond (kn/hi/en)
3. Persistence & security: Postgres checkpointer, tables, `log_event()`, AES-256-GCM vault
4. Browser agent + human gate: planner, Playwright against the mock portal, OTP, safe-stop
5. Web app: the 6 screens + landing page
6. Follow-up: APScheduler polling, follow-up agent, web push, reminders
7. Phone line: Twilio -> Pipecat -> `/turn`; caller number maps to case
8. Evaluation & demo hardening: eval script, seed/reset, README, golden-path checklist

## Conventions
- Python 3.11+, type hints, Pydantic models for every LLM structured output.
- Unit tests for the rules engine and the document mapper (they are deterministic).
- Encode scheme rules only from official pages; record the URL and effective date.
- Prefer small, working increments; the demo golden path matters more than breadth.
