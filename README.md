# YojanaSaathi (Team AURA)

Voice-first agent that takes citizens from "Am I eligible?" to "Submitted" for welfare
schemes, in Kannada, Hindi and English, on the web or by phone call.

Project rules and decisions: `CLAUDE.md`. Background, MVP features, UI and demo: `docs/PROJECT_BRIEF.md`.

## Run the skeleton (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r agent/requirements-dev.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn agent.main:app --reload   # backend on :8000
.\.venv\Scripts\python.exe -m pytest -q                          # tests
.\.venv\Scripts\python.exe -m agent.cli                          # text chat (kn/hi safe)

cd web; npm install; npm run dev                                 # web on :5173
```
(macOS/Linux: `source .venv/bin/activate`, then `pip`, `uvicorn`, `pytest` directly.)

Type "I'm 62, can I get a pension?" -> the agent pauses for confirmation -> say/tap Yes
(ಹೌದು / हाँ) -> "Submitted!". Anything that isn't a clear yes or no re-asks; nothing submits.
