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
from agent import portal
from tests.fake_driver import FakeDriver
from tests.helpers import FakeLLM

TEST_MASTER_KEY = base64.b64encode(bytes(range(32))).decode()  # tests only
# Never the real portal: every test gets a FakeDriver (in-process); the browser tests point
# these at tests/fake_portal.py on 127.0.0.1.
TEST_PORTAL = {"MOCK_PORTAL_URL": "https://mock-portal.invalid",  # .invalid never resolves
               "MOCK_PORTAL_API": "https://mock-portal.invalid/api",
               "MOCK_PORTAL_AGENT_KEY": "yjs_ag_test_key_not_a_secret"}


def _test_env() -> dict[str, str]:
    """Settings the agent under test uses (also for the subprocess restart tests)."""
    url = config.TEST_DATABASE_URL or (db.url_for_tests(config.DATABASE_URL) if config.DATABASE_URL else "")
    return {"DATABASE_URL": url, "MASTER_KEY": TEST_MASTER_KEY,
            "VAULT_DIR": tempfile.mkdtemp(prefix="ys-vault-test-"),
            "LLM_PROVIDER": "none", "LLM_WARMUP": "0", "LLM_KEEPWARM": "0",
            "SARVAM_API_KEY": "",  # no Sarvam calls from tests (also in subprocesses)
            "BROWSER_HEADLESS": "true", "BROWSER_SLOWMO_MS": "0", **TEST_PORTAL}


TEST_ENV = _test_env()
config.DATABASE_URL = TEST_ENV["DATABASE_URL"] or None
config.MASTER_KEY = TEST_MASTER_KEY
config.VAULT_DIR = Path(TEST_ENV["VAULT_DIR"])
config.BROWSER_HEADLESS, config.BROWSER_SLOWMO_MS = True, 0.0
for _k, _v in TEST_PORTAL.items():
    setattr(config, _k, _v)
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


@pytest.fixture(autouse=True)
def fake_driver() -> FakeDriver:
    """The portal, in-process (OTP 123456). Browser tests swap in the real driver."""
    previous = portal._driver
    fake = FakeDriver()
    portal.set_driver(fake)
    yield fake
    portal.set_driver(previous)


@pytest.fixture
def subprocess_env() -> dict[str, str]:
    return {**os.environ, **TEST_ENV, "PYTHONIOENCODING": "utf-8"}


def pytest_addoption(parser):
    parser.addoption("--regen-web-fixtures", action="store_true",
                     help="rewrite web/src/test/fixtures/*.json from GET /summary (tests/web_fixtures.py)")


config.SARVAM_API_KEY = None  # tests never call Sarvam; test_tts.py scripts the responses
