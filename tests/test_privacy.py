"""Aadhaar as last 4 digits only (masked before the graph, the LLM, the checkpoint and the
audit log), consent before saving a profile, and the citizen's view / delete endpoints."""

import uuid

import pytest

from agent import privacy_check
from agent.main import app, graph, store
from agent.privacy import mask_aadhaar, scrub
from tests.helpers import CaseClient

client = CaseClient(app)  # sends the case's session token, like the web app

READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
AADHAAR_MSG = "my aadhaar is 2345 6789 0123 and I'm 62"


@pytest.fixture
def case_id() -> str:
    return f"priv-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str) -> dict:
    r = client.post(f"/turn/{case_id}", json={"text": text})
    assert r.status_code == 200
    return r.json()


def state(case_id: str) -> dict:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values


# --- masking ------------------------------------------------------------------------------


@pytest.mark.parametrize("text, masked", [
    ("2345 6789 0123", "XXXX XXXX 0123"),
    ("2345-6789-0123", "XXXX XXXX 0123"),
    ("234567890123", "XXXX XXXX 0123"),
    ("ಆಧಾರ್ ೨೩೪೫ ೬೭೮೯ ೦೧೨೩", "ಆಧಾರ್ XXXX XXXX ೦೧೨೩"),  # Kannada digits
    ("आधार २३४५ ६७८९ ०१२३", "आधार XXXX XXXX ०१२३"),  # Devanagari digits
])
def test_aadhaar_masked(text, masked):
    assert mask_aadhaar(text)[0] == masked


@pytest.mark.parametrize("text", ["I'm 62", "income 1,20,000", "1 lakh 20 thousand",
                                  "call 98450 12345", "9845012345", "YJS-07531B2688", "+919845012345",
                                  "2345678901234"])  # 13 digits: not an Aadhaar
def test_other_numbers_untouched(text):
    assert mask_aadhaar(text) == (text, [])


def test_scrub_masks_nested_values_and_drops_bytes():
    out = scrub({"a": ["2345 6789 0123"], "b": b"\xff\xd8", "c": 234567890123, "d": 62})
    assert out == {"a": ["XXXX XXXX 0123"], "b": "<bytes withheld>", "c": "XXXX XXXX 0123", "d": 62}


def test_audit_detail_is_scrubbed(case_id):
    store.ensure_case(case_id)
    store.log_event("agent", "test_scrub", case_id, None, {"note": "aadhaar 2345 6789 0123"})
    data = client.get(f"/cases/{case_id}/data").json()
    row = next(a for a in data["audit"] if a["action"] == "test_scrub")
    assert row["detail"] == {"note": "aadhaar XXXX XXXX 0123"}


def test_aadhaar_in_a_turn_never_reaches_graph_llm_or_db(case_id, fake_llm):
    turn(case_id, AADHAAR_MSG)
    hidden = "my aadhaar is [Aadhaar number hidden] and I'm 62"
    assert fake_llm.calls == [hidden]
    assert state(case_id)["msg"] == hidden
    assert state(case_id)["profile"] == {"age": 62}  # the rest still works; no digits leak into facts
    data = client.get(f"/cases/{case_id}/data").json()
    masked = next(a for a in data["audit"] if a["action"] == "aadhaar_masked")
    assert masked["detail"] == {"aadhaar_last4": ["0123"]}
    assert "2345 6789" not in str(data) and "234567890123" not in str(data)


def test_aadhaar_said_when_asked_for_income_is_not_read_as_income(case_id):
    turn(case_id, "ನನಗೆ 62 ವರ್ಷ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?")  # asks for income next
    turn(case_id, "2345 6789 0123")
    assert "annual_income" not in state(case_id)["profile"]


def test_privacy_scan_of_the_whole_test_database_is_clean(case_id):
    # A case with an Aadhaar number said aloud, documents, the whole portal flow (form with
    # mobile, account, IFSC, date of birth; a wrong OTP and the right one; screenshots; the
    # delegation token), then scan every table (incl. the checkpointer's blobs) and the vault.
    from tests import helpers

    client.put(f"/cases/{case_id}/consent", json={"profile": True, "documents": True})
    helpers.upload_documents(client, case_id, "pension-001")
    client.put(f"/cases/{case_id}/documents/identity_proof", content=b"\x89PNG card 2345 6789 0123",
               headers={"Content-Type": "image/png"}, params={"aadhaar_last4": "0123"})
    turn(case_id, AADHAAR_MSG)
    turn(case_id, "income 1 lakh 20 thousand")
    out = helpers.answer_form(lambda m: turn(case_id, m), turn(case_id, "senior citizen pension"))
    assert out["pause"]["type"] == "otp"
    turn(case_id, "654321")
    turn(case_id, helpers.OTP)
    assert "YJS-" in turn(case_id, "yes")["reply"]
    assert privacy_check.scan_database(store) == [], "findings name table.column only"
    assert privacy_check.scan_vault(privacy_check.config.VAULT_DIR) == []


@pytest.mark.parametrize("noise", ["00000000000000000000000000000003.000.123456789012",
                                   "1f0a5c3e-1234-6123-8123-123456789012"])
def test_privacy_scan_ignores_checkpoint_versions_and_uuids(noise):
    assert not privacy_check.has_full_aadhaar(noise)
    assert privacy_check.has_full_aadhaar(f"{noise} aadhaar 2345 6789 0123")


@pytest.mark.parametrize("text, found", [
    ("call 98765 43210", ["mobile number"]), ("+91 9876543210", ["mobile number"]),
    ("ifsc SBIN0001234", ["IFSC code"]), ("token yjs_del_abcdef123456", ["portal token"]),
    ("XXXXXX3210 and SBIN0******", []),  # masked
    ("YJS-07531B2688", []),  # an application number is not personal
    ("sha 9a7f3c2b1d0e9876543210ffab", []),  # a hex digest
    ("version 0.9876543210123 and 1.9876543210", []),  # digits of a random decimal
])
def test_portal_leak_patterns(text, found):
    assert privacy_check.portal_leaks(text) == found


def test_sealed_values_are_not_leaks():
    from agent import sealed

    sealed.set_key(bytes(range(32)))
    for _ in range(50):
        assert privacy_check.portal_leaks(sealed.seal("c", "mobile", "9876543210")) == []


def test_privacy_scan_finds_planted_portal_values_in_case_memory(case_id):
    turn(case_id, READY)
    graph.update_state({"configurable": {"thread_id": case_id}},
                       {"form_shown": {"mobile": "9876543210", "bank_ifsc": "SBIN0001234"}})
    try:
        found = privacy_check.scan_database(store)
        assert any("mobile number" in f for f in found) and any("IFSC code" in f for f in found)
    finally:
        store.checkpointer.delete_thread(case_id)


def test_privacy_scan_finds_a_planted_number(case_id):
    store.ensure_case(case_id)
    with store.pool.connection() as conn:
        conn.execute("UPDATE cases SET status = 'x 2345 6789 0123' WHERE case_id = %s", (case_id,))
    try:
        assert "cases.status: full Aadhaar-like number" in privacy_check.scan_database(store)
    finally:
        with store.pool.connection() as conn:
            conn.execute("UPDATE cases SET status = NULL WHERE case_id = %s", (case_id,))


# --- consent before saving the profile ------------------------------------------------------


def saved_profile(case_id: str) -> dict:
    return client.get(f"/cases/{case_id}/data").json()["saved_profile"]


def test_profile_not_saved_without_consent(case_id):
    turn(case_id, READY)
    assert saved_profile(case_id) == {}
    assert client.get(f"/cases/{case_id}/consent").json() == {"profile": False, "documents": False,
                                                              "at": None}


def test_consent_saves_profile_now_and_on_later_turns(case_id):
    turn(case_id, "I'm 62")
    out = client.put(f"/cases/{case_id}/consent", json={"profile": True}).json()
    assert out["profile"] is True and out["documents"] is False
    assert saved_profile(case_id) == {"age": 62}
    turn(case_id, "income 1 lakh 20 thousand")
    assert saved_profile(case_id) == {"age": 62, "annual_income": 120000}


def test_withdrawing_profile_consent_empties_the_saved_profile(case_id):
    turn(case_id, READY)
    client.put(f"/cases/{case_id}/consent", json={"profile": True})
    client.put(f"/cases/{case_id}/consent", json={"profile": False})
    assert saved_profile(case_id) == {}
    assert state(case_id)["profile"]["age"] == 62  # the open case still works


# --- view and delete my data ------------------------------------------------------------


def test_view_my_data(case_id):
    turn(case_id, READY)
    data = client.get(f"/cases/{case_id}/data").json()
    assert set(data) == {"case", "consent", "saved_profile", "case_memory", "documents", "screenshots",
                         "events", "audit"}
    assert data["case_memory"]["profile"] == {"age": 62, "annual_income": 120000}
    assert data["case"]["case_id"] == case_id
    assert "data_viewed" in [a["action"] for a in client.get(f"/cases/{case_id}/data").json()["audit"]]


def test_unknown_case_has_no_data():
    assert client.get("/cases/nobody-here/data").status_code == 404


def test_delete_is_idempotent(case_id):
    r = client.delete("/cases/nobody-here/data")
    assert r.status_code == 200 and r.json() == {"deleted": False}
    turn(case_id, READY)
    assert client.delete(f"/cases/{case_id}/data").json()["deleted"] is True
    r = client.delete(f"/cases/{case_id}/data")  # the demo reset, run twice
    assert r.status_code == 200 and r.json() == {"deleted": False}


def test_delete_my_data(case_id):
    turn(case_id, READY)
    turn(case_id, "senior citizen pension")  # paused at confirm
    client.put(f"/cases/{case_id}/consent", json={"profile": True, "documents": True})
    client.put(f"/cases/{case_id}/documents/age_proof", content=b"%PDF-1.7 birth certificate",
               headers={"Content-Type": "application/pdf"})
    (doc,) = store.documents(case_id)

    out = client.delete(f"/cases/{case_id}/data").json()["counts"]
    assert out["documents"] == 1 and out["cases"] == 1 and out["profiles"] == 1 and out["citizens"] == 1
    assert client.get(f"/cases/{case_id}/data").status_code == 404
    assert state(case_id) == {}  # case memory (checkpoints) gone too
    with store.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s",
                            (case_id,)).fetchone()["n"] == 0
        audit = [r["action"] for r in conn.execute(
            "SELECT action FROM audit_log WHERE case_id = %s ORDER BY id", (case_id,))]
    assert audit[-1] == "data_deleted"  # the trail stays (append-only, no personal values)
    from agent.main import vault
    assert not (vault.root / f"{doc['storage_key']}.ysv").exists()

    # the same case ID starts fresh
    assert turn(case_id, "hello")["pause"] is None
