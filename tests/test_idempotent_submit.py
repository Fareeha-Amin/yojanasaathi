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
from agent.replies import t
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


READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ, ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ"


def to_confirm(case_id: str) -> None:
    text(case_id, READY)
    assert text(case_id, "proceed")["pause"]["type"] == "confirm"


def assert_already_submitted(out: dict, lang: str = "en") -> None:
    assert out["pause"] is None  # no new confirm pause: nothing left to approve
    assert out["reply"] == t("already_submitted", lang, app_id="DEMO-0001")


def test_already_submitted_english_text_unchanged():
    assert t("already_submitted", "en", app_id="DEMO-0001") ==         "Already submitted. Your application ID is DEMO-0001."


def test_double_yes_same_channel(case_id, portal):
    to_confirm(case_id)
    assert "Submitted!" in text(case_id, "yes")["reply"]
    assert_already_submitted(text(case_id, "yes"))
    assert_already_submitted(text(case_id, "yes"))
    assert len(portal) == 1


def test_double_yes_across_channels(case_id, portal):
    # The Phase 1 acceptance run: voice leaves the pause open, text approves,
    # then voice says yes again.
    assert voice(case_id, KN_READY, "kn")["pause"] is None  # eligible + checklist
    assert voice(case_id, "ಹೌದು", None)["pause"]["type"] == "confirm"  # yes to filling the form
    assert "DEMO-0001" in text(case_id, "yes")["reply"]  # the gate, approved by text
    assert_already_submitted(voice(case_id, "ಹೌದು", None), "kn")
    assert_already_submitted(text(case_id, "हाँ", "hi"), "hi")
    assert len(portal) == 1


@pytest.mark.parametrize("msg", ["yes", "ಹೌದು", "हाँ", "submit it again", "I'm 62, can I get a pension?"])
def test_any_message_after_submit_returns_existing_id(case_id, portal, msg):
    to_confirm(case_id)
    text(case_id, "yes")
    # a case with no lang replies in the script of the message
    assert_already_submitted(text(case_id, msg), {"ಹೌದು": "kn", "हाँ": "hi"}.get(msg, "en"))
    assert_already_submitted(text(case_id, "yes"))  # and no confirm pause was re-opened
    assert len(portal) == 1


def test_submit_node_refuses_when_app_id_exists(portal):
    # Defence in depth: even if routing ever led back to submit, it must not resubmit.
    out = submit({"app_id": "DEMO-0001", "status": "submitted"})
    assert out == {"reply": "Already submitted. Your application ID is DEMO-0001."}
    assert portal == []


def test_cancelled_then_yes_still_needs_the_gate(case_id, portal):
    to_confirm(case_id)
    text(case_id, "no")
    out = text(case_id, "yes")  # a fresh turn after cancel: offers the form again, no submit
    assert out["pause"] is None
    assert text(case_id, "yes")["pause"]["type"] == "confirm"  # and the gate asks again
    assert portal == []
