"""Start the agent for the Playwright end-to-end tests (web/playwright.config.js runs it).

Own database: DATABASE_URL's name + "_e2e_test", dropped and created on every start (never
the real one; db.recreate_database refuses names not ending in _test). No LLM (the golden
path is deterministic), no Sarvam read-aloud (E2E_SARVAM=1 to allow it), a test MASTER_KEY,
a temporary vault. Port: E2E_AGENT_PORT (8010).

Run by hand: .\\.venv\\Scripts\\python.exe web\\e2e\\agent_server.py
"""

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

base = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
if not base:
    sys.exit("DATABASE_URL is not set (.env): the e2e agent needs PostgreSQL")
name = conninfo_to_dict(base).get("dbname") or "yojanasaathi"
url = make_conninfo(base, dbname=f"{name}_e2e_test")

# Before importing the agent: agent.config reads the environment at import time.
os.environ.update({
    "DATABASE_URL": url,
    "MASTER_KEY": base64.b64encode(bytes(range(32))).decode(),  # tests only
    "VAULT_DIR": tempfile.mkdtemp(prefix="ys-vault-e2e-"),
    "LLM_PROVIDER": "none", "LLM_WARMUP": "0", "LLM_KEEPWARM": "0",
})
if os.environ.get("E2E_SARVAM") != "1":  # read aloud costs Sarvam credit: off unless asked
    os.environ["SARVAM_API_KEY"] = ""

import uvicorn  # noqa: E402

from agent import db  # noqa: E402

db.recreate_database(url)
uvicorn.run("agent.main:app", host="127.0.0.1", port=int(os.environ.get("E2E_AGENT_PORT", "8010")),
            log_level="warning")
