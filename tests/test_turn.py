"""The /turn contract: pause at the human gate, resume on the next message."""

import uuid

import pytest

from agent.main import app, graph
from tests import helpers
from tests.helpers import CaseClient

client = CaseClient(app)  # plain /turn calls; the token only goes to /cases/... (document uploads)

# One message with age + income: the citizen qualifies for all 4 portal schemes.
EN_READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"


@pytest.fixture
def case_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"reply", "pause", "ui", "subtitle"}  # contract shape (ui 2026-10-09, subtitle Phase 5)
    return body


def to_confirm(case_id: str, ready: str = EN_READY, lang: str | None = None) -> dict:
    """Interview -> 4 matches -> pick pension-001 -> form questions -> OTP -> the confirm
    pause (Phase 4: the portal's review step; FakeDriver, tests/fake_driver.py)."""
    helpers.upload_documents(client, case_id, "pension-001")
    assert turn(case_id, ready, lang)["pause"] is None
    out = turn(case_id, "senior citizen pension")
    return helpers.to_confirm(lambda t: turn(case_id, t), out)


def submitted_id(out: dict) -> bool:
    return "YJS-" in out["reply"]


def test_health():
    body = client.get("/health").json()
    assert body["ok"] is True and "llm" in body


def test_pause_then_yes_submits(case_id):
    first = to_confirm(case_id)
    assert "Submitted" not in first["reply"]

    second = turn(case_id, "ಹೌದು")
    assert second["pause"] is None
    assert submitted_id(second)


def test_no_submits_nothing(case_id):
    to_confirm(case_id)
    out = turn(case_id, "no")
    assert out["pause"] is None
    assert "nothing was submitted" in out["reply"]


def test_unclear_stays_paused_then_yes(case_id):
    to_confirm(case_id)
    out = turn(case_id, "what documents do I need?")
    assert out["pause"]["type"] == "confirm"
    assert "Submitted" not in out["reply"]

    out = turn(case_id, "yes")
    assert out["pause"] is None
    assert "Submitted" in out["reply"]


@pytest.mark.parametrize("yes", ["ಹೌದು", "हाँ", "yes"])
def test_yes_in_any_language_after_reask(case_id, yes):
    to_confirm(case_id, KN_READY)
    turn(case_id, "what?")
    out = turn(case_id, yes)
    assert out["pause"] is None
    assert submitted_id(out)


def test_cases_are_isolated(case_id):
    to_confirm(case_id)
    other = turn(f"{case_id}-other", "hello")
    assert other["pause"] is None  # fresh case starts at the interview, not resumed
    out = turn(case_id, "yes")
    assert "Submitted" in out["reply"]


def case_lang(case_id: str) -> str | None:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values.get("lang")


def test_lang_is_optional(case_id):
    turn(case_id, "I'm 62")
    assert case_lang(case_id) is None  # Latin script, no lang sent: replies default to English


def test_lang_stored_on_new_turn(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", lang="kn")
    assert case_lang(case_id) == "kn"


def test_lang_updates_while_paused(case_id):
    to_confirm(case_id, KN_READY, lang="kn")
    out = turn(case_id, "हाँ", lang="hi")
    assert submitted_id(out)
    assert case_lang(case_id) == "hi"


def test_lang_omitted_on_resume_keeps_previous(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", lang="kn")
    turn(case_id, "what?")
    assert case_lang(case_id) == "kn"


def test_unknown_lang_rejected(case_id):
    r = client.post(f"/turn/{case_id}", json={"text": "hi", "lang": "fr"})
    assert r.status_code == 422


def test_cancelled_case_can_start_again(case_id):
    to_confirm(case_id)
    turn(case_id, "no")
    out = turn(case_id, "senior citizen pension")
    # nothing was submitted, so the form can be redone: the answers are kept, log in again
    assert out["pause"]["type"] == "otp"
    assert turn(case_id, helpers.OTP)["pause"]["type"] == "confirm"
    # A submitted case does not restart: see test_idempotent_submit.py
