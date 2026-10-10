"""Case memory lives in Postgres: a pause at confirm and the per-scheme `applications`
survive an agent restart, so per-scheme idempotency holds across restarts. Plus the
tables: case row, timeline, append-only audit log, and the startup failure message."""

import json
import subprocess
import sys
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from agent import config, db
from agent.config import ROOT
from agent.main import app, store
from agent.replies import t
from tests import helpers
from tests.helpers import CaseClient

client = CaseClient(app)

READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"

# A separate Python process = a real agent restart: nothing in memory carries over (also
# not the browser session: the real PlaywrightDriver, against tests/fake_portal.py).
# "<FORM>" answers the form questions (tests/helpers.answer_form).
CHILD = """
import json, sys
from agent.main import app
from tests.helpers import CaseClient, answer_form, upload_documents
job = json.loads(sys.stdin.read())
c = CaseClient(app)
def turn(text):
    return c.post("/turn/" + job["case"], json={"text": text}).json()
if job.get("docs"):
    upload_documents(c, job["case"], job["docs"])
out = {}
for text in job["texts"]:
    out = answer_form(turn, out) if text == "<FORM>" else turn(text)
    print(json.dumps(out, ensure_ascii=False))
"""


@pytest.fixture
def case_id() -> str:
    return f"persist-{uuid.uuid4().hex[:8]}"


def agent_process(env: dict, case_id: str, *texts: str, docs: str | None = None) -> list[dict]:
    """Start the agent in a fresh process, send the turns, stop it."""
    r = subprocess.run([sys.executable, "-c", CHILD],
                       input=json.dumps({"case": case_id, "texts": texts, "docs": docs}),
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]


def to_confirm(case_id: str) -> dict:
    helpers.upload_documents(client, case_id, "pension-001")
    client.post(f"/turn/{case_id}", json={"text": READY})
    out = client.post(f"/turn/{case_id}", json={"text": "senior citizen pension"}).json()
    return helpers.to_confirm(lambda m: client.post(f"/turn/{case_id}", json={"text": m}).json(), out)


def rows(sql: str, *args) -> list[dict]:
    with store.pool.connection() as conn:
        return conn.execute(sql, args).fetchall()


def actions(case_id: str) -> list[str]:
    return [r["action"] for r in rows("SELECT action FROM audit_log WHERE case_id = %s ORDER BY id", case_id)]


@pytest.fixture
def fake_site(subprocess_env):
    from tests.fake_portal import AGENT_KEY, FakePortal
    from tests.test_portal_browser import _chromium_installed

    if not _chromium_installed():
        pytest.skip("Playwright Chromium not installed")
    site = FakePortal().start()
    yield site, {**subprocess_env, "MOCK_PORTAL_URL": site.url, "MOCK_PORTAL_API": site.api,
                 "MOCK_PORTAL_AGENT_KEY": AGENT_KEY, "PORTAL_FIRST_TIMEOUT": "20"}
    site.stop()


def test_restart_at_confirm_then_yes_submits_once(case_id, fake_site):
    site, env = fake_site
    # Process 1: documents, interview, pension-001, form questions, OTP -> paused at the
    # confirm gate with the portal's review page open. Then the agent stops.
    out = agent_process(env, case_id, READY, "senior citizen pension", "<FORM>", helpers.OTP, docs="pension-001")
    assert out[-1]["pause"]["type"] == "confirm"

    # Process 2 (restarted agent): the browser session died with process 1, so "ಹೌದು"
    # must not submit that preview: log in again (new OTP), fill again, a new read-back,
    # and only then the yes submits. The second "ಹೌದು" gets the existing ID back.
    relogin, refill, done, again = agent_process(env, case_id, "ಹೌದು", helpers.OTP, "ಹೌದು", "ಹೌದು")
    assert relogin["pause"]["type"] == "otp"  # not submitted: audit shows submit_blocked_stale below
    assert refill["pause"]["type"] == "confirm"
    assert done["pause"] is None and "YJS-" in done["reply"]
    [(_, app_id)] = site.submissions
    assert app_id in done["reply"]
    assert again["pause"] is None and again["reply"] == t("already_submitted", "kn", app_id=app_id)

    # Process 3: still the same ID after another restart, still one submission.
    (third,) = agent_process(env, case_id, "yes")
    assert app_id in third["reply"] and third["pause"] is None
    assert len(site.submissions) == 1
    assert actions(case_id).count("submitted") == 1
    assert actions(case_id).count("submit_blocked_stale") == 1
    assert actions(case_id).count("resubmit_blocked") == 2


def test_case_row_timeline_and_audit(case_id):
    to_confirm(case_id)
    (case,) = rows("SELECT * FROM cases WHERE case_id = %s", case_id)
    assert case["paused"] == "confirm" and case["selected_scheme"] == "pension-001"
    assert case["status"] == "awaiting_confirmation"

    client.post(f"/turn/{case_id}", json={"text": "yes"})
    (case,) = rows("SELECT * FROM cases WHERE case_id = %s", case_id)
    assert case["paused"] is None and case["status"] == "submitted"
    assert case["applications"] == {"pension-001": {"app_id": "YJS-0000000001", "status": "SUBMITTED"}}

    kinds = [r["kind"] for r in rows("SELECT kind FROM case_events WHERE case_id = %s ORDER BY id", case_id)]
    assert kinds.count("submitted") == 1 and kinds[-1] == "submitted"
    assert {"turn", "portal_step"} <= set(kinds)
    acts = actions(case_id)
    key = ["case_opened", "eligibility_decided", "form_answered", "declaration_affirmed", "otp_requested",
           "otp_verified", "document_read", "form_filled", "confirm_requested", "citizen_approved",
           "submit_clicked", "submitted", "delegation_granted"]
    assert [a for a in acts if a in key] == sorted([a for a in acts if a in key], key=key.index)
    assert set(key) <= set(acts)
    audit = {r["action"]: r for r in rows("SELECT * FROM audit_log WHERE case_id = %s", case_id)}
    assert audit["citizen_approved"]["actor"] == "citizen"
    assert audit["citizen_approved"]["detail"] == {"decision": "yes", "said": "yes"}
    assert audit["submitted"]["scheme_id"] == "pension-001" and audit["submitted"]["actor"] == "browser_agent"
    assert audit["submitted"]["detail"]["app_id"] == "YJS-0000000001"
    assert audit["otp_requested"]["detail"] == {"mobile_last4": "3210"}
    # decisions name the fields they used, never the citizen's values
    decided = audit["eligibility_decided"]["detail"]
    assert decided["based_on"] == ["age", "annual_income"] and "120000" not in json.dumps(decided)
    everything = json.dumps([r["detail"] for r in rows("SELECT detail FROM audit_log WHERE case_id = %s", case_id)])
    for secret in ("9876543210", "34567890123", "SBIN0001234", "Ramesh", "123456", "yjs_del_"):
        assert secret not in everything, secret


def test_timeline_has_no_message_text(case_id):
    client.post(f"/turn/{case_id}", json={"text": READY})
    (ev,) = rows("SELECT detail FROM case_events WHERE case_id = %s", case_id)
    assert "62" not in json.dumps(ev["detail"]) and "lakh" not in json.dumps(ev["detail"])


def test_declined_and_unclear_are_audited(case_id):
    to_confirm(case_id)
    client.post(f"/turn/{case_id}", json={"text": "what documents do I need for this?"})
    client.post(f"/turn/{case_id}", json={"text": "no"})
    acts = actions(case_id)
    assert acts[-2:] == ["confirm_unclear", "citizen_declined"]
    unclear = rows("SELECT detail FROM audit_log WHERE case_id = %s AND action = 'confirm_unclear'", case_id)
    assert unclear[0]["detail"] == {"decision": "unclear"}  # longer answers are not copied


@pytest.mark.parametrize("sql", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log",
                                 "TRUNCATE audit_log"])
def test_audit_log_is_append_only(sql):
    store.log_event("system", "test_row")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with store.pool.connection() as conn:
            conn.execute(sql)


def test_profile_needs_consent_in_the_database_itself(case_id):
    citizen = store.ensure_case(case_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.pool.connection() as conn:
            conn.execute("INSERT INTO profiles (citizen_id, data) VALUES (%s, '{\"age\": 62}')", (citizen,))


def test_health_reports_db():
    assert client.get("/health").json()["db"] == "ok"


# --- startup fails clearly, never falls back to memory ---------------------------------


def test_startup_without_database_url():
    with pytest.raises(db.StartupError, match="DATABASE_URL is not set"):
        db.open_store(None)


def test_startup_server_down_says_what_to_do():
    with pytest.raises(db.StartupError) as e:
        db.open_store("postgresql://postgres:s3cret-pw@127.0.0.1:1/yojanasaathi")
    msg = str(e.value)
    assert "Is PostgreSQL running?" in msg and "postgresql-x64-17" in msg
    assert "s3cret-pw" not in msg and "postgres:***@127.0.0.1:1/yojanasaathi" in msg


def test_startup_wrong_password():
    info = psycopg.conninfo.conninfo_to_dict(config.DATABASE_URL)
    bad = psycopg.conninfo.make_conninfo(config.DATABASE_URL, password="definitely-wrong")
    with pytest.raises(db.StartupError) as e:
        db.open_store(bad)
    assert "Check the user and password" in str(e.value) and "definitely-wrong" not in str(e.value)
    assert info.get("password", "<none>") not in str(e.value)


def test_startup_missing_database():
    bad = psycopg.conninfo.make_conninfo(config.DATABASE_URL, dbname="no_such_db_ys")
    with pytest.raises(db.StartupError, match='CREATE DATABASE "no_such_db_ys"'):
        db.open_store(bad)


def test_agent_process_refuses_to_start_without_postgres(subprocess_env):
    env = {**subprocess_env, "DATABASE_URL": "postgresql://postgres:x@127.0.0.1:1/yojanasaathi"}
    r = subprocess.run([sys.executable, "-c", "import agent.main"], capture_output=True, text=True,
                       encoding="utf-8", env=env, cwd=ROOT, timeout=120)
    assert r.returncode != 0
    assert "StartupError: Cannot connect to PostgreSQL" in r.stderr


def test_recreate_refuses_non_test_database():
    with pytest.raises(ValueError, match="only \\*_test"):
        db.recreate_database("postgresql://postgres@localhost/yojanasaathi")
