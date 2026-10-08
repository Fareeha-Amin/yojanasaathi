"""The /turn contract: pause at the human gate, resume on the next message."""

import uuid

import pytest
from fastapi.testclient import TestClient

from agent.main import app, graph

client = TestClient(app)


@pytest.fixture
def case_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"reply", "pause"}  # contract shape
    return body


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_pause_then_yes_submits(case_id):
    first = turn(case_id, "I'm 62, can I get a pension?")
    assert first["pause"]["type"] == "confirm"
    assert "Submitted" not in first["reply"]

    second = turn(case_id, "ಹೌದು")
    assert second["pause"] is None
    assert "DEMO-0001" in second["reply"]


def test_no_submits_nothing(case_id):
    turn(case_id, "I'm 62")
    out = turn(case_id, "no")
    assert out["pause"] is None
    assert "nothing was submitted" in out["reply"]


def test_unclear_stays_paused_then_yes(case_id):
    turn(case_id, "I'm 62")
    out = turn(case_id, "what documents do I need?")
    assert out["pause"]["type"] == "confirm"
    assert "Submitted" not in out["reply"]

    out = turn(case_id, "yes")
    assert out["pause"] is None
    assert "Submitted" in out["reply"]


@pytest.mark.parametrize("yes", ["ಹೌದು", "हाँ", "yes"])
def test_yes_in_any_language_after_reask(case_id, yes):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ")
    turn(case_id, "what?")
    out = turn(case_id, yes)
    assert out["pause"] is None
    assert "Submitted" in out["reply"]


def test_cases_are_isolated(case_id):
    turn(case_id, "I'm 62")
    other = turn(f"{case_id}-other", "hello")
    assert other["pause"]["type"] == "confirm"  # fresh case starts at interview, not resumed
    out = turn(case_id, "yes")
    assert "Submitted" in out["reply"]


def case_lang(case_id: str) -> str | None:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values.get("lang")


def test_lang_is_optional(case_id):
    turn(case_id, "I'm 62")
    assert case_lang(case_id) is None


def test_lang_stored_on_new_turn(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", lang="kn")
    assert case_lang(case_id) == "kn"


def test_lang_updates_while_paused(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", lang="kn")
    out = turn(case_id, "हाँ", lang="hi")
    assert "Submitted" in out["reply"]
    assert case_lang(case_id) == "hi"


def test_lang_omitted_on_resume_keeps_previous(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", lang="kn")
    turn(case_id, "what?")
    assert case_lang(case_id) == "kn"


def test_unknown_lang_rejected(case_id):
    r = client.post(f"/turn/{case_id}", json={"text": "hi", "lang": "fr"})
    assert r.status_code == 422


def test_finished_case_starts_new_turn(case_id):
    turn(case_id, "I'm 62")
    turn(case_id, "yes")
    out = turn(case_id, "hello again")
    assert out["pause"]["type"] == "confirm"
