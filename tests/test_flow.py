"""Phase 2 agent brain through /turn: interview -> eligibility -> checklist -> confirm.

The LLM is a FakeLLM (tests/conftest.py): these tests prove the golden path works on the
deterministic parsers alone, and how LLM output is merged when it does come.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from agent.llm import Extraction
from agent.main import app, graph
from agent.replies import t

client = TestClient(app)


@pytest.fixture
def case_id() -> str:
    return f"flow-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"reply", "pause"}
    return out


def state(case_id: str) -> dict:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values


def kannada(text: str) -> bool:
    return any(0x0C80 <= ord(c) < 0x0D00 for c in text)


def test_golden_path_kannada(case_id):
    # 1. The pension line: asks only the missing field (income), in Kannada.
    out = turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")
    assert out == {"reply": t("ask_annual_income", "kn"), "pause": None}
    assert state(case_id)["missing"] == ["annual_income"]  # not gender, student, land...

    # 2. Income in number words -> eligible, with reason, source and date, and checklist.
    out = turn(case_id, "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ")
    r = out["reply"]
    assert out["pause"] is None and kannada(r)
    assert "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ: ನೀವು ಅರ್ಹರು" in r
    assert "ವಯಸ್ಸು 62, ಕನಿಷ್ಠ 60 ಬೇಕು" in r and "₹1,20,000, ಮಿತಿ ₹3,00,000" in r
    assert "ಮೂಲ: ಡೆಮೊ ಪೋರ್ಟಲ್" in r and "1 ಏಪ್ರಿಲ್ 2026" in r
    assert "5 ದಾಖಲೆಗಳು" in r and r.endswith(t("ask_proceed", "kn"))
    [result] = state(case_id)["eligible"]
    assert result["scheme_id"] == "pension-001"
    assert result["source_url"] and result["effective_date"] == "2026-04-01"
    assert state(case_id)["profile"] == {"age": 62, "annual_income": 120000}

    # 3. "ಹೌದು" to filling the form -> read-back + confirm pause (not a submit).
    out = turn(case_id, "ಹೌದು")
    assert out["pause"]["type"] == "confirm"
    assert out["pause"]["preview"]["fields"] == {"age": 62, "annual_income": 120000}
    assert "ವಯಸ್ಸು 62" in out["reply"] and "₹1,20,000" in out["reply"]

    # 4. "ಹೌದು" at the gate -> submitted, in Kannada.
    out = turn(case_id, "ಹೌದು")
    assert out == {"reply": t("submitted", "kn", app_id="DEMO-0001"), "pause": None}


def test_golden_path_hindi(case_id):
    assert turn(case_id, "मैं 62 साल की हूँ। क्या मुझे पेंशन मिल सकती है?", "hi")["reply"] == \
        t("ask_annual_income", "hi")
    r = turn(case_id, "एक लाख बीस हज़ार")["reply"]
    assert "वरिष्ठ नागरिक पेंशन योजना: आप पात्र हैं" in r and "स्रोत:" in r
    assert turn(case_id, "हाँ")["pause"]["type"] == "confirm"
    assert "DEMO-0001" in turn(case_id, "हाँ")["reply"]


def test_golden_path_english_one_message(case_id):
    out = turn(case_id, "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?")
    assert "Senior Citizen Pension Scheme: you qualify" in out["reply"]
    assert "Source: the demo portal's pension rule, in force from 1 April 2026" in out["reply"]
    assert turn(case_id, "proceed")["pause"]["type"] == "confirm"


def test_not_eligible_explains_why(case_id):
    out = turn(case_id, "I'm 55, pension?")
    assert out["pause"] is None
    assert "Senior Citizen Pension Scheme: you don't qualify right now" in out["reply"]
    assert "age 55, at least 60 needed" in out["reply"]
    assert "annual income" not in out["reply"]  # decided without asking income
    assert turn(case_id, "proceed")["pause"] is None  # nothing to apply for


def test_no_topic_asks_across_schemes_in_order(case_id):
    out = turn(case_id, "hello")
    assert out["reply"] == f"{t('greeting', 'en')} {t('ask_age', 'en')}"
    assert state(case_id)["missing"][0] == "age"


def test_only_needed_fields_are_asked(case_id):
    # Scholarship scope: age is not a rule field there, so it's never asked.
    turn(case_id, "scholarship please")
    asked = []
    for answer in ["2 lakh", "yes", "SC"]:
        asked.append(state(case_id)["asking"])
        out = turn(case_id, answer)
    assert asked == ["annual_income", "is_student", "category"]
    assert "Post-Matric Scholarship for SC students: you qualify" in out["reply"]


def test_checklist_with_documents_the_citizen_has(case_id):
    turn(case_id, "I'm 62 and our income is 1 lakh. pension?")
    out = turn(case_id, "I have aadhaar, photo and bank passbook but no income certificate")
    assert "You have 3 of 5; still needed: income certificate and residence certificate." in out["reply"]
    assert state(case_id)["asking"] == "proceed"


def test_decline_then_proceed_later(case_id):
    turn(case_id, "I'm 62 and our income is 1 lakh. pension?")
    assert turn(case_id, "no")["reply"] == t("proceed_declined", "en")
    assert turn(case_id, "apply")["pause"]["type"] == "confirm"


def test_status_before_applying(case_id):
    assert turn(case_id, "status")["reply"].startswith(t("status_none", "en"))
    turn(case_id, "I'm 62 and our income is 1 lakh. pension?")
    assert "say proceed to apply" in turn(case_id, "what is my application status?")["reply"]


def test_lang_from_script_when_client_sends_none(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ")
    assert state(case_id)["lang"] == "kn"
    assert kannada(turn(case_id, "120000")["reply"])  # digits keep the case's language


# --- LLM merge ---------------------------------------------------------------------------


def test_llm_fills_free_text_facts_flagged_for_readback(case_id, fake_llm):
    msg = "my wife and I are both above sixty and live in Mysore"
    fake_llm.extractions[msg] = Extraction(intent="info", district="Mysuru", topic="pension")
    turn(case_id, msg)
    s = state(case_id)
    assert s["profile"]["district"] == "Mysuru"
    assert s["topics"] == ["pension"]
    assert s["sources"]["district"] == "lookup"  # deterministic lookup found it first


def test_llm_number_is_fallback_and_flagged(case_id, fake_llm):
    msg = "income is around one-twenty"  # our parser can't read "one-twenty" as 1,20,000
    fake_llm.extractions[msg] = Extraction(intent="info", annual_income=120000)
    turn(case_id, "I'm 62, pension?")
    out = turn(case_id, msg)
    s = state(case_id)
    assert s["profile"]["annual_income"] == 120000
    assert s["sources"]["annual_income"] in ("llm", "llm_attributed")
    assert "annual_income" in s["readback"]
    out = turn(case_id, "yes")
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


def test_short_deterministic_answer_skips_llm(case_id, fake_llm):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")
    fake_llm.calls.clear()
    turn(case_id, "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ")
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
    fake_llm.extractions[msg] = Extraction(intent="proceed", age=60)
    out = turn(case_id, msg)
    assert state(case_id)["profile"]["age"] == 45  # parser's "I'm 45" wins
    assert "you don't qualify" in out["reply"] and out["pause"] is None
