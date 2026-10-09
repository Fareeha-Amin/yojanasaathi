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

## Team ownership (changed 2026-10-09)
- Fareeha: everything in this repo (agent, voice, web app, phone line). Changes to this
  repo, including the `/turn` contract, need no "tell the team" step.
- Ayush: only the mock government portal, separate repo `ayush81233/mock` (OTP +
  application flow). Anything we need from the portal is a request to him.

## Non-negotiable design rules
1. **The LLM understands and speaks; code decides and acts.** The LLM extracts profile
   facts, routes intent and explains results. Eligibility is decided by JSON Logic rules,
   never by the LLM. Every eligibility result carries `source_url` and `effective_date`.
2. **Human gate in code.** The `confirm` node calls LangGraph `interrupt()`. Nothing may
   click the portal's final Submit until the graph is resumed with an explicit approval.
   **Submission is idempotent per scheme.** Once a scheme has an application ID in the
   case's `applications`, nothing submits that scheme again: a request to submit it again
   (a repeated "yes" on any channel / in any language, or naming that scheme) gets the
   existing ID back ("Already submitted. Your application ID is ..."), with no new confirm
   pause. Everything else routes normally (questions, status, other eligible schemes).
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
  response: { "reply": "...", "pause": null | { "type": "confirm" | "otp" | "safe_stop", ...data },
              "ui": null | { "type": "eligibility" | "submitted", ...data } }
```
If the graph is paused, the incoming text resumes it (`Command(resume=text)`).
`lang` is optional (added 2026-10-08, decision 1 below): voice sends the STT-detected
language; when present it updates the case's `lang`, when absent the case keeps its
previous `lang`. Any other value is rejected with 422. Old clients sending only `text` work.
`ui` is optional output (added 2026-10-09, Fareeha): `reply` is what gets spoken (1-3 short
sentences); `ui` is this turn's screen payload (every scheme with status, full reasons,
source_url, effective_date, checklist with the portal's labels; or the submitted app ID and
next schemes). Clients that read only reply/pause keep working; the voice bridge forwards
`ui` in its RTVI `turn` message. Keep the shape backward compatible (all clients are in this
repo; the portal never calls it).

## Agent graph (target)
Nodes: router -> interview -> eligibility -> document -> respond;
proceed: planner -> browser -> confirm -> submit -> track. Follow-up agent is
triggered by the scheduler on status change. Shared state: `lang`, `profile`,
`missing`, `eligible`, `checklist`, `preview`, `app_id`, `status`, `reply`.
`interview` asks only for fields in `missing` = union of `required_fields` of schemes
still possible, minus fields already known.

## Mock portal (separate repo: github.com/ayush81233/mock)
Django + DRF + React. Labelled "Demo portal, not a government website". YojanaSaathi
reaches it ONLY via Playwright (and the status endpoint for polling). Read from the code at
commit 6e6e24d (2026-10-09):
- **Schemes:** exactly 4, seeded by `backend/schemes/management/commands/seed_schemes.py`,
  the source of truth for `rules/*.json` (see "Schemes" below). Frontend route
  `/schemes/:id`, so `source_url` = `{MOCK_PORTAL_URL}/schemes/<id>`.
- **API:** `POST /api/auth/request-otp/`, `POST /api/auth/verify-otp/` (returns a DRF token),
  `GET /api/schemes/`, `GET /api/schemes/<id>/`, `POST /api/applications/`,
  `GET /api/applications/mine/`, `GET|.. /api/applications/<application_number>/`,
  `.../submit/`, `.../documents/`, `.../pdf/`; `/api/notifications/`.
- **Auth:** the application endpoints need the citizen's token (`TokenAuthentication`,
  `Authorization: Token <key>`), which is issued only by OTP login. Phase 4/6 must hold it
  per case (outside graph state) and never log it.
- **OTP:** real Twilio Verify SMS, and only to the registered test mobile(s) in the portal's
  `TWILIO_ALLOWED_MOBILE`; any other number is refused. The citizen reads the code out (rule 3).
- **Application number:** `YJS-` + 10 uppercase hex chars (e.g. `YJS-07531B2688`).
- **Statuses:** `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `REJECTED`,
  `CORRECTION_REQUIRED` (changed live in Django admin for the follow-up demo). Our
  `applications[scheme]["status"]` uses these codes; spoken labels are `status_<CODE>` templates.

## Repo layout
```
agent/   FastAPI app + LangGraph graph, nodes, tools (Python)
  main.py    FastAPI: /health, /turn (per-case lock), /cases/{id}/consent|documents|data
  graph.py   CaseState + nodes + build_graph(checkpointer)
  db.py      Postgres pool + PostgresSaver (case memory) + table queries; StartupError
  schema.sql our tables (applied at every start): citizens, profiles, cases, case_events,
             documents (metadata), audit_log (append-only trigger)
  audit.py   log_event(): every consequential action -> audit_log (case = thread ID)
  vault.py   AES-256-GCM document vault on disk; per-file key wrapped with MASTER_KEY
  privacy.py Aadhaar masking (before the graph) + scrub() for audit details
  privacy_check.py  scan DB (incl. checkpoint blobs) + vault for full Aadhaar / plaintext
  gate.py    deterministic yes/no/unclear parser for the confirm pause
  numbers.py number words kn/hi/en -> int (digits, lakh/saavira/hazaar, fused Kannada)
  facts.py   deterministic facts per message (numbers -> age/income, district, scheme choice)
  districts.py  district lookup -> canonical English name (31 Karnataka districts)
  rules.py   three-valued JSON Logic evaluator + scheme loader (eligibility decided here)
  checklist.py  document mapper (required docs minus what the citizen has)
  replies.py + i18n/{en,kn,hi}.json   reviewed reply templates (key replies never from LLM)
  llm.py     provider factory (.env), structured extraction, free-form answers, warm-up
  portal_seed.py  reads the mock portal's seed_schemes.py (ast, never executed)
  config.py  env settings (loads repo-root .env)
  cli.py     text REPL against /turn (use for kn/hi on Windows instead of curl); [case] [lang]
rules/   one JSON file per mock-portal scheme (portal IDs): rule, required_fields, documents
         (portal labels kn/hi/en), application_fields, titles, topic, priority, aliases,
         portal_rules, source_url, effective_date, verification (all "DEMO ...")
voice/   Pipecat + Sarvam bot (own venv voice/.venv; separate process on :7860, talks only to /turn)
  bot.py     pipeline + bot() entry for Pipecat's dev runner (webrtc now, twilio in Phase 7)
  bridge.py  AgentBridge: finished turn -> POST /turn with lang -> speak reply
  lang.py    STT code -> lang, short-turn rule, reply script -> TTS language (no Pipecat import)
  smoke.py   live round-trip without a mic (Bulbul REST -> VAD -> Saaras -> /turn)
  tests/     bridge tests (run with the voice venv)
web/     React (Vite) app
tests/   pytest (deterministic parts: gate, numbers, facts, rules, checklist, replies, flow,
         /turn contract, per-scheme idempotency, portal seed sync, persistence + restart,
         vault, privacy/consent/data rights); conftest.py gives every test a FakeLLM and
         points the agent at a fresh <db>_test database; test_llm_live.py is opt-in;
         fixtures/portal_seed.json = seed snapshot
data/    (gitignored) data/vault/*.ysv = encrypted documents
docs/    PROJECT_BRIEF.md
```

## Commands
Dev machine is Windows (PowerShell). Python 3.12.
```powershell
# backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r agent/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn agent.main:app --reload   # http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m pytest -q                          # tests (no LLM; need Postgres)
.\.venv\Scripts\python.exe -m agent.cli my-case kn               # chat with /turn by text
.\.venv\Scripts\python.exe -m agent.privacy_check                # DB + vault: no full Aadhaar / plaintext
$env:RUN_LIVE_LLM="1"; .\.venv\Scripts\python.exe -m pytest tests/test_llm_live.py -q -s  # live Ollama
$env:MOCK_PORTAL_REPO="C:\path\to\mock"; .\.venv\Scripts\python.exe -m pytest tests/test_portal_seed.py -q  # vs live seed
.\.venv\Scripts\python.exe -m agent.portal_seed <mock>\backend\schemes\management\commands\seed_schemes.py  # snapshot

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
| `LLM_PROVIDER`, `LLM_MODEL` | agent | `ollama` + `qwen3:8b` (local); `openai` / `anthropic` / `none` |
| `LLM_API_KEY`, `LLM_BASE_URL` | agent | cloud providers only (`LLM_BASE_URL` = any OpenAI-compatible API) |
| `OLLAMA_BASE_URL` | agent | default http://localhost:11434 |
| `LLM_TIMEOUT` | agent | seconds per LLM call (default 20), then deterministic fallback |
| `LLM_WARMUP`, `LLM_KEEPWARM` | agent | load model at startup (1); 1-token ping every N s (2 for ollama, 0 = off) |
| `MOCK_PORTAL_URL` | agent, Phase 4 | site Playwright drives (public URL); fills `{MOCK_PORTAL_URL}` in rules' `source_url` |
| `MOCK_PORTAL_API` | Phase 4/6 | portal API base for status polling (public URL) |
| `MOCK_PORTAL_REPO` | tests | optional local clone of the portal; seed-sync test also checks it |
| `DATABASE_URL` | agent | required; startup fails clearly without it (no in-memory fallback) |
| `TEST_DATABASE_URL` | tests | default: `DATABASE_URL` db name + `_test` (dropped + created per run) |
| `MASTER_KEY` | agent | required; base64 of 32 bytes; wraps each document key. Lose it = documents unreadable |
| `VAULT_DIR` | agent | encrypted files, default `data/vault` |
| `DOC_RETENTION_HOURS`, `VAULT_PURGE_SECONDS` | agent | documents deleted N h after the case's latest submission (24); purge every 60 s |
| `DOC_MAX_BYTES` | agent | upload limit (10 MB) |
| `TWILIO_*` | Phase 7 | |

## Team decisions (2026-10-08)
1. **`lang` on `/turn`:** optional `"kn" | "hi" | "en"`; old clients keep working.
2. **LLM:** Ollama, `qwen3:8b`, local (decided 2026-10-09 by Fareeha); `langchain-ollama==1.1.0`
   pinned. Provider is switchable in `.env`; a cloud provider also needs its `langchain-*`
   package installed once (not installed; ask before adding).
3. **Ports:** agent `8000`, our web app `5173`, voice bot `7860` (added Phase 1). The mock portal runs on Ayush's laptop and is
   reached over a **public URL**: never assume localhost for it; always read
   `MOCK_PORTAL_URL` (site) and `MOCK_PORTAL_API` (API) from `.env`.
   Demo-day fallback: portal runs locally on this laptop at `http://127.0.0.1:5174` (site)
   and `http://127.0.0.1:8001/api` (API); just change the two env vars.
4. **Git:** repo initialised on `main`; first commit "Phase 0: foundation".
Approved dependencies beyond the stack: `python-dotenv` (requirements), `pytest` (requirements-dev),
`langchain-ollama` (+ its `ollama`, `langchain-core`; Phase 2). No JSON Logic package: own evaluator.
Phase 3 (planned in the stack): `langgraph-checkpoint-postgres==3.1.2`, `psycopg[binary]==3.3.6`,
`psycopg-pool==3.3.3`, `cryptography==50.0.2`. No `python-multipart`: uploads are raw request bodies.
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
- **Demo laptop power:** keep it plugged in with Windows power mode "Best performance". The
  GPU's low-power state is what causes the ~3 s LLM latency after a pause (Phase 2 latency notes).
- **Windows event loops (Phases 3-4):** async psycopg needs `SelectorEventLoop`, async
  Playwright needs `ProactorEventLoop`, and `uvicorn --reload` can break Playwright
  subprocesses. Phase 3 decision: **sync** psycopg + sync `PostgresSaver` (no event loop
  involved), so the default Proactor loop stays free for Playwright. Never switch the
  checkpointer to `AsyncPostgresSaver` on Windows. `--reload` vs Playwright: Phase 4.
- **Test DB:** pytest drops and recreates `<db>_test` (only names ending `_test`; see
  `db.recreate_database`). Close DBeaver's connection to it if a test run hangs on DROP
  (it uses `WITH (FORCE)`, so it shouldn't).
- **Masking regex:** any 12-digit run (4-4-4, contiguous; ASCII/Kannada/Devanagari digits)
  is an "Aadhaar", except `+`-prefixed (`+91` phone numbers). Over-masking is intended.

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
- **Acceptance test VERIFIED 2026-10-09** by Fareeha with a real mic + headphones, all 6 steps:
  greeting, Kannada line -> reply, barge-in stops audio, "ಹೌದು" submits, cross-channel case
  sharing (voice pause, text "yes"), spoken "service not available" when the agent is down.

## Idempotent submission (fixed 2026-10-09, found in the Phase 1 acceptance test)
- Bug: after a submit, the next message restarted the graph (interview -> new confirm
  pause), so a second "yes" ran `submit` again. Same ID only because the stub hardcodes it;
  in Phase 4 it would have been a second portal submission.
- First fix (`route_entry`: any case with `app_id` -> `already_submitted`) turned every later
  message into "already submitted", a dead end once a citizen has several eligible schemes.
- **Now per scheme (2026-10-09):** `applications` = {scheme_id: {"app_id", "status"}}. The
  router sends to `already_submitted` only (a) a message naming a scheme that is in
  `applications`, or (b) a bare yes (`parse_decision` == yes, no new facts, no scheme picked)
  after a submission, answered with `last_submitted`'s ID. Everything else routes normally.
  After a submit the agent names the next eligible scheme and asks the citizen to *say its
  name* (not "yes"), so a stray repeated "yes" can never start a new application.
  `prepare` and `submit` both refuse a scheme already in `applications` (defence in depth).
  The portal call is `_submit_to_portal()` (Phase 4 puts Playwright there; tests count calls).
- The application ID is the marker, not `status` (status changes in Phase 6).
- Cancelled cases ("no") can still start again; nothing was submitted.
- Tests: `tests/test_idempotent_submit.py`: after submitting pension-001, (a) "yes" again in
  every language / naming it -> existing ID, no submit; (b) questions and status answered
  normally; (c) choosing pension-002 (by name or "next") starts its flow; (d) exactly one
  portal submission per scheme, also across voice + text; submit-node guard.

## Implementation decisions (Phase 2, agent brain)
- **Graph:** `START -> router` -> `interview` -> `eligibility`; `respond` (free-form), `status`,
  `declined`, `choose_reask`, `already_submitted`; proceed: `eligibility` -> `prepare`
  (read-back preview; Phase 4 puts planner + browser here) -> `confirm` -> `submit`. The
  document checklist is built per scheme inside `eligibility` / `prepare` (no separate node).
  Choosing a scheme only reaches `prepare`; the gate still needs its own explicit yes.
- **Interview scope:** questions only for the schemes of the topic the citizen named
  (pension / health; none named = all). `missing` = required_fields of in-scope schemes still
  UNKNOWN, in `FIELD_ORDER` (age, annual_income); one question per turn.
- **Matching:** eligibility is then evaluated for ALL 4 schemes, ordered topic first, then
  `priority` (pension-001 = 1). Several matches: speak the count, the one-line reason and the
  top 2 titles, ask "which one?" (`asking="choose"`). One match: title, reason, number of
  documents, "shall I start?". A scheme is picked by name (`aliases` + titles in kn/hi/en),
  by position ("ಮೊದಲನೆಯದು", "दूसरा", "second"), by "next", or by the LLM's `scheme` field
  as a fallback; a bare "yes" at "which one?" asks for a name. No match in the named topic:
  say why and offer to check the other schemes (`asking="others"`).
- **Rules engine:** own JSON Logic evaluator with Kleene logic (missing var = UNKNOWN, not
  false), so "age 55" decides the pension without asking income. Operators are whitelisted.
- **Schemes (changed 2026-10-09): only the 4 mock-portal schemes, all DEMO.** `rules/*.json`
  mirror the portal seed (commit 6e6e24d): pension-001 (min_age 60, max_income 300000),
  pension-002 (min_age 60), health-001 (max_income 500000), health-002 (max_income 400000);
  titles, document names and application-field labels are the portal's own kn/hi/en text.
  `tests/test_portal_seed.py` fails if a rules file disagrees with the seed snapshot (or a
  live clone via `MOCK_PORTAL_REPO`). The seed has no effective date: `effective_date` is the
  seed commit date. The earlier pm-kisan / post-matric-sc / gruha-lakshmi files were removed.
- **Numbers never from the LLM converting words.** Order: STT digits, then `agent/numbers.py`
  (kn/hi/en number words), then the LLM value as a fallback, flagged (`readback`,
  `preview.needs_readback`, amber at review). If the parser read a number the LLM placed,
  the parser's value is kept. Measured with live Sarvam: Saaras writes ages as digits but
  kept "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ ರೂಪಾಯಿ" as words, so the parser is on the golden path.
- **Attribution:** strong money (multiplier, >= 1000, ₹/rupees adjacent) > age cue (ವರ್ಷ /
  साल / years / "I'm 62") > money word nearby (values >= 100 only). Per month -> x12, flagged.
- **LLM calls:** one structured extraction per turn (`Extraction`, JSON schema via Ollama
  `format`, temperature 0, few-shot kn/hi/en, fixed prefix for the prompt cache, thinking off
  via `reasoning=False` -> `think: false`). Skipped for short answers the parsers understood.
  Deterministic facts always win. Any LLM failure/timeout -> carry on without it;
  `LLM_PROVIDER=none` runs the golden path with no LLM at all.
- **Replies:** key replies are templates in `agent/i18n/{en,kn,hi}.json` (tests check same
  keys and placeholders). **The kn and hi files NEED NATIVE-SPEAKER REVIEW** (marked in
  `_review`). Scheme titles and document names are NOT in the templates: they are the
  portal's wording from `rules/*.json`, so both sides say the same thing. Spoken replies are
  1-3 short sentences; full reasons, source and checklist go to `ui`. The LLM writes only
  `respond` answers (general questions), then the pending question is asked again.
- **Language:** `lang` from the client wins; if the case has none, the message script
  decides (Kannada / Devanagari); Latin text keeps the case's lang (default English).
- **Latency (measured 2026-10-09, RTX 3070 Ti laptop, Ollama 0.34):** warm extraction
  ~1.05 s; free-form answer ~3.4 s; answer turns parsed deterministically ~0.03 s. After >= 10 s
  idle a call took ~3.3 s (GPU wake, outside model compute). `LLM_KEEPWARM=2` brought 5 of 6
  post-idle turns to 1.1-1.4 s; one was still 3.4 s. Model stays loaded (`keep_alive=-1`),
  warm-up runs in a background thread at startup; `/health` reports `llm` state and `/turn`
  skips the LLM while it is still `warming`.
- **Contract:** `/turn` gained the optional `ui` output (see "The one API contract"); the
  confirm `preview` also carries fields, documents, source_url and effective_date.
- Known gap: edits at the confirm pause ("no, my income is ...") cancel instead of editing
  (Phase 4 review screen).
- **Acceptance VERIFIED 2026-10-09** by Fareeha: voice test (all 5 steps: Kannada pension
  line -> income question -> eligible with reason + source -> read-back + confirm -> "ಹೌದು"
  submits -> repeated "ಹೌದು" gets the same ID) and the `agent.cli` text version. That run was
  on the single-match flow; the 4-match / per-scheme flow above needs its own run.

## Implementation decisions (Phase 3, persistence & security)
- **Case memory = `PostgresSaver`** on a sync psycopg pool (`agent/db.py`), thread ID =
  case ID. A confirm pause and `applications` survive restarts, so per-scheme idempotency
  holds after a restart. `/turn` invokes with `durability="sync"` (each step's checkpoint
  is written before the next step). Verified: a hard-killed uvicorn at the confirm pause,
  restarted, "ಹೌದು" submits once, the next "ಹೌದು" returns the same ID
  (`tests/test_persistence.py` does it with 3 separate processes).
- **Startup fails clearly** (`StartupError`, password redacted) if `DATABASE_URL` is
  missing/wrong, Postgres is down, the database doesn't exist, tables can't be created, or
  `MASTER_KEY` is missing / not 32 bytes. Never an in-memory fallback.
- **Tables** (`agent/schema.sql`, idempotent, applied at every start): `citizens` (one per
  case for now; `phone_hash`/`phone_last4` reserved for Phase 7), `profiles` (consent
  flags; a CHECK makes the DB refuse profile data without `consent_profile`), `cases`
  (readable projection updated after every turn: lang, status, selected_scheme, paused,
  applications; the graph state stays the source of truth), `case_events` (timeline: one
  `turn` row per turn with intent/lang/status/pause, `submitted` rows; never message
  text), `documents` (metadata only), `audit_log`. No scheme tables: schemes stay in
  `rules/*.json` (portal seed is the source of truth).
- **Audit:** `agent.audit.log_event(actor, action, case_id?, scheme_id?, detail?)`; nodes
  get the case ID from the LangGraph run (`get_config()`). Append-only by trigger (UPDATE,
  DELETE, TRUNCATE raise). Details hold field names and IDs, never personal values,
  because audit rows outlive "delete my data" (no FK to cases). Actions so far:
  agent_started, case_opened, aadhaar_masked, eligibility_decided (when the decision
  changes; field names + rule effective dates), confirm_requested (logged in `prepare`,
  since an interrupted node re-runs on resume), citizen_approved / citizen_declined /
  confirm_unclear (with the bare word said, if <= 3 tokens), submitted, resubmit_blocked
  (guard: router / prepare / submit), consent_changed, profile_saved, document_stored /
  document_read / document_deleted / document_auto_deleted, documents_expiry_set,
  data_viewed, data_deleted. A failed audit write fails the action (no unaudited submit).
  Production: connect as a role with INSERT-only on audit_log (the superuser can drop the
  trigger).
- **Aadhaar:** `/turn` replaces any Aadhaar-like number with "[Aadhaar number hidden]"
  (no digits, so the number parsers can't read the last 4 as income) before the graph,
  LLM, checkpoint or logs see it; the last 4 go to an `aadhaar_masked` audit row. The
  voice bridge masks its transcript log line and the RTVI echo the same way. Documents
  take `?aadhaar_last4=1234` only (pattern-checked; full numbers are rejected with 422).
  Not covered: an Aadhaar read out as separate number words that STT leaves as words.
- **Vault** (`agent/vault.py`): per-file random AES-256-GCM key, wrapped with MASTER_KEY
  and kept in the file header (`YSV1 | nonce | wrapped key | nonce | ciphertext`), AAD =
  magic + storage key. Postgres holds type, owner, sha256 of plaintext, storage key,
  size, aadhaar_last4, expiry. One document per (case, type); re-upload replaces. Expiry =
  `DOC_RETENTION_HOURS` after the case's latest submission (set in `record_turn`);
  `purge_expired()` runs at startup and every `VAULT_PURGE_SECONDS` in a daemon thread
  (moves to APScheduler in Phase 6) and also removes orphan files. Uploaded types show as
  `"uploaded"` in the checklist (`/turn` passes `docs_stored`). Nothing in the vault path
  calls the LLM.
- **Consent:** `PUT /cases/{id}/consent {"profile"?: bool, "documents"?: bool}`. Profile
  consent copies the case's profile into `profiles.data` and keeps it in sync each turn;
  withdrawing empties it. Document uploads need document consent (403 otherwise);
  withdrawing deletes the documents. Case memory (the checkpoint) is not the "saved
  profile": it is what the open case needs, and is removed by "delete my data".
- **Data rights:** `GET /cases/{id}/data` (case row, consent, saved profile, case memory,
  document metadata, timeline, audit rows; logged), `DELETE /cases/{id}/data` (documents,
  timeline, case, profile, citizen if no other case, checkpoints; audit keeps a
  `data_deleted` row). `GET /cases/{id}/documents`, `PUT|DELETE
  /cases/{id}/documents/...` (raw body, jpeg/png/webp/pdf, 10 MB).
- **Not done in Phase 3 (named):** JWT. These endpoints are keyed by case ID, the same trust
  level as `/turn`. JWT comes with the web app login (Phase 5); until then there is
  deliberately no endpoint that returns document contents. Spoken consent question:
  deferred, because a bare "yes" after a submission is reserved for idempotency. Consent is
  given in the UI/API for now. Multi-process: the per-case lock is in-process (one agent
  process); a second process would need `pg_advisory_xact_lock`.
- **Latency:** deterministic turns via uvicorn + local Postgres 16-80 ms (was ~30 ms in
  memory). Measure with a persistent HTTP client: a fresh `httpx.post()` per call costs
  ~250 ms of client SSL setup on Windows.

## Notes for Phase 4 (browser agent) and Phase 6 (follow-up); not built yet
- **Crash window at submit (Phase 4):** with `durability="sync"` the only gap is inside
  the submit node, between the portal's Submit click and the checkpoint write. Before the
  real portal call, record a write-ahead marker (e.g. a `submitting` case_event /
  `cases.applications[sid] = {"status": "SUBMITTING"}`) and, on finding one, check the
  portal's `GET /api/applications/mine/` instead of submitting again (safe-stop if unsure).
- **After "delete my data"** our memory of an application ID is gone (the audit row
  keeps it). Phase 4 must check `GET /api/applications/mine/` for the scheme before
  submitting, so a deleted-and-restarted case can't apply twice.
- **Phone line (Phase 7):** do NOT use the caller number as the case ID (it would sit in
  checkpoint thread IDs and in audit rows that outlive deletion). Map
  HMAC(MASTER_KEY-derived key, number) -> `citizens.phone_hash` -> that citizen's open case.
- **Documents to the portal (Phase 4):** `vault.read(case_id, doc_id, actor="browser_agent")`
  decrypts to memory; upload with Playwright, drop the bytes; never write plaintext to disk.
- **Application fields:** each rules file has the scheme's `application_fields` from the
  portal seed (name, type, required, options, label kn/hi/en). After the citizen picks a
  scheme, the agent asks ONLY the required fields still missing from the profile, one per
  turn, read back before submit. pension-001: full_name, dob, gender, marital_status,
  annual_income, disbursement_mode, bank_account_number, bank_ifsc, nominee_name, address,
  declaration_consent. pension-002: full_name, dob, gender, assistance_category,
  annual_income, disability_details (optional), bank_account_number, bank_ifsc, address,
  declaration_consent. health-001: full_name, dob, gender, annual_income, hospital_name,
  medical_condition, bank_account_number, bank_ifsc, address, declaration_consent.
  health-002: applicant_name, dob, gender, family_members_count, ration_card_category,
  annual_income, coverage_preference, bank_account_number, bank_ifsc, address,
  declaration_consent. Select/radio options must map to the portal's exact English option.
- **`declaration_consent` is a legal affirmation** (e.g. pension-001: "I solemnly affirm that
  I am not drawing any other central/state government pension."). The agent must read the
  portal's label out in the citizen's language and get an explicit yes for it (deterministic
  gate, like confirm); it never ticks the box by itself, and the review screen shows it.
- **Application numbers** look like `YJS-XXXXXXXXXX`; store them in `applications`.
- **Statuses** (Phase 6 polling): DRAFT, SUBMITTED, UNDER_REVIEW, APPROVED, REJECTED,
  CORRECTION_REQUIRED; templates `status_<CODE>` exist in kn/hi/en.
- **OTP** is a real Twilio Verify SMS, only to the portal's registered test mobile; the
  citizen reads the code out (`interrupt({"type": "otp"})`); never read SMS.
- **Auth:** the portal's application endpoints need the citizen's token from OTP login;
  keep it per case outside graph state, never log it.

## Build phases (one at a time; stop after each for review)
Each phase: short plan, build, unit tests for deterministic parts, then report what was
built + commands + a hand acceptance test, update this file, and stop.
0. Foundation: layout, env, pinned deps, `/health` + `/turn` on a stub graph (DONE 2026-10-08)
1. Voice layer: Pipecat + Sarvam STT/TTS (browser), POSTs to `/turn` with `lang`, barge-in (DONE 2026-10-09, acceptance verified)
2. Agent brain: router, interview, eligibility (JSON Logic), checklist, respond (kn/hi/en) (DONE 2026-10-09, voice + text acceptance verified; then changed to the 4 portal schemes, multiple matches, per-scheme idempotency, short replies + `ui`)
3. Persistence & security: Postgres checkpointer, tables, `log_event()`, AES-256-GCM vault (BUILT 2026-10-09; restart verified with real processes; voice acceptance pending)
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
