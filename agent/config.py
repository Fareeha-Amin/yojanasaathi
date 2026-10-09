"""Settings read from the environment (.env in the repo root). Never hardcode secrets."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _opt(name: str) -> str | None:
    return os.getenv(name) or None


CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]

# LLM (Phase 2). Switch provider here, not in code (agent/llm.py: make_chat_model).
#   ollama (default, local) | openai (or any OpenAI-compatible API via LLM_BASE_URL)
#   | anthropic | none (deterministic parsers only; the golden path still works)
LLM_PROVIDER = (_opt("LLM_PROVIDER") or "none").lower()
LLM_MODEL = _opt("LLM_MODEL")
LLM_API_KEY = _opt("LLM_API_KEY")
LLM_BASE_URL = _opt("LLM_BASE_URL")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))  # seconds per call; then fall back
LLM_WARMUP = os.getenv("LLM_WARMUP", "1") not in ("0", "false", "no")  # load model at startup
# Local GPU only: ping the model every N seconds so the GPU doesn't idle down (0 = off).
LLM_KEEPWARM = float(os.getenv("LLM_KEEPWARM", "2" if LLM_PROVIDER == "ollama" else "0"))

# Persistence & security (Phase 3). Both are required: the agent refuses to start without
# them (agent/db.py open_store); it never falls back to in-memory case memory.
DATABASE_URL = _opt("DATABASE_URL")
# Tests use their own database (created and dropped by tests/conftest.py); default:
# DATABASE_URL with "_test" appended to the database name.
TEST_DATABASE_URL = _opt("TEST_DATABASE_URL")
MASTER_KEY = _opt("MASTER_KEY")  # base64 of 32 bytes; wraps each document's own key
VAULT_DIR = Path(os.getenv("VAULT_DIR") or ROOT / "data" / "vault")  # encrypted files (gitignored)
# Documents are deleted this many hours after the case's latest submission.
DOC_RETENTION_HOURS = float(os.getenv("DOC_RETENTION_HOURS", "24"))
VAULT_PURGE_SECONDS = float(os.getenv("VAULT_PURGE_SECONDS", "60"))  # how often expiry is checked
DOC_MAX_BYTES = int(os.getenv("DOC_MAX_BYTES", str(10 * 1024 * 1024)))

# Web app session tokens (Phase 5, agent/auth.py; signing key derived from MASTER_KEY).
SESSION_TTL_HOURS = float(os.getenv("SESSION_TTL_HOURS", str(24 * 30)))
# POST /tts (agent/tts.py): Sarvam Bulbul for read-aloud when the voice bot isn't connected.
# Same key as the voice bot. Unset: /tts answers 503 and the web app falls back.
SARVAM_API_KEY = _opt("SARVAM_API_KEY")
TTS_CACHE_SIZE = int(os.getenv("TTS_CACHE_SIZE", "128"))

def _url(name: str) -> str | None:
    """A URL setting without surrounding spaces or a trailing slash."""
    v = (os.getenv(name) or "").strip().rstrip("/")
    return v or None


# Mock portal (Phase 4). Public URLs (Render / Netlify); never assume localhost.
MOCK_PORTAL_URL = _url("MOCK_PORTAL_URL")  # the site Playwright drives
MOCK_PORTAL_API = _url("MOCK_PORTAL_API")  # API base, ends with /api
MOCK_PORTAL_AGENT_KEY = (os.getenv("MOCK_PORTAL_AGENT_KEY") or "").strip() or None  # yjs_ag_..., secret
# The API sleeps when idle (Render): the first call may take this long, later ones less.
PORTAL_FIRST_TIMEOUT = float(os.getenv("PORTAL_FIRST_TIMEOUT", "90"))
PORTAL_TIMEOUT = float(os.getenv("PORTAL_TIMEOUT", "20"))

# Browser agent (Phase 4): Playwright Chromium in its own thread. Demo: headless false,
# slow-mo 250 ms so the audience sees the form being filled.
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").strip().lower() not in ("0", "false", "no")
BROWSER_SLOWMO_MS = float(os.getenv("BROWSER_SLOWMO_MS", "0"))
BROWSER_IDLE_SECONDS = float(os.getenv("BROWSER_IDLE_SECONDS", "900"))  # a case's session closes after
OTP_TTL_SECONDS = float(os.getenv("OTP_TTL_SECONDS", "600"))  # Twilio Verify codes last 10 minutes


def portal_url_warnings() -> list[str]:
    """What is wrong with the portal settings (logged at agent start). Values are never
    echoed: a key pasted into the wrong variable must not reach the logs."""
    import re

    out = []
    for name in ("MOCK_PORTAL_URL", "MOCK_PORTAL_API"):
        v = globals()[name]
        if not v:
            out.append(f"{name} is not set: scheme source links and the portal won't work (.env)")
        elif "<" in v or ">" in v:
            out.append(f"{name} still holds a placeholder (<...>): set the portal's public URL in .env")
        elif not re.fullmatch(r"https?://[^\s<>{}\"']+", v):
            out.append(f"{name} is not an http(s) URL: expected e.g. https://portal.example.org"
                       + ("/api" if name.endswith("API") else ""))
        elif name == "MOCK_PORTAL_API" and not v.endswith("/api"):
            out.append("MOCK_PORTAL_API must end with /api (the portal's API base, e.g. "
                       "https://portal.example.org/api)")
    key = MOCK_PORTAL_AGENT_KEY
    if not key:
        out.append("MOCK_PORTAL_AGENT_KEY is not set: the agent can't read the portal's form requirements")
    elif not key.startswith("yjs_ag_"):
        out.append("MOCK_PORTAL_AGENT_KEY does not look like an agent key (expected yjs_ag_...)")
    return out


def portal_ready() -> bool:
    """All portal settings present and well formed (else the agent won't open the browser)."""
    return not portal_url_warnings()


# Status tracking (Phase 6, agent/tracking/): APScheduler inside FastAPI polls the portal's
# agent API (read-only delegation) once per citizen with open applications.
STATUS_POLL_SECONDS = float(os.getenv("STATUS_POLL_SECONDS", "300"))  # 30 for the demo
STATUS_POLL = os.getenv("STATUS_POLL", "1").strip().lower() not in ("0", "false", "no")  # off: tests
TRACK_TIMEOUT = float(os.getenv("TRACK_TIMEOUT", "15"))  # seconds per portal call from the poller
TRACK_NOW_TIMEOUT = float(os.getenv("TRACK_NOW_TIMEOUT", "8"))  # "what's my status?" checks now
TRACK_BACKOFF_MAX = float(os.getenv("TRACK_BACKOFF_MAX", "900"))  # errors / 429: 2x per failure, up to this
# Web push (backend only for now). Generate keys: python -m agent.tracking.push keys
VAPID_PRIVATE_KEY = _opt("VAPID_PRIVATE_KEY")  # base64url raw private key; secret
VAPID_PUBLIC_KEY = _opt("VAPID_PUBLIC_KEY")  # base64url uncompressed point; the browser needs it
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:yojanasaathi@example.org")
