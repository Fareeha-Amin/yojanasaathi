"""The real PlaywrightDriver (headless Chromium) against the local fake portal
(tests/fake_portal.py: same data-testids, OTP 123456), through /turn.

Covers: happy path, wrong OTP x3, missing document, missing testid, a dialog, edit at the
review, a double yes, off-host requests blocked, no plaintext document on disk, no full
numbers / OTP in the logs. Skipped when Playwright's Chromium is not installed
(python -m playwright install chromium).
"""

import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from agent import config, portal
from agent.main import app, graph, store
from tests.fake_portal import AGENT_KEY, FakePortal, Tracker
from tests.helpers import OTP, CaseClient, answer_form, upload_documents

try:
    from playwright.sync_api import sync_playwright  # noqa: F401

    from agent.portal.browser import PlaywrightDriver
except ImportError:  # pragma: no cover
    PlaywrightDriver = None


def _chromium_installed() -> bool:
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / "AppData/Local/ms-playwright")
    return any(root.glob("chromium-*")) or any(Path.home().glob(".cache/ms-playwright/chromium-*"))


pytestmark = pytest.mark.skipif(PlaywrightDriver is None or not _chromium_installed(),
                                reason="Playwright Chromium not installed")

client = CaseClient(app)
READY = "I'm 62 and our income is 1 lakh 20 thousand. Can I get a pension?"
MARK = b"%PDF-1.4 PLAINTEXT-MARKER-"


@pytest.fixture(scope="module")
def site():
    fake = FakePortal().start()
    yield fake
    fake.stop()


@pytest.fixture(scope="module")
def browser_driver(site):
    driver = PlaywrightDriver()
    yield driver
    driver.shutdown()


@pytest.fixture(autouse=True)
def use_browser(site, browser_driver, monkeypatch):
    monkeypatch.setattr(config, "MOCK_PORTAL_URL", site.url)
    monkeypatch.setattr(config, "MOCK_PORTAL_API", site.api)
    monkeypatch.setattr(config, "MOCK_PORTAL_AGENT_KEY", AGENT_KEY)
    monkeypatch.setattr(config, "PORTAL_FIRST_TIMEOUT", 20.0)
    site.reset()
    portal.set_driver(browser_driver)
    yield
    portal.set_driver(None)


@pytest.fixture
def case_id() -> str:
    return f"browser-{uuid.uuid4().hex[:8]}"


def turn(case_id: str, text: str) -> dict:
    r = client.post(f"/turn/{case_id}", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def mobile_of(case_id: str) -> str:
    """One citizen (portal account) per test case: the fake portal lives for the module."""
    return "9" + str(int(case_id.split("-")[-1], 16) % 10**9).zfill(9)


def to_otp(case_id: str, answers: dict | None = None, **kw) -> dict:
    upload_documents(client, case_id, "pension-001")
    for d in ("identity_proof",):  # one document with a marker, to look for on disk
        client.put(f"/cases/{case_id}/documents/{d}", content=MARK + case_id.encode(),
                   headers={"Content-Type": "application/pdf"}).raise_for_status()
    turn(case_id, READY)
    answers = {"mobile": mobile_of(case_id), **(answers or {})}
    return answer_form(lambda t: turn(case_id, t), turn(case_id, "senior citizen pension"), answers=answers, **kw)


def audit(case_id: str) -> list[dict]:
    return store.case_data(case_id)["audit"]


def test_happy_path_and_double_yes(case_id, site):
    out = to_otp(case_id)
    assert (out["pause"] or {}).get("type") == "otp", out["reply"]
    assert site.otp_sent[-1] == mobile_of(case_id)
    out = turn(case_id, OTP)
    assert (out["pause"] or {}).get("type") == "confirm", out["reply"]
    assert site.submissions == [] or all(n for _, n in site.submissions)
    before = len(site.submissions)
    # the page values were read back: the review shows the masked account, not the digits
    form = {f["field"]: f for f in out["pause"]["preview"]["form"]}
    assert form["bank_account_number"]["text"] == "XXXXXX0123"
    assert form["marital_status"]["text_en"] == "Married"
    # documents arrived intact (the bytes, from memory)
    got = {d: b for (_, d, b) in site.uploads}
    assert got["Identity Proof"] == MARK + case_id.encode()
    assert len(got) == 5

    out = turn(case_id, "yes")
    app_id = graph.get_state({"configurable": {"thread_id": case_id}}).values["applications"]["pension-001"]["app_id"]
    assert app_id in out["reply"] and len(site.submissions) == before + 1
    assert site.submissions[-1] == ("pension-001", app_id)
    again = turn(case_id, "yes")
    assert app_id in again["reply"] and len(site.submissions) == before + 1
    actions = [r["action"] for r in audit(case_id)]
    for a in ("otp_requested", "otp_verified", "document_uploaded", "form_filled", "confirm_requested",
              "citizen_approved", "submit_clicked", "submitted", "delegation_granted", "portal_session_closed"):
        assert a in actions, a
    steps = [e["detail"]["step"] for e in store.case_data(case_id)["events"] if e["kind"] == "portal_step"]
    assert {"login", "otp", "applicant", "details", "documents", "review", "submit"} <= set(steps)


def test_wrong_otp_three_times(case_id, site):
    to_otp(case_id)
    for code in ("111111", "222222"):
        out = turn(case_id, code)
        assert (out["pause"] or {}).get("type") == "otp" and "didn't work" in out["reply"]
    out = turn(case_id, "333333")
    assert out["pause"] is None and "three times" in out["reply"]
    assert not any(p.startswith("/api/applications") for _, p in site.requests[-5:])


def test_missing_document_never_opens_the_browser(case_id, site):
    upload_documents(client, case_id, "pension-001", skip=("residence_proof",))
    turn(case_id, READY)
    before = len(site.requests)
    out = turn(case_id, "senior citizen pension")
    assert "Residence Proof" in out["reply"]
    assert len(site.requests) == before


def test_missing_testid_safe_stops(case_id, site):
    site.rename = {"apply-step2-next-btn": "apply-step2-continue-btn"}
    to_otp(case_id)
    out = turn(case_id, OTP)
    assert (out["pause"] or {}).get("type") == "safe_stop", out["reply"]
    stop = [r for r in audit(case_id) if r["action"] == "safe_stop"][-1]["detail"]
    assert stop["testid"] == "apply-step2-next-btn"
    assert out["pause"]["screenshot"]
    assert site.submissions == [] or ("pension-001", None) not in site.submissions


def test_dialog_safe_stops(case_id, site):
    site.dialog_at_step = 3
    to_otp(case_id)
    out = turn(case_id, OTP)
    assert (out["pause"] or {}).get("type") == "safe_stop", out["reply"]
    stop = [r for r in audit(case_id) if r["action"] == "safe_stop"][-1]["detail"]
    assert stop.get("dialog") == "alert"


def test_edit_at_review_fills_again(case_id, site):
    to_otp(case_id)
    turn(case_id, OTP)
    out = turn(case_id, "no, the account number is 5555 6666 777")
    assert (out["pause"] or {}).get("type") == "confirm", out["reply"]
    assert "ending 6777" in out["reply"]
    out = turn(case_id, "yes")
    assert "YJS-" in out["reply"]
    number = site.submissions[-1][1]
    submitted = next(a for a in site._apps if a["number"] == number)
    assert submitted["form_data"]["bank_account_number"] == "55556666777"
    assert submitted["form_data"]["declaration_consent"] is True


def test_offhost_requests_are_blocked(case_id, site):
    tracker = Tracker().start()
    try:
        site.offhost_url = tracker.url + "/pixel.png"
        to_otp(case_id)
        out = turn(case_id, OTP)
        assert (out["pause"] or {}).get("type") == "confirm", out["reply"]
        assert tracker.hits == 0
        blocked = [r["detail"]["host"] for r in audit(case_id) if r["action"] == "offhost_blocked"]
        assert f"127.0.0.1:{tracker.port}" in blocked
    finally:
        tracker.stop()


def test_drift_stops_before_any_sms(case_id, site):
    site.extra_field = {"name": "caste", "label": "Caste", "type": "text", "required": True}
    out = to_otp(case_id)
    assert (out["pause"] or {}).get("type") == "safe_stop"
    assert mobile_of(case_id) not in site.otp_sent


def test_already_submitted_on_the_portal_is_not_submitted_again(case_id, site):
    to_otp(case_id)
    turn(case_id, OTP)
    turn(case_id, "yes")
    first = site.submissions[-1][1]
    # "delete my data": our memory of the number is gone, the portal still has it
    client.delete(f"/cases/{case_id}/data").raise_for_status()
    out = to_otp(case_id)
    out = turn(case_id, OTP)
    assert first in out["reply"]
    assert [n for _, n in site.submissions].count(first) == 1 and len(site.submissions) == len(set(site.submissions))


def test_no_plaintext_document_on_disk(case_id):
    start = time.time()
    to_otp(case_id)
    turn(case_id, OTP)
    needle = MARK + case_id.encode()
    roots = [Path(tempfile.gettempdir()), config.VAULT_DIR]
    for root in roots:
        for p in root.rglob("*"):
            try:
                if p.is_file() and p.stat().st_mtime >= start - 1 and p.stat().st_size < 50_000_000:
                    assert needle not in p.read_bytes(), p
            except (PermissionError, OSError):
                continue


def test_no_full_numbers_or_otp_in_logs(case_id, caplog):
    with caplog.at_level(logging.DEBUG):
        to_otp(case_id, answers={"bank_account_number": "1234 5678 9012"})
        turn(case_id, "111111")
        turn(case_id, OTP)
        turn(case_id, "yes")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("123456789012", mobile_of(case_id), OTP, "111111", "SBIN0001234", "yjs_del_"):
        assert secret not in text, secret
