"""Submission is idempotent PER SCHEME: once a scheme has an application ID, a request to
submit that scheme again (a repeated "yes", any channel, any language, or naming it) gets
the existing ID back and never reaches the portal. Everything else routes normally:
questions, status, and applying for another eligible scheme.

Phase 4: the portal is the FakeDriver (tests/fake_driver.py); `fake_driver.submits`
records every click on the portal's Submit."""

import asyncio
import uuid

import httpx
import pytest

from agent.graph import submit
from agent.llm import Extraction
from agent.main import app, graph
from agent.replies import t
from tests import helpers
from tests.helpers import CaseClient
from voice.agent_client import AgentClient

text_client = CaseClient(app)

READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ, ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ"
FIRST, SECOND = "YJS-0000000001", "YJS-0000000002"  # the FakeDriver's numbers, in order


@pytest.fixture
def case_id() -> str:
    cid = f"idem-{uuid.uuid4().hex[:8]}"
    helpers.upload_documents(text_client, cid, "pension-001")  # covers pension-002's too
    return cid


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


def submit_pension_001(case_id: str) -> None:
    text(case_id, READY)
    helpers.to_confirm(lambda m: text(case_id, m), text(case_id, "senior citizen pension"))
    assert FIRST in text(case_id, "yes")["reply"]


def assert_existing_id(out: dict, lang: str = "en", app_id: str = FIRST) -> None:
    assert out["pause"] is None  # no new confirm pause: nothing left to approve
    assert out["reply"] == t("already_submitted", lang, app_id=app_id)


def test_already_submitted_english_text_unchanged():
    assert t("already_submitted", "en", app_id="DEMO-0001") == \
        "Already submitted. Your application ID is DEMO-0001."


# (a) "yes" again -> existing ID, no new submit
@pytest.mark.parametrize("again", ["yes", "ಹೌದು", "हाँ", "submit it again", "yes yes"])
def test_a_repeated_yes_returns_existing_id(case_id, fake_driver, again):
    submit_pension_001(case_id)
    lang = {"ಹೌದು": "kn", "हाँ": "hi"}.get(again, "en")  # a case with no lang takes the script's
    assert_existing_id(text(case_id, again), lang)
    assert_existing_id(text(case_id, "yes"), lang)
    assert fake_driver.submit_count("pension-001") == 1


def test_a_naming_the_submitted_scheme_returns_existing_id(case_id, fake_driver):
    submit_pension_001(case_id)
    assert_existing_id(text(case_id, "apply for the senior citizen pension"))
    assert fake_driver.submit_count() == 1


# (b) a question -> normal answer
def test_b_question_after_submit_gets_a_normal_answer(case_id, fake_llm):
    submit_pension_001(case_id)
    q = "what documents does the health scheme need?"
    fake_llm.extractions[q] = Extraction(intent="question")
    fake_llm.answers[q] = "It needs identity, residence and income proof."
    out = text(case_id, q)
    assert out["reply"].startswith("It needs identity, residence and income proof.")
    assert "Already submitted" not in out["reply"] and out["pause"] is None


def test_b_status_question_after_submit(case_id):
    submit_pension_001(case_id)
    out = text(case_id, "what is my application status?")
    assert out["reply"] == t("status_app", "en", title="Senior Citizen Pension Scheme",
                             app_id=FIRST, status="submitted")


def test_b_eligibility_question_after_submit_offers_the_rest(case_id):
    submit_pension_001(case_id)
    out = text(case_id, READY)
    assert "You can apply for 3 more schemes" in out["reply"]
    assert "Senior Citizen Pension Scheme" not in out["reply"]
    ui = {s["scheme_id"]: s["app_id"] for s in out["ui"]["schemes"]}
    assert ui["pension-001"] == FIRST and ui["pension-002"] is None


# (c) choosing another scheme -> its flow starts (only its new questions are asked)
@pytest.mark.parametrize("choice", ["social security pension", "the next one"])
def test_c_choosing_another_scheme_starts_its_flow(case_id, fake_driver, choice):
    submit_pension_001(case_id)
    out = text(case_id, choice)
    asked = []
    while (out["ui"] or {}).get("type") == "form":
        asked.append(out["ui"]["field"])
        out = text(case_id, helpers.FORM_ANSWERS[out["ui"]["field"]])
    assert asked == ["assistance_category", "declaration_consent"]  # name, account... are known
    assert out["pause"]["type"] == "otp"
    out = text(case_id, helpers.OTP)
    assert out["pause"]["preview"]["scheme_id"] == "pension-002"
    out = text(case_id, "yes")
    assert out["reply"].startswith(t("submitted", "en", app_id=SECOND))
    # (d) still exactly one submission each
    assert fake_driver.submit_count("pension-001") == 1 and fake_driver.submit_count("pension-002") == 1
    assert_existing_id(text(case_id, "yes"), app_id=SECOND)  # latest scheme's ID
    assert fake_driver.submit_count() == 2


def test_d_one_portal_submission_across_voice_and_text(case_id, fake_driver):
    # The Phase 1 acceptance bug: voice leaves the pause open, text approves, voice says
    # yes again.
    assert voice(case_id, KN_READY, "kn")["pause"] is None  # 4 matches
    first_q = voice(case_id, "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ", None)
    assert first_q["ui"]["type"] == "form"  # the bridge forwards ui to the web app
    helpers.to_confirm(lambda m: text(case_id, m), first_q)
    assert FIRST in text(case_id, "yes")["reply"]  # the gate, approved by text
    out = voice(case_id, "ಹೌದು", None)
    assert out["pause"] is None and out["reply"] == t("already_submitted", "kn", app_id=FIRST)
    assert out["ui"] is None
    assert_existing_id(text(case_id, "हाँ", "hi"), "hi")
    assert fake_driver.submit_count("pension-001") == 1 and fake_driver.submit_count() == 1


def test_submit_node_refuses_when_scheme_has_an_application(fake_driver):
    # Defence in depth: even if routing ever led back to submit, it must not resubmit.
    out = submit({"selected": "pension-001",
                  "applications": {"pension-001": {"app_id": "DEMO-0001", "status": "SUBMITTED"}}})
    assert out.update == {"reply": "Already submitted. Your application ID is DEMO-0001.", "subtitle": None}
    assert fake_driver.submit_count() == 0 and "submit" not in fake_driver.calls


def test_cancelled_then_yes_still_needs_the_gate(case_id, fake_driver):
    text(case_id, READY)
    helpers.to_confirm(lambda m: text(case_id, m), text(case_id, "senior citizen pension"))
    text(case_id, "no")
    out = text(case_id, "yes")  # nothing was submitted: no "already submitted", no submit
    assert out["pause"] is None and "Already submitted" not in out["reply"]
    assert text(case_id, "senior citizen pension")["pause"]["type"] == "otp"  # log in again
    assert text(case_id, helpers.OTP)["pause"]["type"] == "confirm"
    assert fake_driver.submit_count() == 0


def test_applications_are_tracked_per_scheme(case_id):
    submit_pension_001(case_id)
    s = graph.get_state({"configurable": {"thread_id": case_id}}).values
    assert s["applications"] == {"pension-001": {"app_id": FIRST, "status": "SUBMITTED"}}
    assert s["offered"] == ["pension-002", "health-001", "health-002"]
