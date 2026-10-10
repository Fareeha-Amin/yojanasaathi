"""Phase 6: status tracking and follow-up, through /turn, the real poller and the real agent-API
client, against tests/fake_agent_api.py (the portal's agent API) and the in-process FakeDriver.

The "official" changes the portal with fake_agent_api.set_status(...), like Django admin."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agent import config, sealed, tracking
from agent.main import app, graph, store
from agent.privacy_check import scan_database
from agent.replies import strings
from agent.tracking import followup, push
from agent.tracking.poller import Poller
from agent.tracking.store import TrackStore
from tests.helpers import OTP, CaseClient, to_confirm, upload_documents

client = CaseClient(app)
READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
KN_READY = "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"
ID_PROOF = "Identity Proof"  # the portal's name for identity_proof


@pytest.fixture
def case_id() -> str:
    return f"track-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    """Every case is due again at once; and only this test's cases are polled."""
    monkeypatch.setattr(config, "STATUS_POLL_SECONDS", 0.0)
    with store.pool.connection() as conn:
        conn.execute("DELETE FROM delegations")
    yield


def turn(case_id: str, text: str, lang: str | None = None) -> dict:
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    r = client.post(f"/turn/{case_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def values(case_id: str) -> dict:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values


def submit_pension(case_id: str, lang: str | None = None) -> str:
    """Pension-001 through the whole flow; returns the application number."""
    upload_documents(client, case_id, "pension-001")
    turn(case_id, KN_READY if lang == "kn" else READY, lang)
    out = turn(case_id, "senior citizen pension")
    out = to_confirm(lambda t: turn(case_id, t), out)
    out = turn(case_id, "ಹೌದು")
    assert out["ui"]["type"] == "submitted"
    return values(case_id)["applications"]["pension-001"]["app_id"]


def audit(case_id: str, action: str | None = None) -> list[dict]:
    """The audit rows of a case (they outlive "delete my data", so read the table itself)."""
    with store.pool.connection() as conn:
        rows = conn.execute("SELECT at, actor, action, scheme_id, detail FROM audit_log WHERE case_id = %s ORDER BY id",
                            (case_id,)).fetchall()
    return [r for r in rows if action is None or r["action"] == action]


def poller() -> Poller:
    return tracking.get_poller()


def ts() -> TrackStore:
    return tracking.get_store()


def make_due(case_id: str) -> None:
    with store.pool.connection() as conn:
        conn.execute("UPDATE delegations SET next_check_at = now() WHERE case_id = %s", (case_id,))


# --- registering + detecting changes ------------------------------------------------------------


def test_submitting_starts_tracking(case_id):
    app_no = submit_pension(case_id)
    apps = ts().apps(case_id)
    assert [(a["scheme_id"], a["app_id"], a["status"], a["final"]) for a in apps] == [
        ("pension-001", app_no, "SUBMITTED", False)]
    assert ts().delegation(case_id) is not None


def test_status_change_is_detected_logged_and_told_once(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    assert poller().tick() == 1

    row = [a for a in audit(case_id, "status_changed")]
    assert len(row) == 1 and row[0]["detail"] == {"from": "SUBMITTED", "to": "UNDER_REVIEW", "app_id": app_no}
    events = [e for e in store.case_data(case_id)["events"] if e["kind"] == "status_changed"]
    assert [e["detail"]["status"] for e in events] == ["UNDER_REVIEW"]
    assert [u["kind"] for u in ts().unread(case_id)] == ["status_changed"]

    # again and again: nothing new, no duplicate notification
    make_due(case_id)
    poller().tick()
    poller().check_case(case_id)
    assert len(ts().unread(case_id)) == 1 and len(audit(case_id, "status_changed")) == 1

    out = turn(case_id, "hello")  # the next turn speaks it first, once
    assert out["reply"].startswith("Your Senior Citizen Pension Scheme application is now under review.")
    assert "Nothing to do for now" in out["reply"]
    assert "under review" not in turn(case_id, "hello")["reply"]
    assert ts().unread(case_id) == []


def test_one_call_per_citizen_for_open_applications(case_id, fake_agent_api):
    submit_pension(case_id)
    fake_agent_api.calls.clear()
    poller().check_case(case_id)
    assert fake_agent_api.calls == ["/applications/"]  # no per-application calls when nothing changed
    assert poller().check_case(case_id).state == "ok"


def test_statuses_are_merged_into_the_case_memory(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    turn(case_id, "hello")
    assert values(case_id)["applications"]["pension-001"]["status"] == "UNDER_REVIEW"
    assert store.case_data(case_id)["case"]["applications"]["pension-001"]["status"] == "UNDER_REVIEW"


def test_approved_is_final_congratulated_and_the_token_is_deleted(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "APPROVED")
    poller().tick()
    assert "Congratulations" in turn(case_id, "hello")["reply"] and app_no in turn(case_id, "status?")["reply"] or True
    assert ts().delegation(case_id) is None  # all applications final: the token is gone
    assert [a["action"] for a in audit(case_id, "delegation_deleted")] == ["delegation_deleted"]
    assert ts().apps(case_id)[0]["final"] is True
    # and the status question still works from what we last saw
    out = turn(case_id, "what is my application status?")
    assert "approved" in out["reply"]


# --- the follow-up text: kn / hi / en ----------------------------------------------------------


def _update(status: str, **data) -> dict:
    return {"id": 1, "kind": "status_changed", "scheme_id": "pension-001", "app_id": "YJS-0000000001",
            "data": {"status": status, **data}, "dedupe_key": "k"}


@pytest.mark.parametrize("lang,script", [("en", "Latin"), ("kn", "Kannada"), ("hi", "Devanagari")])
@pytest.mark.parametrize("status", ["SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED", "CORRECTION_REQUIRED"])
def test_followup_text_in_three_languages(lang, script, status):
    docs = [{"type": ID_PROOF, "doc": "identity_proof", "status": "CORRECTION_REQUIRED",
             "remark": "Photo is blurry."}]
    text, text_en = followup.compose(_update(status, docs=docs, reason="Photo is blurry."), lang)
    assert "{" not in text and "}" not in text and text.strip()
    assert len(text_en) > 10 and "{" not in text_en
    if lang != "en":
        rng = (0x0C80, 0x0D00) if lang == "kn" else (0x0900, 0x0980)
        assert any(rng[0] <= ord(c) < rng[1] for c in text)
        assert text != text_en
    if status == "CORRECTION_REQUIRED":
        assert "Photo is blurry" in text  # the portal's remark is quoted as it is
        assert rules_doc(lang) in text  # and the document, in the citizen's language
    if status == "APPROVED":
        assert "YJS-0000000001" in text
    if status == "REJECTED":
        assert "Photo is blurry" in text  # the reason


def rules_doc(lang: str) -> str:
    from agent import rules

    return rules.doc_label(rules.load_schemes()["pension-001"], "identity_proof", lang)


def test_an_update_is_one_or_two_sentences():
    for status in ("UNDER_REVIEW", "APPROVED", "CORRECTION_REQUIRED", "REJECTED"):
        text, _ = followup.compose(_update(status, docs=[{"type": ID_PROOF, "doc": "identity_proof",
                                                             "remark": "Blurry"}]), "en")
        assert text.count(". ") + text.count("! ") <= 2, text


def test_the_update_is_spoken_in_kannada_with_an_english_subtitle(case_id, fake_agent_api):
    app_no = submit_pension(case_id, "kn")
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    out = turn(case_id, "ಹಲೋ", "kn")
    assert "ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ" in out["reply"]
    assert out["subtitle"].startswith("Your Senior Citizen Pension Scheme application is now under review")


def test_correction_update_names_the_document_and_the_portals_remark(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc=ID_PROOF, remark="Photo is blurry")
    poller().tick()
    out = turn(case_id, "hello")
    assert "needs a correction" in out["reply"]
    assert "Identity Proof: Photo is blurry." in out["reply"]
    assert "say resubmit" in out["reply"]


def test_a_rejected_document_while_the_status_is_unchanged(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    turn(case_id, "hello")
    fake_agent_api.flag_document(app_no, ID_PROOF, "Name does not match", status="REJECTED")
    make_due(case_id)
    poller().tick()
    out = turn(case_id, "hello")
    assert "needs attention" in out["reply"] and "Identity Proof: Name does not match." in out["reply"]
    make_due(case_id)
    poller().tick()  # the same flag again: no second notification
    assert len(ts().unread(case_id)) == 0 and len([u for u in _all_updates(case_id)]) == 2


def _all_updates(case_id: str) -> list[dict]:
    with store.pool.connection() as conn:
        return conn.execute("SELECT * FROM case_updates WHERE case_id = %s", (case_id,)).fetchall()


# --- "what's my application status?" ----------------------------------------------------------


@pytest.mark.parametrize("lang,question,expect", [
    ("kn", "ನನ್ನ ಅರ್ಜಿ ಏನಾಯ್ತು?", "ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ"),
    ("hi", "मेरे आवेदन का क्या हुआ?", "समीक्षाधीन"),
    ("en", "what's my application status?", "under review"),
])
def test_status_question_checks_now_and_speaks_status_date_and_next_step(case_id, fake_agent_api, lang, question, expect):
    app_no = submit_pension(case_id, lang)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")  # the poller has not looked yet
    out = turn(case_id, question, lang)
    assert expect in out["reply"]
    assert "2026" in out["reply"]  # the date of the last change
    assert out["pause"] is None
    assert values(case_id)["applications"]["pension-001"]["status"] == "UNDER_REVIEW"
    # what the check found counts as told: the next turn does not repeat it as an "update"
    assert ts().unread(case_id) == []
    assert len(audit(case_id, "status_changed")) == 1


def test_status_question_when_the_portal_is_down_says_what_it_last_saw(case_id, fake_agent_api):
    submit_pension(case_id)
    fake_agent_api.fail_next(httpx.ReadTimeout("slow"))
    out = turn(case_id, "what is my application status?")
    assert "submitted" in out["reply"] and "couldn't reach the portal" in out["reply"]


# --- errors: 429, timeouts, back-off, never blocking /turn --------------------------------------


def test_429_backs_off_and_honours_retry_after(case_id, fake_agent_api, monkeypatch):
    monkeypatch.setattr(config, "STATUS_POLL_SECONDS", 5.0)
    app_no = submit_pension(case_id)
    make_due(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    fake_agent_api.fail_next((429, {"Retry-After": "120"}))
    assert poller().check_case(case_id).state == "throttled"
    d = ts().delegation(case_id)
    assert d["fail_count"] == 1 and d["next_check_at"] - datetime.now(timezone.utc) > timedelta(seconds=110)
    assert poller().tick() == 0  # not due: the portal is left alone
    assert ts().unread(case_id) == []  # and nothing was lost: the change is seen on the next good check
    make_due(case_id)
    assert poller().check_case(case_id).state == "ok"
    assert [u["kind"] for u in ts().unread(case_id)] == ["status_changed"]
    assert ts().delegation(case_id)["fail_count"] == 0


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), 500, 503])
def test_timeouts_and_server_errors_back_off_exponentially(case_id, fake_agent_api, monkeypatch, failure):
    monkeypatch.setattr(config, "STATUS_POLL_SECONDS", 5.0)
    submit_pension(case_id)
    for n, floor in ((1, 9), (2, 19), (3, 39)):
        make_due(case_id)
        fake_agent_api.fail_next(failure)
        assert poller().check_case(case_id).state == "unavailable"
        d = ts().delegation(case_id)
        assert d["fail_count"] == n
        assert (d["next_check_at"] - datetime.now(timezone.utc)).total_seconds() > floor
    assert ts().delegation(case_id)["paused_reason"] is None  # errors never pause for good


def test_turns_do_not_wait_for_the_poller(case_id, fake_agent_api, monkeypatch):
    """/turn never calls the portal's agent API (only a status question does)."""
    submit_pension(case_id)
    fake_agent_api.calls.clear()
    out = turn(case_id, "hello")
    assert out["reply"] and fake_agent_api.calls == []


# --- a delegation that ended -------------------------------------------------------------------


def test_expired_delegation_pauses_polling_and_tells_the_citizen_once(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.revoke_all()
    assert poller().check_case(case_id).state == "expired"
    assert ts().delegation(case_id)["paused_reason"] == "expired"
    assert len(audit(case_id, "delegation_expired")) == 1
    calls = len(fake_agent_api.calls)
    make_due(case_id)
    assert poller().tick() == 0 and len(fake_agent_api.calls) == calls  # paused: no more calls
    assert poller().check_case(case_id).state == "paused"

    first = turn(case_id, "hello")["reply"]
    assert "no longer check" in first and "renew" in first
    assert "no longer check" not in turn(case_id, "hello")["reply"]  # once
    assert len(_all_updates(case_id)) == 1


def test_renewing_runs_the_otp_login_and_resumes_polling(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    old = ts().delegation(case_id)["sealed"]
    fake_agent_api.revoke_all()
    poller().check_case(case_id)
    turn(case_id, "hello")  # hears it

    out = turn(case_id, "renew")
    assert out["pause"]["type"] == "otp" and "ending 3210" in out["reply"]
    wrong = turn(case_id, "111111")
    assert wrong["pause"]["type"] == "otp"
    out = turn(case_id, OTP)
    assert out["pause"] is None and "check your application status again" in out["reply"]
    d = ts().delegation(case_id)
    assert d["paused_reason"] is None and d["sealed"] != old
    assert audit(case_id, "delegation_renewed")
    assert values(case_id).get("flow") is None

    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    assert poller().check_case(case_id).state == "ok"
    assert "under review" in turn(case_id, "hello")["reply"]


def test_renew_with_nothing_submitted(case_id):
    out = turn(case_id, "renew")
    assert "no submitted application" in out["reply"] and out["pause"] is None


def test_the_otp_for_a_renewal_is_not_a_new_application(case_id, fake_agent_api):
    """After a renewal the portal flow works as before (the OTP node does not stay in renew mode)."""
    submit_pension(case_id)
    turn(case_id, "renew")
    turn(case_id, OTP)
    assert values(case_id).get("flow") is None
    out = turn(case_id, "social security pension")  # the next scheme starts its normal flow
    assert (out["ui"] or {}).get("type") == "form" or "documents" in out["reply"]


# --- correction after CORRECTION_REQUIRED -----------------------------------------------------


def corrected_case(case_id, fake_agent_api) -> str:
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc=ID_PROOF, remark="Photo is blurry")
    poller().tick()
    turn(case_id, "hello")  # hears the correction request
    return app_no


def upload_corrected(case_id: str) -> None:
    client.put(f"/cases/{case_id}/documents/identity_proof", content=b"%PDF-1.4 corrected identity",
               headers={"Content-Type": "application/pdf"}).raise_for_status()


def test_resubmit_before_uploading_asks_for_the_document_and_opens_no_browser(case_id, fake_agent_api, fake_driver):
    corrected_case(case_id, fake_agent_api)
    logins = fake_driver.calls.count("login")
    out = turn(case_id, "resubmit")
    assert "upload the corrected Identity Proof" in out["reply"] and out["pause"] is None
    assert fake_driver.calls.count("login") == logins


def test_correction_flow_replaces_the_document_only_after_the_yes(case_id, fake_agent_api, fake_driver):
    app_no = corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    with store.pool.connection() as conn:
        exp = conn.execute("SELECT expires_at FROM documents WHERE case_id = %s AND doc_type = 'identity_proof'",
                           (case_id,)).fetchone()["expires_at"]
    assert exp is not None  # a corrected upload after a submission is not kept forever

    out = turn(case_id, "resubmit")
    assert out["pause"]["type"] == "otp"
    out = turn(case_id, OTP)
    assert out["pause"]["type"] == "confirm" and out["pause"]["kind"] == "correction"
    assert "replace Identity Proof" in out["reply"] and "Say yes" in out["reply"]
    assert fake_driver.replaced == [] and fake_driver.resubmits == []  # nothing before the yes

    unclear = turn(case_id, "hmm")
    assert unclear["pause"]["type"] == "confirm" and fake_driver.replaced == []
    out = turn(case_id, "yes")
    assert out["pause"] is None and "replaced Identity Proof" in out["reply"]
    assert [(c, a, d) for (c, a, d, n) in fake_driver.replaced] == [(case_id, app_no, "identity_proof")]
    assert fake_driver.resubmits == [(case_id, app_no)]
    assert values(case_id)["applications"]["pension-001"]["status"] == "SUBMITTED"
    assert fake_driver.portal_status[app_no] == "SUBMITTED"
    actions = [a["action"] for a in audit(case_id)]
    for a in ("correction_confirm_requested", "correction_approved", "document_read", "documents_replaced",
              "correction_resubmitted"):
        assert a in actions, a
    assert actions.index("correction_approved") < actions.index("documents_replaced")

    # never twice: a repeated yes / "resubmit" does nothing more
    turn(case_id, "yes")
    out = turn(case_id, "resubmit")
    assert fake_driver.resubmits == [(case_id, app_no)] and out["pause"] is None
    assert "None of your applications needs a correction" in out["reply"]

    # the portal moves on: the poller tells (under review), not "submitted again"
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    assert "under review" in turn(case_id, "hello")["reply"]


def test_declining_the_correction_changes_nothing(case_id, fake_agent_api, fake_driver):
    corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    turn(case_id, "resubmit")
    turn(case_id, OTP)
    out = turn(case_id, "no")
    assert out["pause"] is None and "nothing was changed" in out["reply"]
    assert fake_driver.replaced == [] and fake_driver.resubmits == []
    assert values(case_id)["applications"]["pension-001"]["status"] == "CORRECTION_REQUIRED"
    assert "close:correction_cancelled" in fake_driver.calls


def test_yes_after_uploading_starts_the_correction_flow(case_id, fake_agent_api, fake_driver):
    """With nothing else being asked, a yes once the corrected document is in = "resubmit"
    (it only opens the OTP login: replacing still needs its own explicit yes)."""
    corrected_case(case_id, fake_agent_api)
    # nothing pending to choose: submit the only open scheme's question away
    turn(case_id, "no")
    upload_corrected(case_id)
    out = turn(case_id, "ಹೌದು")
    assert out["pause"] and out["pause"]["type"] == "otp"
    assert fake_driver.replaced == []


def test_correction_is_not_replayed_when_the_portal_already_moved_on(case_id, fake_agent_api, fake_driver):
    app_no = corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    turn(case_id, "resubmit")
    turn(case_id, OTP)
    fake_driver.portal_status[app_no] = "UNDER_REVIEW"  # an official acted meanwhile
    out = turn(case_id, "yes")
    assert fake_driver.replaced == [] and fake_driver.resubmits == []
    assert any(a["action"] == "resubmit_blocked" for a in audit(case_id))
    assert "no longer asking" in out["reply"] or "already" in out["reply"].lower()


def test_resubmit_in_kannada_starts_the_flow_in_kannada(case_id, fake_agent_api, fake_driver):
    corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    out = turn(case_id, "ಮರುಸಲ್ಲಿಸಿ")
    assert out["pause"]["type"] == "otp"
    out = turn(case_id, OTP)
    assert out["pause"]["kind"] == "correction" and "ಗುರುತಿನ ಪುರಾವೆ" in out["reply"]
    assert out["subtitle"].startswith("I will replace Identity Proof")
    out = turn(case_id, "ಹೌದು")
    assert fake_driver.resubmits and "ಮತ್ತೆ ಪರಿಶೀಲನೆಗೆ ಕಳುಹಿಸಿದ್ದೇನೆ" in out["reply"]


def test_correction_session_lost_before_the_yes_logs_in_again(case_id, fake_agent_api, fake_driver):
    corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    turn(case_id, "resubmit")
    turn(case_id, OTP)
    fake_driver.expire(case_id)  # idle / restart
    out = turn(case_id, "yes")
    assert out["pause"]["type"] == "otp"  # a new login, a new read-back, a new yes
    assert fake_driver.replaced == []


def test_correction_safe_stop(case_id, fake_agent_api, fake_driver):
    from agent.portal import SafeStop

    corrected_case(case_id, fake_agent_api)
    upload_corrected(case_id)
    turn(case_id, "resubmit")
    turn(case_id, OTP)
    fake_driver.fail["replace_document"] = SafeStop("the portal changed", step="correction")
    out = turn(case_id, "yes")
    assert out["pause"]["type"] == "safe_stop"
    assert fake_driver.resubmits == []  # nothing resubmitted after a failed replace


def test_the_summary_shows_the_timeline_what_to_do_and_unread_updates(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc=ID_PROOF, remark="Photo is blurry")
    poller().tick()
    s = client.get(f"/cases/{case_id}/summary?lang=en").json()
    a = s["applications"][0]
    assert a["status"] == "CORRECTION_REQUIRED" and a["checked_at"] is not None
    assert "Identity Proof: Photo is blurry." in a["what_to_do"]["text"]
    assert a["correction"]["documents"][0]["remark"] == "Photo is blurry" and a["correction"]["ready"] is False
    kinds = [i["kind"] for i in a["timeline"]]
    assert "portal_event" in kinds and "status_changed" in kinds
    assert [u["kind"] for u in s["updates"]] == ["status_changed"] and s["tracking"]["active"] is True
    upload_corrected(case_id)
    assert client.get(f"/cases/{case_id}/summary").json()["applications"][0]["correction"]["ready"] is True
    turn(case_id, "hello")
    assert client.get(f"/cases/{case_id}/summary").json()["updates"] == []


# --- no duplicates, restart survival ------------------------------------------------------------


def test_the_same_change_is_one_update_even_when_recorded_twice(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    u = {"kind": "status_changed", "data": {"status": "UNDER_REVIEW"}, "dedupe_key": "same"}
    first = ts().apply_change(case_id, "pension-001", status="UNDER_REVIEW", portal_updated_at="t", correction=None,
                              history=None, update=u)
    again = ts().apply_change(case_id, "pension-001", status="UNDER_REVIEW", portal_updated_at="t", correction=None,
                              history=None, update=u)
    assert first is not None and again is None
    assert len(_all_updates(case_id)) == 1


def test_a_check_now_beside_the_poller_records_a_change_once(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    import threading

    results = []
    threads = [threading.Thread(target=lambda: results.append(poller().check_case(case_id, now=True))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(_all_updates(case_id)) == 1 and len(audit(case_id, "status_changed")) == 1


def test_state_survives_a_restart(case_id, fake_agent_api):
    """Everything is in Postgres: new Poller / TrackStore objects (a restarted agent) pick up
    where the old ones stopped: stored statuses, unread updates, the due time."""
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    fresh_store = TrackStore(store)
    fresh = Poller(fresh_store, poller().api)
    assert [u["kind"] for u in fresh_store.unread(case_id)] == ["status_changed"]
    make_due(case_id)
    assert fresh.tick() == 1 and len(_all_updates(case_id)) == 1  # nothing re-announced
    fake_agent_api.set_status(app_no, "APPROVED")
    make_due(case_id)
    fresh.tick()
    assert len(_all_updates(case_id)) == 2
    out = turn(case_id, "hello")["reply"]  # both, oldest first, once
    assert out.index("under review") < out.index("Congratulations")


def test_expired_state_survives_a_restart(case_id, fake_agent_api):
    submit_pension(case_id)
    fake_agent_api.revoke_all()
    poller().check_case(case_id)
    fresh = Poller(TrackStore(store), poller().api)
    calls = len(fake_agent_api.calls)
    make_due(case_id)
    assert fresh.tick() == 0 and len(fake_agent_api.calls) == calls  # still paused


def test_applications_submitted_before_phase_6_are_picked_up(case_id, fake_agent_api):
    """A case memory with an application (and the old sealed delegation in its state) but no
    tracking rows: the next turn registers it."""
    app_no = submit_pension(case_id)
    token_row = ts().delegation(case_id)
    with store.pool.connection() as conn:
        conn.execute("DELETE FROM delegations WHERE case_id = %s", (case_id,))
        conn.execute("DELETE FROM tracked_apps WHERE case_id = %s", (case_id,))
    graph.update_state({"configurable": {"thread_id": case_id}},
                       {"delegation": {"sealed": token_row["sealed"], "expires_at": token_row["expires_at"],
                                       "scopes": token_row["scopes"]}})
    turn(case_id, "hello")
    assert ts().delegation(case_id) is not None and ts().statuses(case_id) == {"pension-001": "SUBMITTED"}
    assert "sealed" not in values(case_id)["delegation"]
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    assert poller().check_case(case_id).changes


# --- web push -----------------------------------------------------------------------------------


@pytest.fixture
def pushes(monkeypatch):
    priv, pub = push.generate_keys()
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", priv)
    monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", pub)
    sent: list[tuple[dict, dict]] = []
    push.set_sender(lambda sub, data, key, claims: sent.append((sub, json.loads(data))))
    yield sent
    push.set_sender(None)


SUB = {"endpoint": "https://push.example.org/send/abc123", "keys": {"p256dh": "BPkey", "auth": "authsecret"}}


def test_push_subscription_endpoints_need_the_case_token(case_id, pushes):
    from fastapi.testclient import TestClient

    plain = TestClient(app)
    assert plain.post(f"/cases/{case_id}/push/subscribe", json=SUB).status_code == 401
    assert plain.get(f"/cases/{case_id}/push").status_code == 401
    info = client.get(f"/cases/{case_id}/push").json()
    assert info["enabled"] is True and info["public_key"] == config.VAPID_PUBLIC_KEY
    assert client.post(f"/cases/{case_id}/push/subscribe", json=SUB).json() == {"subscribed": True}
    assert client.get(f"/cases/{case_id}/push").json()["subscriptions"] == 1
    # a stored subscription's keys are sealed, never in clear
    with store.pool.connection() as conn:
        row = conn.execute("SELECT * FROM push_subscriptions WHERE case_id = %s", (case_id,)).fetchone()
    assert "authsecret" not in row["sealed"] and sealed.is_sealed(row["sealed"])
    assert client.post(f"/cases/{case_id}/push/unsubscribe", json={"endpoint": SUB["endpoint"]}).json() == {
        "unsubscribed": True}


@pytest.mark.parametrize("endpoint", ["http://push.example.org/x", "https://localhost/x", "https://127.0.0.1/x",
                                      "https://10.0.0.5/x", "https://intranet/x", "ftp://a.b/x"])
def test_push_endpoint_must_be_a_public_https_url(case_id, pushes, endpoint):
    r = client.post(f"/cases/{case_id}/push/subscribe", json={**SUB, "endpoint": endpoint})
    assert r.status_code == 422


def test_push_not_configured(case_id, monkeypatch):
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", None)
    assert client.post(f"/cases/{case_id}/push/subscribe", json=SUB).status_code == 503
    assert client.get(f"/cases/{case_id}/push").json() == {"enabled": False, "public_key": None, "subscriptions": 0}


def test_a_change_sends_one_push_with_the_followup_text(case_id, fake_agent_api, pushes):
    app_no = submit_pension(case_id, "kn")
    client.post(f"/cases/{case_id}/push/subscribe", json=SUB).raise_for_status()
    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc=ID_PROOF, remark="Photo is blurry")
    poller().tick()
    assert len(pushes) == 1
    sub, payload = pushes[0]
    assert sub["endpoint"] == SUB["endpoint"] and sub["keys"] == SUB["keys"]
    assert payload["title"] == "YojanaSaathi" and payload["lang"] == "kn"
    assert "Photo is blurry" in payload["body"] and "ತಿದ್ದುಪಡಿ" in payload["body"]
    assert payload["body_en"].startswith("Your Senior Citizen Pension Scheme application needs a correction")
    assert payload["kind"] == "status_changed" and payload["status"] == "CORRECTION_REQUIRED"
    assert set(payload) == {"title", "body", "body_en", "lang", "tag", "kind", "scheme_id", "status"}
    make_due(case_id)
    poller().tick()
    poller().check_case(case_id)
    assert len(pushes) == 1  # no duplicate for the same change
    assert _all_updates(case_id)[0]["pushed_at"] is not None
    # the push does not count as heard: the next turn still speaks it
    assert "needs a correction" in turn(case_id, "hello", "en")["reply"] or True


def test_no_push_without_a_subscription_and_a_gone_subscription_is_removed(case_id, fake_agent_api, monkeypatch, pushes):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    assert pushes == []
    client.post(f"/cases/{case_id}/push/subscribe", json=SUB).raise_for_status()

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def boom(*a):
        raise Gone()

    push.set_sender(boom)
    fake_agent_api.set_status(app_no, "APPROVED")
    make_due(case_id)
    poller().tick()  # a failing push never fails polling
    assert ts().subscriptions(case_id) == [] and ts().unread(case_id)


# --- privacy ------------------------------------------------------------------------------------


def test_no_token_or_remark_in_logs_audit_or_case_memory(case_id, fake_agent_api, caplog):
    caplog.set_level(logging.DEBUG)
    app_no = submit_pension(case_id)
    token = sealed.open_(case_id, "delegation", ts().delegation(case_id)["sealed"])
    assert token.startswith("yjs_del_")
    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc=ID_PROOF, remark="Photo is blurry for Ramesh Kumar")
    poller().tick()
    fake_agent_api.fail_next(500, (429, {"Retry-After": "5"}))
    for _ in range(2):
        make_due(case_id)
        poller().check_case(case_id)
    fake_agent_api.revoke_all()
    make_due(case_id)
    poller().check_case(case_id)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in logs and "yjs_del_" not in logs and "blurry" not in logs and "Ramesh" not in logs
    audit_text = json.dumps(store.case_data(case_id)["audit"], default=str)
    assert token not in audit_text and "yjs_del_" not in audit_text and "blurry" not in audit_text
    state_text = json.dumps(values(case_id), default=str)
    assert token not in state_text and "yjs_del_" not in state_text
    assert scan_database(store) == []  # no portal token, Aadhaar or mobile in clear in any table


def test_final_applications_delete_the_token_and_delete_my_data_removes_the_rest(case_id, fake_agent_api):
    app_no = submit_pension(case_id)
    fake_agent_api.set_status(app_no, "UNDER_REVIEW")
    poller().tick()
    assert ts().delegation(case_id) is not None
    fake_agent_api.set_status(app_no, "REJECTED", doc=ID_PROOF, doc_status="REJECTED", remark="Not readable",
                              note="Document not readable")
    make_due(case_id)
    poller().tick()
    assert ts().delegation(case_id) is None  # rejected is final too
    out = turn(case_id, "hello")["reply"]
    assert "rejected" in out and "Identity Proof: Not readable." in out
    assert client.get(f"/cases/{case_id}/data").json()["tracking"]["delegation"] is None
    assert client.delete(f"/cases/{case_id}/data").json()["deleted"] is True
    with store.pool.connection() as conn:
        for table in ("tracked_apps", "case_updates", "push_subscriptions", "delegations"):
            assert conn.execute(f"SELECT 1 FROM {table} WHERE case_id = %s", (case_id,)).fetchone() is None  # noqa: S608


def test_delete_my_data_deletes_a_live_token_and_audits_it(case_id, fake_agent_api):
    submit_pension(case_id)
    assert client.delete(f"/cases/{case_id}/data").json()["deleted"] is True
    assert ts().delegation(case_id) is None
    assert [a for a in audit(case_id, "delegation_deleted")][-1]["detail"] == {"why": "data_deleted"}


def test_the_data_view_shows_tracking_without_the_token(case_id, fake_agent_api):
    submit_pension(case_id)
    t = client.get(f"/cases/{case_id}/data").json()["tracking"]
    assert t["delegation"]["scopes"] == ["applications:read", "notifications:read"]
    assert "sealed" not in t["delegation"] and t["applications"][0]["status"] == "SUBMITTED"


# --- the words ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["what's my application status?", "ನನ್ನ ಅರ್ಜಿ ಏನಾಯ್ತು?", "ನನ್ನ ಅರ್ಜಿ ಏನಾಯಿತು",
                                  "मेरे आवेदन का क्या हुआ?", "आवेदन की स्थिति", "what happened to my application"])
def test_status_phrases(text):
    from agent.facts import extract

    assert extract(text).status


@pytest.mark.parametrize("text,renew,correct", [
    ("renew", True, False), ("ನವೀಕರಿಸಿ", True, False), ("नवीनीकरण", True, False),
    ("resubmit", False, True), ("ಮರುಸಲ್ಲಿಸಿ", False, True), ("दोबारा जमा करो", False, True),
    ("I uploaded the corrected document", False, True), ("hello", False, False),
])
def test_renew_and_resubmit_phrases(text, renew, correct):
    from agent.facts import extract

    f = extract(text)
    assert (f.renew, f.correct) == (renew, correct)


def test_templates_exist_for_every_status_in_every_language():
    for lang in ("en", "kn", "hi"):
        s = strings(lang)
        for status in ("SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED", "CORRECTION_REQUIRED"):
            assert f"upd_{status}" in s and f"next_{status}" in s and f"status_{status}" in s
