"""Agent brain through /turn: interview -> eligibility (4 portal schemes) -> choose ->
confirm -> submit -> next scheme.

The LLM is a FakeLLM (tests/conftest.py): these tests prove the golden path works on the
deterministic parsers alone, and how LLM output is merged when it does come.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from agent.llm import Extraction
from agent.main import app, graph
from agent.replies import t

client = TestClient(app)

KN_TITLE = {"pension-001": "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ", "pension-002": "ಸಾಮಾಜಿಕ ಭದ್ರತಾ ಪಿಂಚಣಿ ನೆರವು ಯೋಜನೆ"}


@pytest.fixture
def case_id() -> str:
    return f"flow-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"reply", "pause", "ui", "subtitle"}
    return out


def state(case_id: str) -> dict:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values


def kannada(text: str) -> bool:
    return any(0x0C80 <= ord(c) < 0x0D00 for c in text)


def sentences(text: str) -> int:
    return len([s for s in re.split(r"[.?!।]\s*", text) if s.strip()])


def test_golden_path_kannada_four_matches(case_id):
    # 1. The pension line: asks only the missing field (income), in Kannada.
    out = turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")
    assert out == {"reply": t("ask_annual_income", "kn"), "pause": None, "ui": None,
                   "subtitle": t("ask_annual_income", "en")}  # English under the Kannada bubble
    assert state(case_id)["missing"] == ["annual_income"]

    # 2. Income in number words -> 4 matches: short spoken reply, full detail on screen.
    out = turn(case_id, "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ")
    r = out["reply"]
    assert out["pause"] is None and kannada(r) and sentences(r) <= 3
    assert "4 ಯೋಜನೆಗಳಿಗೆ ಅರ್ಹರು" in r and "ವಯಸ್ಸು 62 ಮತ್ತು ವಾರ್ಷಿಕ ಆದಾಯ ₹1,20,000" in r
    assert r.index(KN_TITLE["pension-001"]) < r.index(KN_TITLE["pension-002"])  # pension-001 first
    assert "ರಾಷ್ಟ್ರೀಯ ಆರೋಗ್ಯ" not in r  # only the top two are spoken
    ui = out["ui"]
    assert ui["type"] == "eligibility"
    assert [s["scheme_id"] for s in ui["schemes"]] == ["pension-001", "pension-002", "health-001", "health-002"]
    first = ui["schemes"][0]
    assert first["status"] == "eligible" and first["title"] == KN_TITLE["pension-001"]
    assert first["reasons"] == ["ವಯಸ್ಸು 62, ಕನಿಷ್ಠ 60 ಬೇಕು", "ವಾರ್ಷಿಕ ಆದಾಯ ₹1,20,000, ಮಿತಿ ₹3,00,000"]
    assert first["source_url"].endswith("/schemes/pension-001") and first["effective_date"]
    assert [d["label"] for d in first["documents"]][:2] == ["ಗುರುತಿನ ಪುರಾವೆ", "ವಯಸ್ಸಿನ ಪುರಾವೆ"]
    assert state(case_id)["asking"] == "choose"

    # 3. Picking pension-001 by name -> read-back + confirm pause (not a submit).
    out = turn(case_id, "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ")
    assert out["pause"]["type"] == "confirm"
    assert out["pause"]["preview"]["scheme_id"] == "pension-001"
    assert out["pause"]["preview"]["fields"] == {"age": 62, "annual_income": 120000}
    assert "ವಯಸ್ಸು 62" in out["reply"] and sentences(out["reply"]) <= 3

    # 4. "ಹೌದು" at the gate -> submitted, and the next eligible scheme is offered by name.
    out = turn(case_id, "ಹೌದು")
    assert out["pause"] is None
    assert out["reply"] == " ".join([t("submitted", "kn", app_id="DEMO-0001"),
                                     t("submitted_next", "kn", title=KN_TITLE["pension-002"])])
    assert out["ui"]["type"] == "submitted" and out["ui"]["app_id"] == "DEMO-0001"
    assert [n["scheme_id"] for n in out["ui"]["next"]] == ["pension-002", "health-001", "health-002"]


def test_bare_yes_at_choose_asks_for_a_name(case_id):
    turn(case_id, "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?")
    out = turn(case_id, "yes")
    assert out["reply"] == t("choose_reask", "en", title="Senior Citizen Pension Scheme")
    assert out["pause"] is None


@pytest.mark.parametrize("choice, sid", [
    ("the first one", "pension-001"), ("second", "pension-002"),
    ("national health support", "health-001"), ("family healthcare please", "health-002"),
])
def test_choose_by_position_or_name(case_id, choice, sid):
    turn(case_id, "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?")
    out = turn(case_id, choice)
    assert out["pause"]["type"] == "confirm" and out["pause"]["preview"]["scheme_id"] == sid


def test_hindi_golden_path(case_id):
    assert turn(case_id, "मैं 62 साल की हूँ। क्या मुझे पेंशन मिल सकती है?", "hi")["reply"] == \
        t("ask_annual_income", "hi")
    r = turn(case_id, "एक लाख बीस हज़ार")["reply"]
    assert "4 योजनाओं के लिए पात्र हैं" in r and "वरिष्ठ नागरिक पेंशन योजना" in r
    out = turn(case_id, "पहली वाली")
    assert out["pause"]["preview"]["scheme_id"] == "pension-001"
    assert "DEMO-0001" in turn(case_id, "हाँ")["reply"]


def test_one_match_names_reason_documents_and_asks(case_id):
    out = turn(case_id, "I'm 45 and our income is 4.5 lakh, I need help with hospital bills")
    # health-001 (<= 5 L) only; health-002 (<= 4 L) and both pensions (age) are out
    assert out["reply"] == t("match_one", "en", title="National Health Support Scheme",
                             facts="annual income ₹4,50,000", n=5)
    assert sentences(out["reply"]) <= 3
    assert turn(case_id, "yes")["pause"]["preview"]["scheme_id"] == "health-001"


def test_not_eligible_in_topic_offers_other_schemes(case_id):
    out = turn(case_id, "I'm 55, pension?")
    assert out["reply"] == t("not_eligible_topic", "en", topic="pension",
                             reasons="age 55, at least 60 needed")
    assert "annual income" not in out["reply"]  # pensions decided without asking income
    assert turn(case_id, "yes")["reply"] == t("ask_annual_income", "en")  # now all schemes
    out = turn(case_id, "90000")
    assert "You qualify for 2 schemes" in out["reply"]
    assert "National Health Support Scheme and Family Healthcare Assistance" in out["reply"]


def test_picking_an_ineligible_scheme_explains(case_id):
    turn(case_id, "I'm 45, income 4.5 lakh, hospital help")
    out = turn(case_id, "family healthcare")
    assert out["reply"] == t("not_eligible_one", "en", title="Family Healthcare Assistance",
                             reasons="annual income ₹4,50,000, limit ₹4,00,000")
    assert out["pause"] is None


def test_no_topic_asks_age_then_income(case_id):
    out = turn(case_id, "hello")
    assert out["reply"] == f"{t('greeting', 'en')} {t('ask_age', 'en')}"
    turn(case_id, "62")
    assert state(case_id)["asking"] == "annual_income"


def test_decline(case_id):
    turn(case_id, "I'm 45, income 4.5 lakh, hospital help")
    assert turn(case_id, "no")["reply"] == t("proceed_declined", "en")
    assert turn(case_id, "apply")["pause"]["type"] == "confirm"  # the one match


def test_status_before_applying(case_id):
    assert turn(case_id, "status")["reply"].startswith(t("status_none", "en"))


def test_lang_from_script_when_client_sends_none(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ")
    assert state(case_id)["lang"] == "kn"
    assert kannada(turn(case_id, "120000")["reply"])  # digits keep the case's language


# --- LLM merge ---------------------------------------------------------------------------


def test_llm_number_is_fallback_and_flagged(case_id, fake_llm):
    msg = "income is around one-twenty"  # our parser can't read "one-twenty" as 1,20,000
    fake_llm.extractions[msg] = Extraction(intent="info", annual_income=120000)
    turn(case_id, "I'm 62, pension?")
    turn(case_id, msg)
    s = state(case_id)
    assert s["profile"]["annual_income"] == 120000
    assert s["sources"]["annual_income"] in ("llm", "llm_attributed")
    out = turn(case_id, "senior citizen")
    assert out["pause"]["preview"]["needs_readback"] == ["annual_income"]
    assert t("readback_unsure", "en", fields="annual income ₹1,20,000") in out["reply"]


def test_parser_beats_llm_conversion(case_id, fake_llm):
    # The real failure from testing: the LLM read "1 lakh 20 thousand" as 102000.
    msg = "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ ರೂಪಾಯಿ ಆದಾಯ, ವಯಸ್ಸು 62"
    fake_llm.extractions[msg] = Extraction(intent="info", annual_income=102000, age=62)
    turn(case_id, msg, "kn")
    s = state(case_id)
    assert s["profile"]["annual_income"] == 120000
    assert s["sources"]["annual_income"] == "number_words"
    assert "annual_income" not in s["readback"]


def test_llm_picks_field_but_value_comes_from_parser(case_id, fake_llm):
    msg = "it is 62 for me"  # no cue word: parser can't tell what 62 is
    fake_llm.extractions[msg] = Extraction(intent="info", age=62)
    turn(case_id, msg)
    assert state(case_id)["profile"]["age"] == 62
    assert state(case_id)["sources"]["age"] == "llm_attributed"


def test_llm_scheme_choice_is_a_fallback(case_id, fake_llm):
    turn(case_id, "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?")
    msg = "the one for poor old people with no other support"
    fake_llm.extractions[msg] = Extraction(intent="proceed", scheme="pension-002")
    out = turn(case_id, msg)
    assert out["pause"]["preview"]["scheme_id"] == "pension-002"  # still read back + gated


def test_short_deterministic_answer_skips_llm(case_id, fake_llm):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")
    fake_llm.calls.clear()
    turn(case_id, "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ")
    turn(case_id, "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ")
    assert fake_llm.calls == []


def test_free_form_question_answered_then_question_repeated(case_id, fake_llm):
    turn(case_id, "I'm 62, pension?")
    q = "why do you need my income?"
    fake_llm.extractions[q] = Extraction(intent="question")
    fake_llm.answers[q] = "The pension has an income limit, so I need it to check."
    out = turn(case_id, q)
    assert out["reply"] == f"The pension has an income limit, so I need it to check. {t('ask_annual_income', 'en')}"


def test_llm_down_still_answers(case_id, fake_llm):
    fake_llm.state = "error"
    turn(case_id, "I'm 62, pension?")
    q = "what is this scheme?"
    fake_llm.extractions[q] = Extraction(intent="question")  # answer() returns None = failed
    assert turn(case_id, q)["reply"].startswith(t("llm_unavailable", "en"))


def test_llm_never_decides_eligibility(case_id, fake_llm):
    # Even if the LLM claimed the citizen were eligible, only the rule decides.
    msg = "I'm 45 and I really need the pension, I am definitely eligible"
    fake_llm.extractions[msg] = Extraction(intent="proceed", age=60, scheme="pension-002")
    out = turn(case_id, msg)
    assert state(case_id)["profile"]["age"] == 45  # parser's "I'm 45" wins
    assert out["pause"] is None and "you don't qualify" in out["reply"]
