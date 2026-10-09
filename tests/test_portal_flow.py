"""Phase 4 flow through /turn with the in-process FakeDriver (tests/fake_driver.py):
documents -> form questions -> OTP pause -> pre-fill -> confirm pause -> explicit yes ->
the portal's Submit. The browser itself is tested in tests/test_portal_browser.py."""

import json
import uuid

import pytest

from agent import sealed
from agent.main import app, graph, store
from agent.portal import SafeStop
from tests.helpers import FORM_ANSWERS, OTP, CaseClient, answer_form, dob_for, to_confirm, upload_documents

client = CaseClient(app)
READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"


@pytest.fixture
def case_id() -> str:
    return f"portal-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"reply", "pause", "ui", "subtitle"}  # the contract is unchanged
    return out


def start(case_id: str, ready: str = READY, scheme: str = "pension-001", lang: str | None = None) -> dict:
    """Interview -> pick the scheme (documents already uploaded) -> first form question."""
    upload_documents(client, case_id, scheme)
    turn(case_id, ready, lang)
    out = turn(case_id, {"pension-001": "senior citizen pension", "pension-002": "social security pension",
                         "health-001": "national health support", "health-002": "family health"}[scheme])
    assert (out["ui"] or {}).get("type") == "form", out
    return out


def values(case_id: str) -> dict:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values


def confirm(case_id: str, **kw) -> dict:
    out = start(case_id)
    return to_confirm(lambda t: turn(case_id, t), out, **kw)


# --- the golden path ------------------------------------------------------------------

def test_full_flow_submits_once(case_id, fake_driver):
    out = start(case_id)
    assert out["pause"] is None
    assert "few details" in out["reply"] and "full name" in out["reply"]
    out = answer_form(lambda t: turn(case_id, t), out)
    assert out["pause"]["type"] == "otp"
    assert out["pause"]["masked_mobile"] == "XXXXXX3210"
    assert "ending 3210" in out["reply"] and "6-digit" in out["reply"]
    assert fake_driver.calls.count("login") == 1 and "fill" not in fake_driver.calls

    out = turn(case_id, OTP)
    assert out["pause"]["type"] == "confirm"
    preview = out["pause"]["preview"]
    assert preview["portal"] is True and preview["scheme_id"] == "pension-001"
    assert "not drawing any other" in preview["declaration"]["text_en"]
    assert len(preview["screenshots"]) >= 4
    # read-back: name, date of birth (spoken in full here, at the edge), income, account end,
    # the declaration, 24-hour read-only access, the question
    assert "Ramesh Kumar" in out["reply"] and f"1 January {dob_for(62)[-4:]}" in out["reply"]
    assert "₹1,20,000" in out["reply"] and "ending 0123" in out["reply"]
    assert "not drawing any other" in out["reply"] and "24 hours" in out["reply"]
    assert fake_driver.submit_count() == 0  # nothing submitted before the yes

    out = turn(case_id, "ಹೌದು")
    assert out["pause"] is None
    app_id = values(case_id)["applications"]["pension-001"]["app_id"]
    assert app_id.startswith("YJS-") and app_id in out["reply"]
    assert out["ui"]["type"] == "submitted"
    assert fake_driver.submit_count("pension-001") == 1
    assert "close:submitted" in fake_driver.calls
    d = values(case_id)["delegation"]
    assert sealed.is_sealed(d["sealed"]) and d["scopes"] == ["applications:read", "notifications:read"]
    assert sealed.open_(case_id, "delegation", d["sealed"]).startswith("yjs_del_")

    again = turn(case_id, "ಹೌದು")  # a repeated yes: the same number, never a second Submit
    assert app_id in again["reply"] and fake_driver.submit_count() == 1


def test_documents_go_from_the_vault_to_the_portal(case_id, fake_driver):
    confirm(case_id)
    docs = {d for (c, d, n) in fake_driver.uploaded if c == case_id}
    assert docs == {"identity_proof", "age_proof", "residence_proof", "income_certificate", "bank_account_details"}
    audit = [r["action"] for r in store.case_data(case_id)["audit"]]
    assert audit.count("document_read") == 5  # each decrypted once, for the upload, audited


def test_kannada_answers(case_id, fake_driver):
    out = start(case_id, KN_READY, lang="kn")
    assert "ಪೂರ್ಣ ಹೆಸರು" in out["reply"]
    kn = {"full_name": "ರಮೇಶ್ ಕುಮಾರ್", "gender": "ಪುರುಷ", "marital_status": "ವಿಧವೆ",
          "disbursement_mode": "ಅಂಚೆ ಕಚೇರಿ", "nominee_name": "ಸೀತಾ", "address": "12 ದೇವಸ್ಥಾನ ರಸ್ತೆ, ತುಮಕೂರು",
          "declaration_consent": "ಹೌದು", "bank_account_number": "ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು"}
    out = to_confirm(lambda t: turn(case_id, t), out, answers=kn)
    assert "ಘೋಷಣೆ" in out["reply"] or "ದೃಢೀಕರಿಸು" in out["reply"]
    typed = fake_driver.values[case_id]
    assert typed["full_name"] == "ರಮೇಶ್ ಕುಮಾರ್"
    assert typed["marital_status"] == "Widowed"  # the portal's exact English option
    assert typed["disbursement_mode"] == "Post Office Savings Bank (POSB)"
    assert typed["bank_account_number"] == "123456789"
    assert typed["declaration_consent"] is True


def test_choices_are_read_aloud_in_the_citizens_language(case_id):
    out = start(case_id, KN_READY, lang="kn")
    while (out["ui"] or {}).get("field") != "marital_status":
        out = turn(case_id, {"full_name": "Ramesh", "dob": dob_for(62), "gender": "male"}[out["ui"]["field"]])
    assert "ವಿವಾಹಿತ" in out["reply"] and "ವಿಚ್ಛೇದಿತ" in out["reply"]  # the portal's own kn options


# --- before the browser -------------------------------------------------------------------

def test_missing_documents_stop_before_the_browser(case_id, fake_driver):
    upload_documents(client, case_id, "pension-001", skip=("age_proof", "income_certificate"))
    turn(case_id, READY)
    out = turn(case_id, "senior citizen pension")
    assert out["pause"] is None
    assert "Age Proof" in out["reply"] and "Income Certificate" in out["reply"]
    assert "login" not in fake_driver.calls
    upload_documents(client, case_id, "pension-001")
    out = turn(case_id, "continue")
    assert (out["ui"] or {}).get("type") == "form"


def test_unusable_document_format(case_id):
    upload_documents(client, case_id, "pension-001")
    client.put(f"/cases/{case_id}/documents/age_proof", content=b"RIFF....WEBP",
               headers={"Content-Type": "image/webp"}).raise_for_status()
    turn(case_id, READY)
    out = turn(case_id, "senior citizen pension")
    assert "PDF, JPG or PNG" in out["reply"] and "Age Proof" in out["reply"]


def test_unreadable_answer_asks_again(case_id):
    start(case_id)
    turn(case_id, "Ramesh Kumar")
    out = turn(case_id, "sometime in spring")
    assert "didn't get that" in out["reply"] and "date of birth" in out["reply"]
    assert values(case_id)["asking"] == "form:dob"


def test_no_after_a_readback_asks_that_field_again(case_id):
    start(case_id)
    turn(case_id, "Ramesh Kumar")
    out = turn(case_id, dob_for(62))
    assert "I heard 1 January" in out["reply"]
    out = turn(case_id, "no")
    assert "correct" in out["reply"] and "date of birth" in out["reply"]


def test_date_of_birth_must_match_the_age(case_id):
    start(case_id)
    turn(case_id, "Ramesh Kumar")
    out = turn(case_id, "1 January 1990")
    assert "makes you" in out["reply"] and "62" in out["reply"]
    assert "dob" not in (values(case_id).get("form") or {})


def test_declaration_no_stops(case_id, fake_driver):
    out = start(case_id)
    out = answer_form(lambda t: turn(case_id, t), out, answers={"declaration_consent": "no"})
    assert out["pause"] is None and "Without this declaration" in out["reply"]
    assert "login" not in fake_driver.calls


def test_refused_mobile_is_asked_again(case_id, fake_driver):
    fake_driver.refuse_mobiles.add("9876543210")
    out = start(case_id)
    out = answer_form(lambda t: turn(case_id, t), out)
    assert out["pause"] is None and "did not accept" in out["reply"]
    assert values(case_id)["asking"] == "form:mobile"


def test_gender_said_earlier_is_not_asked(case_id):
    upload_documents(client, case_id, "pension-001")
    turn(case_id, "I am a woman, 62 years old, income 1 lakh 20 thousand")
    out = turn(case_id, "senior citizen pension")
    asked = []
    for _ in range(15):
        f = (out["ui"] or {}).get("field")
        if not f:
            break
        asked.append(f)
        out = turn(case_id, {**FORM_ANSWERS, "dob": dob_for(62)}[f])
    assert "gender" not in asked and "full_name" in asked


# --- OTP --------------------------------------------------------------------------------

def otp_pause(case_id: str) -> dict:
    out = answer_form(lambda t: turn(case_id, t), start(case_id))
    assert out["pause"]["type"] == "otp"
    return out


def test_wrong_otp_three_times_stops(case_id, fake_driver):
    otp_pause(case_id)
    out = turn(case_id, "111111")
    assert out["pause"]["type"] == "otp" and "2 tries left" in out["reply"]
    assert out["pause"]["attempts_left"] == 2
    turn(case_id, "222222")
    out = turn(case_id, "333333")
    assert out["pause"] is None and "three times" in out["reply"]
    assert "close:otp_failed" in fake_driver.calls and "fill" not in fake_driver.calls
    rejected = [r for r in store.case_data(case_id)["audit"] if r["action"] == "otp_rejected"]
    assert len(rejected) == 3


def test_wrong_then_right_otp(case_id):
    otp_pause(case_id)
    turn(case_id, "999999")
    out = turn(case_id, "one two three four five six")  # digit words work too
    assert out["pause"]["type"] == "confirm"


def test_resend_and_unclear(case_id, fake_driver):
    otp_pause(case_id)
    out = turn(case_id, "what code?")
    assert out["pause"]["type"] == "otp" and "6-digit" in out["reply"]
    out = turn(case_id, "please send again")
    assert out["pause"]["type"] == "otp" and "new code" in out["reply"]
    assert "resend" in fake_driver.calls


def test_expired_code_offers_a_new_one(case_id, monkeypatch):
    from agent import config

    otp_pause(case_id)
    monkeypatch.setattr(config, "OTP_TTL_SECONDS", -1)
    out = turn(case_id, "111111")
    assert "expired" in out["reply"] and "send again" in out["reply"]


def test_cancel_at_otp(case_id, fake_driver):
    otp_pause(case_id)
    out = turn(case_id, "no, stop")
    assert out["pause"] is None and "nothing was submitted" in out["reply"]
    assert "close:cancelled" in fake_driver.calls


# --- review + edits -----------------------------------------------------------------------

def test_spoken_edit_of_a_sensitive_field_fills_again(case_id, fake_driver):
    confirm(case_id)
    out = turn(case_id, "no, the account number is 5555 6666 777")
    assert out["pause"]["type"] == "confirm"  # a new read-back, never a submit
    assert "ending 6777" in out["reply"]
    assert fake_driver.calls.count("fill") == 2 and fake_driver.submit_count() == 0
    assert fake_driver.values[case_id]["bank_account_number"] == "55556666777"
    out = turn(case_id, "yes")
    assert "YJS-" in out["reply"]


def test_edit_by_naming_a_field(case_id, fake_driver):
    confirm(case_id)
    out = turn(case_id, "change the nominee")
    assert out["pause"] is None and "Nominee" in out["reply"]
    out = turn(case_id, "Lakshmi Devi")
    assert out["pause"]["type"] == "confirm"
    assert fake_driver.values[case_id]["nominee_name"] == "Lakshmi Devi"
    assert fake_driver.submit_count() == 0


def test_yes_with_a_field_named_never_submits(case_id, fake_driver):
    confirm(case_id)
    out = turn(case_id, "yes but my name is wrong")
    assert out["pause"] is None and fake_driver.submit_count() == 0


def test_income_edit_on_the_review_screen_fills_again(case_id, fake_driver):
    confirm(case_id)
    r = client.post(f"/cases/{case_id}/edit", json={"field": "annual_income", "value": 200000})
    assert r.status_code == 200
    out = r.json()
    assert out["pause"]["type"] == "confirm" and "₹2,00,000" in out["reply"]
    assert fake_driver.values[case_id]["annual_income"] == "200000"
    assert fake_driver.submit_count() == 0


def test_stale_session_restarts_from_login(case_id, fake_driver):
    confirm(case_id)
    fake_driver.expire(case_id)  # idle timeout / agent restart: the open page is gone
    out = turn(case_id, "yes")
    assert out["pause"]["type"] == "otp"  # log in again, a new preview, a new yes
    assert "logging in again" in out["reply"]
    assert fake_driver.submit_count() == 0
    out = turn(case_id, OTP)
    assert out["pause"]["type"] == "confirm"
    out = turn(case_id, "yes")
    assert "YJS-" in out["reply"] and fake_driver.submit_count() == 1


# --- safe-stop, drift, already submitted -----------------------------------------------------

def test_safe_stop_hands_back_then_retry(case_id, fake_driver):
    otp_pause(case_id)
    fake_driver.fail["fill"] = SafeStop("expected element missing", step="details",
                                        detail={"testid": "apply-step2-next-btn"})
    out = turn(case_id, OTP)
    assert out["pause"]["type"] == "safe_stop"
    assert out["pause"]["step"] == "details" and "Nothing was submitted" in out["reply"]
    row = [r for r in store.case_data(case_id)["audit"] if r["action"] == "safe_stop"][-1]
    assert row["detail"]["testid"] == "apply-step2-next-btn"
    out = turn(case_id, "try again")
    assert out["pause"]["type"] == "otp"  # from the start: log in again


def test_safe_stop_then_stop(case_id, fake_driver):
    otp_pause(case_id)
    fake_driver.fail["fill"] = SafeStop("the portal showed a alert dialog", step="documents")
    turn(case_id, OTP)
    out = turn(case_id, "no")
    assert out["pause"] is None and "stopped" in out["reply"]
    assert fake_driver.submit_count() == 0


def test_drift_stops_before_the_otp(case_id, fake_driver):
    reqs = fake_driver._reqs["pension-001"]
    reqs["application_fields"] = [*reqs["application_fields"], {"name": "caste", "type": "text", "required": True,
                                                                "label": "Caste"}]
    out = answer_form(lambda t: turn(case_id, t), start(case_id))
    assert out["pause"]["type"] == "safe_stop" and "login" not in fake_driver.calls
    drift = [r for r in store.case_data(case_id)["audit"] if r["action"] == "portal_drift"][-1]
    assert "new field caste" in drift["detail"]["differences"]


def test_already_submitted_on_the_portal(case_id, fake_driver):
    fake_driver.submitted[("9876543210", "pension-001")] = "YJS-00000000AB"
    otp_pause(case_id)
    out = turn(case_id, OTP)
    assert out["pause"] is None and "YJS-00000000AB" in out["reply"]
    assert values(case_id)["applications"]["pension-001"]["app_id"] == "YJS-00000000AB"
    assert fake_driver.submit_count() == 0 and "fill" not in fake_driver.calls


def test_portal_not_configured(case_id, monkeypatch, fake_driver):
    from agent import config

    monkeypatch.setattr(config, "MOCK_PORTAL_AGENT_KEY", None)
    upload_documents(client, case_id, "pension-001")
    turn(case_id, READY)
    out = turn(case_id, "senior citizen pension")
    assert "not set up" in out["reply"] and "login" not in fake_driver.calls


# --- privacy -------------------------------------------------------------------------------

def _all_stored_text(case_id: str) -> str:
    """Everything Postgres holds for this case: our tables + the checkpointer's blobs."""
    parts = []
    with store.pool.connection() as conn:
        for table, col in [("checkpoints", "thread_id"), ("checkpoint_blobs", "thread_id"),
                           ("checkpoint_writes", "thread_id"), ("cases", "case_id"), ("case_events", "case_id"),
                           ("audit_log", "case_id"), ("documents", "case_id"), ("screenshots", "case_id")]:
            for row in conn.execute(f"SELECT * FROM {table} WHERE {col} = %s", (case_id,)):  # noqa: S608
                for v in row.values():
                    parts.append(bytes(v).decode("utf-8", "ignore") if isinstance(v, (bytes, memoryview))
                                 else json.dumps(v, default=str, ensure_ascii=False))
    return "\n".join(parts)


def test_sensitive_values_and_otp_never_stored_in_clear(case_id):
    out = start(case_id)
    out = answer_form(lambda t: turn(case_id, t), out, answers={"bank_account_number": "1234 5678 9012"})
    turn(case_id, "111111")  # a wrong code
    turn(case_id, OTP)
    turn(case_id, "yes")
    stored = _all_stored_text(case_id)
    for secret in ("123456789012", "1234 5678 9012", "9876543210", "98765 43210", "SBIN0001234",
                   dob_for(62), "111111", OTP, "yjs_del_"):
        assert secret not in stored, secret
    form = values(case_id)["form"]
    assert all(sealed.is_sealed(v) for v in form.values())
    assert values(case_id)["form_shown"]["bank_account_number"] == "XXXXXX9012"


def test_summary_shows_progress_and_masked_review(case_id):
    confirm(case_id)
    s = client.get(f"/cases/{case_id}/summary").json()
    keys = [st["key"] for st in s["progress"]["steps"]]
    assert keys == ["login", "otp", "applicant", "details", "documents", "review", "submit"]
    assert all(st["status"] == "done" for st in s["progress"]["steps"][:6])
    r = s["review"]
    assert r["screenshots"] and r["declaration"]["text_en"].startswith("I solemnly affirm")
    acct = next(f for f in r["form_fields"] if f["name"] == "bank_account_number")
    assert acct["text"] == "XXXXXX0123" and acct["from_page"] and acct["sensitive"]
    assert "**/**/" in r["readback"]  # the date of birth masked on screen
    shot = client.get(f"/cases/{case_id}/screenshots/{r['screenshots'][0]}")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"


def test_screenshots_need_the_case_token(case_id):
    from fastapi.testclient import TestClient

    from tests.helpers import bearer

    confirm(case_id)
    shot = client.get(f"/cases/{case_id}/summary").json()["review"]["screenshots"][0]
    plain = TestClient(app)
    assert plain.get(f"/cases/{case_id}/screenshots/{shot}").status_code == 401
    assert plain.get(f"/cases/{case_id}/screenshots/{shot}", headers=bearer("someone-else")).status_code == 403


def test_delete_my_data_removes_screenshots_and_closes_the_session(case_id, fake_driver):
    confirm(case_id)
    assert store.screenshots(case_id)
    client.delete(f"/cases/{case_id}/data").raise_for_status()
    assert store.screenshots(case_id) == []
    assert "close:data_deleted" in fake_driver.calls
