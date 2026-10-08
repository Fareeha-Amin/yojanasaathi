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

# LLM (Phase 2). Provider not chosen yet; no provider package is installed.
LLM_PROVIDER = _opt("LLM_PROVIDER")
LLM_MODEL = _opt("LLM_MODEL")
LLM_API_KEY = _opt("LLM_API_KEY")

# Mock portal (Phase 4). Runs on Ayush's laptop behind a public URL; never assume localhost.
MOCK_PORTAL_URL = _opt("MOCK_PORTAL_URL")  # the site Playwright drives
MOCK_PORTAL_API = _opt("MOCK_PORTAL_API")  # status polling API base
