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

# Mock portal (Phase 4). Runs on Ayush's laptop behind a public URL; never assume localhost.
MOCK_PORTAL_URL = _opt("MOCK_PORTAL_URL")  # the site Playwright drives
MOCK_PORTAL_API = _opt("MOCK_PORTAL_API")  # status polling API base
