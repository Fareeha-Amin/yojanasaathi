"""Encrypted document vault: AES-256-GCM per file, key wrapped with MASTER_KEY, metadata
only in Postgres, consent first, auto-delete after submission, never to the LLM."""

import base64
import hashlib
import uuid

import pytest

from agent import config
from agent.db import StartupError
from agent.main import app, store, vault
from agent.vault import VaultError, load_master_key, seal, unseal
from tests.helpers import CaseClient

client = CaseClient(app)  # sends the case's session token, like the web app

KEY = bytes(range(32))
JPEG = b"\xff\xd8\xff\xe0" + b"JFIF citizen photo of an identity card 1234 5678 9012" * 20
READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"


@pytest.fixture
def case_id() -> str:
    return f"vault-{uuid.uuid4().hex[:8]}"


def consent(case_id: str, **kw) -> dict:
    r = client.put(f"/cases/{case_id}/consent", json=kw)
    assert r.status_code == 200
    return r.json()


def upload(case_id: str, doc: str = "identity_proof", data: bytes = JPEG, ctype: str = "image/jpeg",
           **params):
    return client.put(f"/cases/{case_id}/documents/{doc}", content=data,
                      headers={"Content-Type": ctype}, params=params)


# --- crypto -----------------------------------------------------------------------------


def test_seal_roundtrip_and_no_plaintext():
    blob = seal(KEY, "abc", JPEG)
    assert blob.startswith(b"YSV1") and b"JFIF" not in blob and b"1234 5678 9012" not in blob
    assert unseal(KEY, "abc", blob) == JPEG


def test_each_file_has_its_own_key_and_nonce():
    a, b = seal(KEY, "k", JPEG), seal(KEY, "k", JPEG)
    assert a != b and a[4:16] != b[4:16]


def test_wrong_master_key_fails():
    with pytest.raises(VaultError, match="decryption failed"):
        unseal(bytes(32), "abc", seal(KEY, "abc", JPEG))


def test_file_moved_to_another_key_fails():
    with pytest.raises(VaultError):
        unseal(KEY, "other", seal(KEY, "abc", JPEG))


def test_tampered_file_fails():
    blob = bytearray(seal(KEY, "abc", JPEG))
    blob[-1] ^= 1
    with pytest.raises(VaultError):
        unseal(KEY, "abc", bytes(blob))


@pytest.mark.parametrize("bad, msg", [(None, "not set"), ("not base64!", "not valid base64"),
                                      (base64.b64encode(b"short").decode(), "must be 32 bytes")])
def test_master_key_checked_at_startup(bad, msg):
    with pytest.raises(StartupError, match=msg):
        load_master_key(bad)


# --- API: consent, upload, metadata only ----------------------------------------------


def test_upload_needs_document_consent(case_id):
    r = upload(case_id)
    assert r.status_code == 403 and "consent" in r.json()["detail"]
    assert store.documents(case_id) == []


def test_upload_stores_encrypted_file_and_metadata_only(case_id):
    consent(case_id, documents=True)
    r = upload(case_id, aadhaar_last4="9012")
    assert r.status_code == 200
    doc = r.json()
    assert doc["aadhaar"] == "XXXX XXXX 9012" and doc["doc_type"] == "identity_proof"
    assert "content" not in doc and "storage_key" not in doc

    (row,) = store.documents(case_id)
    assert row["sha256"] == hashlib.sha256(JPEG).hexdigest() and row["size_bytes"] == len(JPEG)
    on_disk = (vault.root / f"{row['storage_key']}.ysv").read_bytes()
    assert b"JFIF" not in on_disk and b"1234 5678 9012" not in on_disk
    assert vault.read(case_id, doc["id"]) == JPEG  # only the agent decrypts, to memory
    acts = [a["action"] for a in client.get(f"/cases/{case_id}/data").json()["audit"]]
    assert "document_stored" in acts and "document_read" in acts


def test_no_endpoint_returns_document_contents(case_id):
    consent(case_id, documents=True)
    doc = upload(case_id).json()
    for path in (f"/cases/{case_id}/documents", f"/cases/{case_id}/data",
                 f"/cases/{case_id}/documents/{doc['id']}"):
        r = client.get(path)
        assert b"JFIF" not in r.content


def test_full_aadhaar_is_refused(case_id):
    consent(case_id, documents=True)
    assert upload(case_id, aadhaar_last4="123456789012").status_code == 422


@pytest.mark.parametrize("doc, ctype, code", [("passport_selfie", "image/jpeg", 404),
                                              ("identity_proof", "text/plain", 415)])
def test_upload_validation(case_id, doc, ctype, code):
    consent(case_id, documents=True)
    assert upload(case_id, doc, ctype=ctype).status_code == code


def test_reupload_replaces_the_old_file(case_id):
    consent(case_id, documents=True)
    upload(case_id)
    (old,) = store.documents(case_id)
    upload(case_id, data=b"%PDF-1.7 second version", ctype="application/pdf")
    (new,) = store.documents(case_id)
    assert new["storage_key"] != old["storage_key"]
    assert not (vault.root / f"{old['storage_key']}.ysv").exists()


def test_delete_now(case_id):
    consent(case_id, documents=True)
    doc = upload(case_id).json()
    (row,) = store.documents(case_id)
    assert client.delete(f"/cases/{case_id}/documents/{doc['id']}").status_code == 200
    assert store.documents(case_id) == [] and not (vault.root / f"{row['storage_key']}.ysv").exists()
    assert client.delete(f"/cases/{case_id}/documents/{doc['id']}").status_code == 404


def test_withdrawing_document_consent_deletes_documents(case_id):
    consent(case_id, documents=True)
    upload(case_id)
    assert consent(case_id, documents=False)["documents"] is False
    assert store.documents(case_id) == []


def test_uploaded_document_shows_in_checklist_and_never_reaches_llm(case_id, fake_llm):
    consent(case_id, documents=True)
    upload(case_id)
    assert fake_llm.calls == []  # storing a document makes no LLM call
    out = client.post(f"/turn/{case_id}", json={"text": READY}).json()
    p1 = next(s for s in out["ui"]["schemes"] if s["scheme_id"] == "pension-001")
    status = {d["doc"]: d["status"] for d in p1["documents"]}
    assert status["identity_proof"] == "uploaded" and status["age_proof"] == "needed"
    assert all(b"JFIF" not in c.encode() for c in fake_llm.calls)


# --- auto-delete after submission ---------------------------------------------------------


def submit(case_id: str) -> None:
    client.post(f"/turn/{case_id}", json={"text": READY})
    client.post(f"/turn/{case_id}", json={"text": "senior citizen pension"})
    assert "DEMO-0001" in client.post(f"/turn/{case_id}", json={"text": "yes"}).json()["reply"]


def test_documents_expire_after_submission_and_are_purged(case_id, monkeypatch):
    consent(case_id, documents=True)
    upload(case_id)
    assert store.documents(case_id)[0]["expires_at"] is None  # no clock before submission
    monkeypatch.setattr(config, "DOC_RETENTION_HOURS", 0.0)
    submit(case_id)
    (row,) = store.documents(case_id)
    assert row["expires_at"] is not None
    assert vault.purge_expired() >= 1
    assert store.documents(case_id) == [] and not (vault.root / f"{row['storage_key']}.ysv").exists()
    acts = [a["action"] for a in client.get(f"/cases/{case_id}/data").json()["audit"]]
    assert "documents_expiry_set" in acts and "document_auto_deleted" in acts


def test_retention_window_keeps_documents_until_expiry(case_id):
    consent(case_id, documents=True)
    upload(case_id)
    submit(case_id)  # default retention: 24 h
    vault.purge_expired()
    assert len(store.documents(case_id)) == 1
