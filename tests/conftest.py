"""Tests never call a real LLM: every test gets a FakeLLM (deterministic parsers only,
unless a test scripts what the "LLM" returns). Live Ollama checks: tests/test_llm_live.py.

Postgres: tests use their own database, TEST_DATABASE_URL or DATABASE_URL + "_test"
(e.g. yojanasaathi_test), dropped and created once per session, on the same server as
DATABASE_URL. The real database is never touched. Pure unit tests (numbers, facts, rules,
gate, ...) do not need Postgres; tests that import agent.main do, and without it they
fail with the agent's startup message.
"""

import base64
import os
import tempfile
from pathlib import Path

import pytest

from agent import config, db
from agent import llm as llm_mod
from tests.helpers import FakeLLM

TEST_MASTER_KEY = base64.b64encode(bytes(range(32))).decode()  # tests only


def _test_env() -> dict[str, str]:
    """Settings the agent under test uses (also for the subprocess restart tests)."""
    url = config.TEST_DATABASE_URL or (db.url_for_tests(config.DATABASE_URL) if config.DATABASE_URL else "")
    return {"DATABASE_URL": url, "MASTER_KEY": TEST_MASTER_KEY,
            "VAULT_DIR": tempfile.mkdtemp(prefix="ys-vault-test-"),
            "LLM_PROVIDER": "none", "LLM_WARMUP": "0", "LLM_KEEPWARM": "0",
            "SARVAM_API_KEY": ""}  # no Sarvam calls from tests (also in subprocesses)


TEST_ENV = _test_env()
config.DATABASE_URL = TEST_ENV["DATABASE_URL"] or None
config.MASTER_KEY = TEST_MASTER_KEY
config.VAULT_DIR = Path(TEST_ENV["VAULT_DIR"])
if config.DATABASE_URL:
    try:
        db.recreate_database(config.DATABASE_URL)
    except Exception:
        pass  # Postgres down: unit tests still run; agent.main's StartupError explains the rest


@pytest.fixture(autouse=True)
def fake_llm() -> FakeLLM:
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)


@pytest.fixture
def subprocess_env() -> dict[str, str]:
    return {**os.environ, **TEST_ENV, "PYTHONIOENCODING": "utf-8"}


def pytest_addoption(parser):
    parser.addoption("--regen-web-fixtures", action="store_true",
                     help="rewrite web/src/test/fixtures/*.json from GET /summary (tests/web_fixtures.py)")


config.SARVAM_API_KEY = None  # tests never call Sarvam; test_tts.py scripts the responses
