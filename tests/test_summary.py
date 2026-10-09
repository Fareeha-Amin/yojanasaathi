"""Phase 5 backend for the web app: GET /cases/{id}/summary (the screens), English
subtitles, and edits at the confirm pause (review screen + spoken). An edit never submits."""

import uuid

import pytest

from agent.main import app, graph
from agent.replies import t
from tests.helpers import CaseClient

client = CaseClient(app)

EN_READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"
KN_PENSION = "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ"


@pytest.fixture
def case_id() -> str:
    return f"sum-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200
    return r.json()


def summary(case_id: str, lang: str | None = None) -> dict:
    r = client.get(f"/cases/{case_id}/summary", params={"lang": lang} if lang else None)
    assert r.status_code == 200
    return r.json()


def edit(case_id: str, field: str, value: int):
    return client.post(f"/cases/{case_id}/edit", json={"field": field, "value": value})


def to_confirm(case_id: str) -> dict:
    turn(case_id, EN_READY)
    out = turn(case_id, "senior citizen pension")
    assert out["pause"]["type"] == "confirm"
    return out


def audit_actions(case_id: str) -> list[str]:
    return [a["action"] for a in client.get(f"/cases/{case_id}/data").json()["audit"]]


# --- summary --------------------------------------------------------------------------


def test_empty_case_has_a_summary(case_id):
    out = summary(case_id)
    assert out["profile"] == [] and out["applications"] == [] and out["pause"] is None
    assert {s["status"] for s in out["schemes"]} == {"unknown"}
    assert out["checklist"]["items"] == []


def test_interview_why_i_ask(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")
    out = summary(case_id)
    assert out["lang"] == "kn"
    assert out["profile"][0] | {"source": None} == {
        "field": "age", "label": "ವಯಸ್ಸು", "label_en": "Age", "value": 62, "text": "62", "text_en": "62",
        "source": None, "unsure": False}
    ask = out["asking"]
    assert ask["kind"] == "field" and ask["field"] == "annual_income"
    assert (ask["label"], ask["label_en"]) == ("ವಾರ್ಷಿಕ ಆದಾಯ", "Annual income")  # the "?" chip
    assert ask["question"] == t("ask_annual_income", "kn")
    assert ask["question_en"] == t("ask_annual_income", "en")
    # pension topic: only the pension scheme that still needs income is named
    assert "Senior Citizen Pension Scheme" in ask["why_en"] and "Health" not in ask["why_en"]
    assert KN_PENSION in ask["why"]


def test_four_matches_screen(case_id):
    turn(case_id, KN_READY, "kn")
    out = summary(case_id)
    assert [s["status"] for s in out["schemes"]] == ["eligible"] * 4
    first = out["schemes"][0]
    assert first["scheme_id"] == "pension-001" and first["title"] == KN_PENSION
    assert first["title_en"] == "Senior Citizen Pension Scheme"
    assert first["reasons_en"] == ["age 62, at least 60 needed", "annual income ₹1,20,000, limit ₹3,00,000"]
    assert {c["field"] for c in first["clauses"]} == {"age", "annual_income"}
    assert first["source_url"] and first["effective_date"] == "2026-10-09"
    assert all("label_en" in d for d in first["documents"])
    assert [p["text_en"] for p in out["profile"]] == ["62", "₹1,20,000"]
    assert out["asking"]["kind"] == "choose"
    assert [n["scheme_id"] for n in out["next"]] == ["pension-001", "pension-002", "health-001", "health-002"]
    # Documents screen: every document the 4 schemes need, once each, with the schemes named
    items = out["checklist"]["items"]
    assert len({i["doc"] for i in items}) == len(items) and out["checklist"]["total"] == len(items)
    assert all(i["status"] == "needed" for i in items)
    assert "Senior Citizen Pension Scheme" in next(i for i in items if i["doc"] == "age_proof")["schemes_en"]


def test_summary_in_another_language(case_id):
    turn(case_id, EN_READY)
    out = summary(case_id, "hi")
    assert out["lang"] == "hi" and out["case_lang"] is None
    assert out["schemes"][0]["title"] == "वरिष्ठ नागरिक पेंशन योजना"


def test_not_eligible_reasons(case_id):
    turn(case_id, "I'm 45 and our income is 6 lakh")
    s = {x["scheme_id"]: x for x in summary(case_id)["schemes"]}
    assert s["pension-001"]["status"] == "not_eligible"
    assert "age 45, at least 60 needed" in s["pension-001"]["reasons_en"]


def test_question_left_names_the_field(case_id):
    turn(case_id, "I'm 62")
    s = {x["scheme_id"]: x for x in summary(case_id)["schemes"]}
    assert s["pension-002"]["status"] == "eligible"
    assert s["pension-001"]["status"] == "unknown"
    assert s["pension-001"]["missing_fields"] == [
        {"field": "annual_income", "label": "Annual income", "label_en": "Annual income",  # display labels
         "question": "What is your family's total income in one year?",
         "question_en": "What is your family's total income in one year?"}]


def test_review_from_the_confirm_pause(case_id):
    to_confirm(case_id)
    out = summary(case_id, "kn")
    r = out["review"]
    assert out["pause"]["type"] == "confirm"
    assert r["scheme_id"] == "pension-001" and r["title"] == KN_PENSION
    assert [(f["field"], f["text"], f["editable"]) for f in r["fields"]] == [
        ("age", "62", True), ("annual_income", "₹1,20,000", True)]
    assert "₹1,20,000" in r["readback_en"]
    # documents are missing: said before the yes is asked for (not blocked)
    assert "5 documents for Senior Citizen Pension Scheme are still missing" in r["readback_en"]
    assert r["documents_missing"] == 5
    # the portal's own form fields, values filled in Phase 4
    assert len(r["form_fields"]) == 11
    # already answered -> pre-filled "from your answers"; the rest waits for Phase 4's pre-fill
    known = {f["name"]: f for f in r["form_fields"] if f["from_answers"]}
    assert list(known) == ["annual_income"] and known["annual_income"]["text"] == "₹1,20,000"
    assert all(f["value"] is None for f in r["form_fields"] if f["name"] != "annual_income")
    assert r["form_fields"][-1]["name"] == "declaration_consent"
    assert len(r["documents"]) == 5


def test_applications_timeline_and_next(case_id):
    to_confirm(case_id)
    turn(case_id, "yes")
    out = summary(case_id)
    [app_] = out["applications"]
    assert app_["app_id"] == "DEMO-0001" and app_["status"] == "SUBMITTED"
    assert app_["status_text_en"] == "Submitted"  # badge, title case
    kinds = [e["kind"] for e in app_["timeline"]]
    assert kinds == ["eligibility_decided", "confirm_requested", "citizen_approved", "submitted"]
    assert app_["timeline"][-1]["app_id"] == "DEMO-0001"
    assert [n["scheme_id"] for n in out["next"]] == ["pension-002", "health-001", "health-002"]
    assert out["review"] is None and out["pause"] is None


def test_documents_show_as_uploaded_with_masked_aadhaar(case_id):
    turn(case_id, EN_READY)
    client.put(f"/cases/{case_id}/consent", json={"documents": True})
    r = client.put(f"/cases/{case_id}/documents/identity_proof?aadhaar_last4=0123",
                   content=b"\xff\xd8 fake jpeg", headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    out = summary(case_id)
    item = next(i for i in out["checklist"]["items"] if i["doc"] == "identity_proof")
    assert item["status"] == "uploaded" and item["document"]["aadhaar"] == "XXXX XXXX 0123"
    assert out["checklist"]["items"][-1]["doc"] == "identity_proof"  # missing / needed first
    assert out["checklist"]["ready"] == 1


def test_summary_never_holds_full_aadhaar_or_tokens(case_id):
    turn(case_id, "my aadhaar is 2345 6789 0123 and I'm 62")
    text = str(summary(case_id))
    assert "2345 6789" not in text and "234567890123" not in text
    assert "Bearer" not in text and "token" not in text.lower()


# --- subtitles ------------------------------------------------------------------------


def test_subtitle_is_english_for_kannada_and_none_for_english(case_id):
    out = turn(case_id, KN_READY, "kn")
    assert out["subtitle"].startswith("You qualify for 4 schemes")
    assert out["reply"] != out["subtitle"]
    assert turn(f"{case_id}-en", EN_READY)["subtitle"] is None


def test_subtitle_follows_resume(case_id):
    turn(case_id, KN_READY, "kn")
    paused = turn(case_id, KN_PENSION)
    assert "Senior Citizen Pension Scheme are still missing: say yes to submit anyway" in paused["subtitle"]
    done = turn(case_id, "ಹೌದು")
    assert done["subtitle"].startswith("Submitted! Your application ID is DEMO-0001.")


def test_llm_answer_has_no_subtitle(case_id, fake_llm):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ", "kn")
    fake_llm.answers["ಪಿಂಚಣಿ ಎಷ್ಟು ಸಿಗುತ್ತದೆ?"] = "ಅದು ಪೋರ್ಟಲ್ ನಿರ್ಧರಿಸುತ್ತದೆ."
    from agent.llm import Extraction

    fake_llm.extractions["ಪಿಂಚಣಿ ಎಷ್ಟು ಸಿಗುತ್ತದೆ?"] = Extraction(intent="question")
    out = turn(case_id, "ಪಿಂಚಣಿ ಎಷ್ಟು ಸಿಗುತ್ತದೆ?")
    assert out["reply"].startswith("ಅದು ಪೋರ್ಟಲ್") and out["subtitle"] is None


# --- edits at the confirm pause -------------------------------------------------------


def test_review_edit_reconfirms_never_submits(case_id, ):
    to_confirm(case_id)
    r = edit(case_id, "annual_income", 150000)
    assert r.status_code == 200
    out = r.json()
    assert out["pause"]["type"] == "confirm"  # a NEW read-back and pause
    assert out["pause"]["preview"]["fields"]["annual_income"] == 150000
    assert "₹1,50,000" in out["reply"] and "DEMO" not in out["reply"]
    assert "fields_edited" in audit_actions(case_id) and "submitted" not in audit_actions(case_id)
    state = graph.get_state({"configurable": {"thread_id": case_id}}).values
    assert state["sources"]["annual_income"] == "edited" and not state.get("applications")
    done = turn(case_id, "yes")
    assert "DEMO-0001" in done["reply"]


def test_review_edit_that_breaks_eligibility_stops(case_id):
    to_confirm(case_id)
    out = edit(case_id, "annual_income", 400000).json()
    assert out["pause"] is None
    assert "don't qualify" in out["reply"] and "₹4,00,000" in out["reply"]
    assert summary(case_id)["applications"] == []


def test_edit_audit_has_field_names_not_values(case_id):
    to_confirm(case_id)
    edit(case_id, "age", 63)
    row = next(a for a in client.get(f"/cases/{case_id}/data").json()["audit"] if a["action"] == "fields_edited")
    assert row["detail"] == {"fields": ["age"], "via": "review_screen"}


def test_edit_needs_a_confirm_pause(case_id):
    turn(case_id, EN_READY)
    assert edit(case_id, "age", 63).status_code == 409


@pytest.mark.parametrize("field, value", [("age", 5), ("age", 200), ("annual_income", -1)])
def test_edit_values_validated(case_id, field, value):
    to_confirm(case_id)
    assert edit(case_id, field, value).status_code == 422


def test_edit_unknown_field_rejected(case_id):
    to_confirm(case_id)
    assert edit(case_id, "district", 1).status_code == 422


@pytest.mark.parametrize("said", ["no, my income is 2 lakh", "yes, my income is 2 lakh"])
def test_spoken_edit_at_confirm_reconfirms(case_id, said):
    to_confirm(case_id)
    out = turn(case_id, said)
    assert out["pause"]["type"] == "confirm"  # even with "yes": a changed value is re-read first
    assert out["pause"]["preview"]["fields"]["annual_income"] == 200000
    assert "submitted" not in audit_actions(case_id)
    assert "DEMO-0001" in turn(case_id, "ಹೌದು")["reply"]


def test_same_value_is_not_an_edit(case_id):
    to_confirm(case_id)
    out = turn(case_id, "yes, I'm 62")
    assert out["pause"] is None and "DEMO-0001" in out["reply"]


# --- Phase 5 fix-up, part C ------------------------------------------------------------


def upload(case_id: str, *doc_types: str) -> None:
    client.put(f"/cases/{case_id}/consent", json={"documents": True})
    for d in doc_types:
        r = client.put(f"/cases/{case_id}/documents/{d}", content=b"%PDF-1.4 x",
                       headers={"Content-Type": "application/pdf"})
        assert r.status_code == 200


def test_why_i_ask_names_two_schemes_then_n_more(case_id):
    turn(case_id, "I'm 62")  # no topic: income decides 3 schemes
    why = summary(case_id)["asking"]["why_en"]
    assert why == ("I ask your annual income to check: Senior Citizen Pension Scheme, "
                   "National Health Support Scheme and 1 more.")


def test_readback_names_missing_documents_and_counts_uploads(case_id):
    to_confirm(case_id)
    upload(case_id, "identity_proof", "age_proof", "residence_proof", "income_certificate")
    r = summary(case_id)["review"]
    assert r["documents_missing"] == 1
    assert "1 document for Senior Citizen Pension Scheme is still missing: say yes to submit anyway" in r["readback_en"]
    upload(case_id, "bank_account_details")
    r = summary(case_id)["review"]
    assert r["documents_missing"] == 0
    assert r["readback_en"].endswith("Shall I submit your application for Senior Citizen Pension Scheme? Say yes to submit.")


def test_missing_documents_do_not_block_submit(case_id):
    to_confirm(case_id)
    assert "DEMO-0001" in turn(case_id, "yes")["reply"]  # Phase 4 decides what the portal needs


def test_documents_for_the_chosen_scheme_others_collapsed(case_id):
    to_confirm(case_id)
    c = summary(case_id)["checklist"]
    assert c["title_en"] == "Senior Citizen Pension Scheme" and c["scheme_ids"] == ["pension-001"]
    assert [i["doc"] for i in c["items"]] == [
        "identity_proof", "age_proof", "residence_proof", "income_certificate", "bank_account_details"]
    assert {i["doc"] for i in c["others"]} == {"medical_documents", "family_details"}
    assert (c["total"], c["missing"], c["ready"]) == (5, 5, 0)


def test_no_scheme_chosen_lists_all_qualifying_documents(case_id):
    turn(case_id, EN_READY)
    c = summary(case_id)["checklist"]
    assert c["title"] is None and c["others"] == [] and c["total"] == 7


def test_application_what_to_do_lists_missing_documents(case_id):
    to_confirm(case_id)
    upload(case_id, "identity_proof")
    turn(case_id, "yes")
    [a] = summary(case_id)["applications"]
    assert [d["label_en"] for d in a["missing_documents"]] == [
        "Age Proof", "Residence Proof", "Income Certificate", "Bank Account Details"]


def test_category_tag_from_the_rules(case_id):
    turn(case_id, KN_READY, "kn")
    s = {x["scheme_id"]: x for x in summary(case_id)["schemes"]}
    assert (s["pension-001"]["category_en"], s["pension-001"]["category"]) == ("Pension", "ಪಿಂಚಣಿ")
    assert s["health-002"]["category_en"] == "Health"


def test_review_effective_date_in_words(case_id):
    to_confirm(case_id)
    r = summary(case_id, "kn")["review"]
    assert r["effective_date_text_en"] == "9 October 2026"
