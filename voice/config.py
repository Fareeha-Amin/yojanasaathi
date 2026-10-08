"""Voice settings from the repo-root .env. Never hardcode secrets.

The voice bot is its own process (own venv, voice/.venv) and reaches the agent only
through POST /turn at AGENT_URL.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY") or None
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8000")
# Case used when the client doesn't send one in the /start body ({"body": {"case_id": ...}}).
# Same default as the web app, so voice and text continue the same case.
VOICE_CASE_ID = os.getenv("VOICE_CASE_ID", "demo-case-1")
