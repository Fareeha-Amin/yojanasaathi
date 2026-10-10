"""Web app sessions (Phase 5): HS256 JWT per case; every /cases/{id} endpoint needs it,
/turn stays open for the voice bot and agent.cli."""

import base64
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from agent import auth
from agent.main import app
from tests.helpers import bearer

client = TestClient(app)  # plain client: no automatic token


@pytest.fixture
def case_id() -> str:
    return f"auth-{uuid.uuid4().hex[:8]}"


# --- tokens ---------------------------------------------------------------------------


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def test_issue_and_verify_round_trip(case_id):
    token, exp = auth.issue(case_id)
    assert auth.verify(token) == case_id
    assert exp > time.time()


def test_expired_token_rejected(case_id):
    token, _ = auth.issue(case_id, ttl_seconds=60, now=1_000_000)
    assert auth.verify(token, now=1_000_059) == case_id
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify(token, now=1_000_060)


def test_tampered_payload_rejected(case_id):
    head, body, sig = auth.issue(case_id)[0].split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    claims["sub"] = "someone-else"
    with pytest.raises(auth.AuthError, match="signature"):
        auth.verify(f"{head}.{_b64(claims)}.{sig}")


def test_alg_none_rejected(case_id):
    _, body, _ = auth.issue(case_id)[0].split(".")
    with pytest.raises(auth.AuthError, match="algorithm"):
        auth.verify(f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{body}.")


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", "!!.??.**"])
def test_malformed_tokens_rejected(token):
    with pytest.raises(auth.AuthError):
        auth.verify(token)


def test_token_from_another_master_key_rejected(case_id):
    token, _ = auth.issue(case_id)
    original = auth._key
    try:
        auth.set_key(bytes(32))
        with pytest.raises(auth.AuthError, match="signature"):
            auth.verify(token)
    finally:
        auth._key = original


@pytest.mark.parametrize("header", [None, "", "Token abc", "Bearer", "Bearer "])
def test_bearer_header_parsing(header):
    with pytest.raises(auth.AuthError):
        auth.bearer(header)


def test_cli_prints_a_working_token(monkeypatch, capsys, case_id):
    monkeypatch.setattr("sys.argv", ["agent.auth", case_id])
    original = auth._key
    try:
        auth.main()
    finally:
        auth._key = original
    out = capsys.readouterr().out
    token = next(line.split()[1] for line in out.splitlines() if line.startswith("token:"))
    assert auth.verify(token) == case_id
    assert f"?session={token}" in out


# --- POST /session ------------------------------------------------------------------------


def test_session_without_token_creates_a_new_random_case():
    a = client.post("/session").json()
    b = client.post("/session").json()
    assert a["case_id"].startswith("web-") and len(a["case_id"]) == 4 + 32
    assert a["case_id"] != b["case_id"]
    assert auth.verify(a["token"]) == a["case_id"]


def test_session_with_token_refreshes_same_case(case_id):
    out = client.post("/session", headers=bearer(case_id)).json()
    assert out["case_id"] == case_id and auth.verify(out["token"]) == case_id


def test_session_with_bad_token_is_401():
    r = client.post("/session", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# --- /cases/{id}/... need the case's token; /turn does not --------------------------------

CASE_ENDPOINTS = [
    ("get", "/cases/{c}/summary", None),
    ("post", "/cases/{c}/edit", {"field": "age", "value": 62}),
    ("get", "/cases/{c}/consent", None),
    ("put", "/cases/{c}/consent", {"documents": True}),
    ("get", "/cases/{c}/documents", None),
    ("put", "/cases/{c}/documents/identity_proof", None),
    ("delete", "/cases/{c}/documents/00000000-0000-0000-0000-000000000000", None),
    ("get", "/cases/{c}/data", None),
    ("delete", "/cases/{c}/data", None),
]


@pytest.mark.parametrize("method, path, body", CASE_ENDPOINTS)
def test_case_endpoints_need_a_token(case_id, method, path, body):
    url = path.format(c=case_id)
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method, url, **kwargs).status_code == 401
    other = bearer(f"{case_id}-other")
    assert client.request(method, url, headers=other, **kwargs).status_code == 403
    assert client.request(method, url, headers={"Authorization": "Bearer x.y.z"}, **kwargs).status_code == 401


def test_valid_token_opens_the_case(case_id):
    r = client.get(f"/cases/{case_id}/summary", headers=bearer(case_id))
    assert r.status_code == 200 and r.json()["case_id"] == case_id


def test_turn_still_works_without_a_token(case_id):
    r = client.post(f"/turn/{case_id}", json={"text": "I'm 62"})
    assert r.status_code == 200


def test_web_case_from_session_shares_turn_and_summary():
    s = client.post("/session").json()
    client.post(f"/turn/{s['case_id']}", json={"text": "I'm 62"})
    out = client.get(f"/cases/{s['case_id']}/summary",
                     headers={"Authorization": f"Bearer {s['token']}"}).json()
    assert [p["field"] for p in out["profile"]] == ["age"]
