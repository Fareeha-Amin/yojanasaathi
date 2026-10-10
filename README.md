# YojanaSaathi (Team AURA)

A voice-first AI agent that takes an Indian citizen from **"Am I eligible?"** to
**"Application submitted"** for welfare schemes, in **Kannada, Hindi and English**, on the web
or (planned) by phone call. Built for HackerRing 26' (Open Innovation track).

It interviews the citizen, decides eligibility with **deterministic rules**, builds the
document checklist, pre-fills the application on a portal with a **scripted browser agent**,
**stops for the citizen's explicit yes**, submits, then tracks the status and follows up.

> **Demo project, not a government website.** The agent only ever drives the bundled mock
> portal ([ayush81233/mock](https://github.com/ayush81233/mock)); it never touches a real
> government site.

Project rules and every design decision: [`CLAUDE.md`](CLAUDE.md).
Background, MVP features, UI and demo script: [`docs/PROJECT_BRIEF.md`](docs/PROJECT_BRIEF.md).

## What it does

```
citizen (voice / text)                       the one API:  POST /turn/{case_id}
        |                                    { text, lang? } -> { reply, pause, ui, subtitle }
  Web app (React PWA)      Phone (planned)
        \                   /
         Pipecat voice bot (Sarvam Saaras STT / Bulbul TTS)
                   |
        FastAPI + LangGraph agent  ---- PostgreSQL (case memory, audit log, metadata)
          router -> interview -> eligibility -> prepare -> confirm -> submit -> track
                   |                                          ^
        JSON Logic rules (rules/*.json)        human gate: interrupt(), explicit yes only
                   |
        Playwright script  ->  mock government portal  (OTP is always the citizen's)
```

1. **Interview** - extracts age, income and the like from what the citizen says; asks only for
   what the schemes still in play need, one question per turn.
2. **Eligibility** - JSON Logic rules, one file per scheme, three-valued (an unknown field is
   "unknown", not "no"). Every result carries `source_url` and `effective_date`.
3. **Documents** - the checklist uses the portal's own document names; uploads are encrypted.
4. **Pre-fill** - one Playwright script per portal flow: log in, OTP (typed or said by the
   citizen), fill the form, attach documents, screenshot every step, stop before Submit.
5. **Confirm** - the agent reads back names, numbers and amounts; only an explicit yes submits.
   Submission is idempotent per scheme: a repeated "yes" returns the existing application ID.
6. **Track** - a background poller watches the portal; status changes, corrections and expired
   access are spoken at the citizen's next turn (and pushed). Corrections can be re-submitted
   with a fresh yes.

### Design rules that are enforced in code, not by prompts
- **The LLM understands and speaks; code decides and acts.** Eligibility never comes from the LLM.
- **Human gate.** The `confirm` node uses LangGraph `interrupt()`; `submit` is unreachable
  without it. Default-deny: anything but a clear yes re-asks.
- **OTP and CAPTCHA are never bypassed.** The agent never reads SMS or guesses codes.
- **Safe-stop.** A missing or changed portal element stops the run and hands control back.
- **Privacy.** Document images never reach the LLM; Aadhaar is masked to the last 4 digits;
  files are AES-256-GCM encrypted at rest; every consequential action goes into an
  append-only audit log; consent is explicit; "Delete my data" removes the case.

## Schemes (mock portal, all DEMO)
| ID | Scheme | Rule |
|---|---|---|
| `pension-001` | Senior Citizen Pension Scheme | age 60+, income up to Rs 3,00,000 |
| `pension-002` | Social Security Pension Assistance | age 60+ |
| `health-001` | National Health Support Scheme | income up to Rs 5,00,000 |
| `health-002` | Family Healthcare Assistance | income up to Rs 4,00,000 |

`rules/*.json` mirror the portal's seed data; `tests/test_portal_seed.py` fails if they drift.

## Status
| Phase | | State |
|---|---|---|
| 0 | Foundation, `/health` + `/turn` | done |
| 1 | Voice (Pipecat + Sarvam), barge-in | done, hand-verified |
| 2 | Agent brain: router, interview, eligibility, checklist, replies (kn/hi/en) | done, hand-verified |
| 3 | Postgres case memory, audit log, encrypted vault, consent, data rights | done, hand-verified |
| 4 | Browser agent on the mock portal, OTP, safe-stop, human gate | done, hand-verified live |
| 5 | Web app (React PWA, voice + text, JWT sessions) | built; hand acceptance pending |
| 6 | Status tracking, follow-up, web push (backend), renewal, corrections | built; hand acceptance pending |
| 7 | Phone line (Twilio -> Pipecat -> `/turn`) | not started |
| 8 | Evaluation and demo hardening | not started |

Known gaps: the web app does not yet show tracking updates or subscribe to push; the Kannada and
Hindi reply templates and UI strings need native-speaker review.

## Quick start (Windows PowerShell, Python 3.12, Node.js)
You need **PostgreSQL** (an empty database, e.g. `yojanasaathi`) and, for the full flow, the
**mock portal** running and an **Ollama** model (default `qwen3:8b`) or `LLM_PROVIDER=none`
(the golden path is deterministic and runs without an LLM).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r agent/requirements-dev.txt
copy .env.example .env            # then fill in DATABASE_URL, MASTER_KEY and the portal settings
.\.venv\Scripts\python.exe -m uvicorn agent.main:app              # agent on :8000
cd web; npm install; npm run dev                                   # web app on http://localhost:5173
```
Restart the agent after editing `.env`: settings are read at startup. The agent refuses to
start without Postgres or a valid `MASTER_KEY`; there is no in-memory fallback.

`MASTER_KEY` is base64 of 32 random bytes (it wraps every document key; losing it makes stored
documents unreadable). Generate one:
```powershell
.\.venv\Scripts\python.exe -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

### Mock portal
Run [ayush81233/mock](https://github.com/ayush81233/mock) and point `.env` at it:
- `MOCK_PORTAL_URL` - the portal site Playwright drives, `MOCK_PORTAL_API` - its API base
  (must end in `/api`), `MOCK_PORTAL_AGENT_KEY` - the `yjs_ag_...` key (never logged).
- Local demo-day setup: site `http://localhost:5174`, API `http://127.0.0.1:8001/api`.
  **On Windows use `localhost`, not `127.0.0.1`, for the Vite site:** Vite listens on IPv6 only
  and the agent's browser reports "the portal page did not load" otherwise.
- The portal sends a real SMS OTP, only to its registered test mobile. The citizen types or
  says the code; the agent never reads it.
- For a visible fill during a demo: `BROWSER_HEADLESS=false`, `BROWSER_SLOWMO_MS=250`.

### Voice (separate venv, agent must be running)
```powershell
py -3.12 -m venv voice\.venv
.\voice\.venv\Scripts\python.exe -m pip install -r voice/requirements-dev.txt
.\voice\.venv\Scripts\python.exe -m voice.bot -t webrtc            # bot on :7860, test page /client
.\voice\.venv\Scripts\python.exe -m voice.smoke                    # round-trip without a mic
```
Needs `SARVAM_API_KEY`. The web app starts the bot for its own case, so voice and typed text share
one conversation. Details: [`voice/README.md`](voice/README.md).

### Web app (`web/`)
Landing, Talk, Schemes, Documents, Pre-fill + OTP, Review, My applications, Profile (privacy).
Kannada by default; switch to Hindi or English in the header. Installable as a PWA.
Vite proxies `/api` to the agent (:8000) and `/voice` to the bot (:7860).

## The golden path
1. Say or type *"I'm 62, can I get a pension?"* -> the agent asks the income -> *"1 lakh 20 thousand"*.
2. The Schemes screen shows every scheme with the reasons, the rule's source and its effective date.
3. Upload the scheme's documents (consent first), then pick *Senior Citizen Pension Scheme*.
4. Answer the portal's form questions (one per turn), then give the OTP the portal texts you.
5. Review the read-back and the screenshots, edit a value if needed, say **yes**.
6. My applications shows the `YJS-...` application ID; later status changes are spoken to you.

A second "yes", on any channel or in any language, never submits again.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -q                  # backend: needs Postgres, no LLM, no portal
.\.venv\Scripts\python.exe -m agent.privacy_check        # no full Aadhaar / plaintext in DB or vault
.\voice\.venv\Scripts\python.exe -m pytest voice/tests -q
cd web; npm test                                          # component tests (vitest)
cd web; npm run e2e                                       # Playwright: typed golden path + screen screenshots
```
Backend tests use an in-process fake portal (`tests/fake_driver.py`, OTP `123456`) and a throwaway
`<db>_test` database; `npm run e2e` starts its own agent on :8010 with the same fake portal.
After changing `GET /cases/{id}/summary`, regenerate the web fixtures:
`pytest tests/test_web_fixtures.py -q --regen-web-fixtures`.

## Repository layout
```
agent/    FastAPI + LangGraph agent: graph, rules engine, checklist, vault, audit, privacy,
          portal/ (Playwright browser agent), tracking/ (poller, follow-up, push)
rules/    one JSON rule file per scheme (rule, documents, form fields, source_url, effective_date)
voice/    Pipecat + Sarvam bot (own venv); talks only to /turn
web/      React (Vite) PWA, vitest tests, Playwright e2e
tests/    pytest suite (rules, gate, flow, idempotency, persistence, portal, tracking, privacy)
docs/     PROJECT_BRIEF.md, design/
```

## Tech stack
React (Vite) PWA - Pipecat with Sarvam Saaras / Bulbul - LangGraph on FastAPI with a Postgres
checkpointer - JSON Logic eligibility rules - Playwright (scripted, `data-testid` selectors only) -
PostgreSQL - AES-256-GCM document vault - APScheduler - JWT web sessions.
Not used, by decision: Docker, Redis/Celery, RAG / vector DB, OAuth2, real SMS.

Secrets live in `.env` and are never committed; `.env.example` lists every setting.
