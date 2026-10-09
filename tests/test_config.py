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
])
def test_portal_url_warnings(monkeypatch, url, api, expect):
    monkeypatch.setattr(config, "MOCK_PORTAL_URL", url)
    monkeypatch.setattr(config, "MOCK_PORTAL_API", api)
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
