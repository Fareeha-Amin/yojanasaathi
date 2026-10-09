"""Startup warnings for the portal URLs (logged by the agent; values never echoed)."""

import logging

import pytest
from fastapi.testclient import TestClient

from agent import config


@pytest.mark.parametrize("url, api, expect", [
    (None, None, ["MOCK_PORTAL_URL is not set", "MOCK_PORTAL_API is not set"]),
    ("https://<ayush-portal-public-url>", "https://<ayush-portal-public-url>/api",
     ["MOCK_PORTAL_URL still holds a placeholder", "MOCK_PORTAL_API still holds a placeholder"]),
    ("https://portal.example.org", "yjs_ag_secretlookingvalue", ["MOCK_PORTAL_API is not an http(s) URL"]),
    ("https://portal.example.org", "https://portal.example.org/api", []),
    ("http://127.0.0.1:5174", "http://127.0.0.1:8001/api", []),
    ("https://portal.example.org", "https://portal.example.org", ["MOCK_PORTAL_API must end with /api"]),
])
def test_portal_url_warnings(monkeypatch, url, api, expect):
    monkeypatch.setattr(config, "MOCK_PORTAL_URL", url)
    monkeypatch.setattr(config, "MOCK_PORTAL_API", api)
    monkeypatch.setattr(config, "MOCK_PORTAL_AGENT_KEY", "yjs_ag_secretlookingvalue")
    out = config.portal_url_warnings()
    assert len(out) == len(expect)
    for w, e in zip(out, expect):
        assert w.startswith(e)
    assert "secretlooking" not in " ".join(out)  # the value is never echoed


def test_agent_logs_the_warnings_at_startup(monkeypatch, caplog):
    from agent import main
    from agent.main import app

    # Runs the real lifespan; its shutdown would close the pool every other test shares.
    monkeypatch.setattr(main.store, "close", lambda: None)
    monkeypatch.setattr(config, "MOCK_PORTAL_URL", None)
    monkeypatch.setattr(config, "MOCK_PORTAL_API", "https://portal.example.org/api")
    with caplog.at_level(logging.WARNING, logger="yojanasaathi"), TestClient(app):
        pass
    assert any("MOCK_PORTAL_URL is not set" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("key, expect", [
    (None, "MOCK_PORTAL_AGENT_KEY is not set"),
    ("not-an-agent-key-pasted-by-mistake", "MOCK_PORTAL_AGENT_KEY does not look like an agent key"),
    ("yjs_ag_realkey", None),
])
def test_agent_key_is_checked_and_never_echoed(monkeypatch, key, expect):
    monkeypatch.setattr(config, "MOCK_PORTAL_URL", "https://portal.example.org")
    monkeypatch.setattr(config, "MOCK_PORTAL_API", "https://portal.example.org/api")
    monkeypatch.setattr(config, "MOCK_PORTAL_AGENT_KEY", key)
    out = config.portal_url_warnings()
    assert (out[0].startswith(expect) if expect else out == [])
    assert "pasted-by-mistake" not in " ".join(out) and "realkey" not in " ".join(out)
    assert config.portal_ready() == (expect is None)


def test_urls_are_trimmed(monkeypatch):
    monkeypatch.setenv("MOCK_PORTAL_URL", " https://portal.example.org/ ")
    assert config._url("MOCK_PORTAL_URL") == "https://portal.example.org"
