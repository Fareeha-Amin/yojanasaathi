# YojanaSaathi (Team AURA)

Voice-first agent that takes citizens from "Am I eligible?" to "Submitted" for welfare
schemes, in Kannada, Hindi and English, on the web or by phone call.

Project rules and decisions: `CLAUDE.md`. Background, MVP features, UI and demo: `docs/PROJECT_BRIEF.md`.

## Run the agent (Windows PowerShell)
Needs PostgreSQL (17 tested) with an empty database, e.g. `yojanasaathi`; set `DATABASE_URL`
and `MASTER_KEY` in `.env` (see `.env.example`). The agent creates its tables at startup and
refuses to start without Postgres. Tests create and drop their own `<db>_test` database.
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r agent/requirements-dev.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn agent.main:app --reload   # backend on :8000
.\.venv\Scripts\python.exe -m pytest -q                          # tests (need Postgres)
.\.venv\Scripts\python.exe -m agent.privacy_check                # no full Aadhaar / documents in DB
.\.venv\Scripts\python.exe -m agent.cli                          # text chat (kn/hi safe)

cd web; npm install; npm run dev                                 # web on :5173
```
(macOS/Linux: `source .venv/bin/activate`, then `pip`, `uvicorn`, `pytest` directly.)

## Voice (Pipecat + Sarvam, own venv)
```powershell
py -3.12 -m venv voice\.venv
.\voice\.venv\Scripts\python.exe -m pip install -r voice/requirements-dev.txt
.\voice\.venv\Scripts\python.exe -m voice.bot -t webrtc          # open http://localhost:7860/client
.\voice\.venv\Scripts\python.exe -m voice.smoke                  # round-trip test, no mic
.\voice\.venv\Scripts\python.exe -m pytest voice/tests -q        # voice bridge tests
```
Needs `SARVAM_API_KEY` in `.env` and the agent running. Details: `voice/README.md`.

Type "I'm 62, can I get a pension?" -> the agent pauses for confirmation -> say/tap Yes
(ಹೌದು / हाँ) -> "Submitted!". Anything that isn't a clear yes or no re-asks; nothing submits.
