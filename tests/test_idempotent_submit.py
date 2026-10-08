"""Submission is idempotent: once a case has an application ID, no later message (any
channel, any language) can submit it again; the citizen gets the existing ID back."""

import asyncio
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.graph
from agent.graph import submit
from agent.main import app
from voice.agent_client import AgentClient

text_client = TestClient(app)


@pytest.fixture
def case_id() -> str:
    return f"idem-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def portal(monkeypatch) -> list[dict]:
    """Records every real portal submission."""
    calls: list[dict] = []

    def fake_submit(state):
        calls.append(dict(state))
        return "DEMO-0001"

    monkeypatch.setattr(agent.graph, "_submit_to_portal", fake_submit)
    return calls


def text(case_id: str, msg: str, lang: str | None = None) -> dict:
    body = {"text": msg} if lang is None else {"text": msg, "lang": lang}
    r = text_client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    return r.json()


def voice(case_id: str, msg: str, lang: str | None = None) -> dict:
    async def go():
        client = AgentClient("http://agent", transport=httpx.ASGITransport(app=app))
        try:
            return await client.turn(case_id, msg, lang)
        finally:
            await client.aclose()

    return asyncio.run(go())


def assert_already_submitted(out: dict) -> None:
    assert out["pause"] is None  # no new confirm pause: nothing left to approve
    assert out["reply"] == "Already submitted. Your application ID is DEMO-0001."


def test_double_yes_same_channel(case_id, portal):
    text(case_id, "I'm 62, can I get a pension?")
    assert "Submitted!" in text(case_id, "yes")["reply"]
    assert_already_submitted(text(case_id, "yes"))
    assert_already_submitted(text(case_id, "yes"))
    assert len(portal) == 1


def test_double_yes_across_channels(case_id, portal):
    # The Phase 1 acceptance run: voice leaves the pause open, text approves,
    # then voice says yes again.
    assert voice(case_id, "ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ", "kn")["pause"]["type"] == "confirm"
    assert "Submitted!" in text(case_id, "yes")["reply"]
    assert_already_submitted(voice(case_id, "ಹೌದು", None))
    assert_already_submitted(text(case_id, "हाँ", "hi"))
    assert len(portal) == 1


@pytest.mark.parametrize("msg", ["yes", "ಹೌದು", "हाँ", "submit it again", "I'm 62, can I get a pension?"])
def test_any_message_after_submit_returns_existing_id(case_id, portal, msg):
    text(case_id, "I'm 62")
    text(case_id, "yes")
    assert_already_submitted(text(case_id, msg))
    assert_already_submitted(text(case_id, "yes"))  # and no confirm pause was re-opened
    assert len(portal) == 1


def test_submit_node_refuses_when_app_id_exists(portal):
    # Defence in depth: even if routing ever led back to submit, it must not resubmit.
    out = submit({"app_id": "DEMO-0001", "status": "submitted"})
    assert out == {"reply": "Already submitted. Your application ID is DEMO-0001."}
    assert portal == []


def test_cancelled_then_yes_still_needs_the_gate(case_id, portal):
    text(case_id, "I'm 62")
    text(case_id, "no")
    out = text(case_id, "yes")  # a fresh turn after cancel: re-asks, does not submit
    assert out["pause"]["type"] == "confirm"
    assert portal == []
