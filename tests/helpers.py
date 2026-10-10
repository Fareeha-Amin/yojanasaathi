"""Shared test helpers (not conftest.py: importing that twice would recreate the test DB)."""

import re

from fastapi.testclient import TestClient

from agent.llm import Extraction


class FakeLLM:
    """Deterministic parsers only, unless a test scripts what the "LLM" returns. Every test
    gets one (conftest.fake_llm); module-scoped fixtures must install their own."""

    state = "ready"

    def __init__(self):
        self.extractions: dict[str, Extraction] = {}  # message -> scripted extraction
        self.answers: dict[str, str] = {}
        self.calls: list[str] = []

    def extract(self, msg, asking=None):
        self.calls.append(msg)
        return self.extractions.get(msg)

    def answer(self, msg, lang, kb, profile):
        return self.answers.get(msg)

    def warmup(self):
        pass


_CASE_PATH = re.compile(r"^/cases/([^/?]+)")


def bearer(case_id: str) -> dict[str, str]:
    """The Authorization header the web app sends for this case (agent.main sets the key)."""
    from agent import auth

    return {"Authorization": f"Bearer {auth.issue(case_id)[0]}"}


# Answers the deterministic form parsers read (agent/portal/fields.py), in English; the
# case keeps its language (Latin text never switches it).
FORM_ANSWERS = {
    "full_name": "Ramesh Kumar", "applicant_name": "Ramesh Kumar", "nominee_name": "Sita Kumar",
    "gender": "male", "marital_status": "married", "disbursement_mode": "bank transfer",
    "assistance_category": "old age", "ration_card_category": "BPL", "coverage_preference": "diagnostic",
    "family_members_count": "4", "annual_income": "1 lakh 20 thousand",
    "bank_account_number": "3456 7890 123", "bank_ifsc": "SBIN0001234",
    "address": "12 Temple Road, Tumakuru 572101", "hospital_name": "District Hospital",
    "medical_condition": "Knee replacement surgery", "mobile": "98765 43210", "declaration_consent": "yes",
}
OTP = "123456"


def dob_for(age: int) -> str:
    """A date of birth that makes the citizen exactly `age` today."""
    from datetime import date

    return f"1 January {date.today().year - age}"


def upload_documents(client, case_id: str, scheme_id: str, *, skip: tuple[str, ...] = ()) -> None:
    """Document consent + every document the scheme lists (small fake PDFs)."""
    from agent import rules

    client.put(f"/cases/{case_id}/consent", json={"documents": True}).raise_for_status()
    for d in rules.load_schemes()[scheme_id]["documents"]:
        if d["doc"] in skip:
            continue
        client.put(f"/cases/{case_id}/documents/{d['doc']}", content=b"%PDF-1.4 test " + d["doc"].encode(),
                   headers={"Content-Type": "application/pdf"}).raise_for_status()


def answer_form(turn, out: dict, *, age: int | None = 62, answers: dict | None = None,
                max_turns: int = 20) -> dict:
    """Answer the agent's form questions (ui type "form") until it stops asking; returns
    the /turn output after the last answer (normally the OTP pause). `turn(text)` sends one
    message and returns the /turn JSON."""
    answers = {**FORM_ANSWERS, "dob": dob_for(age) if age else "1 January 1960", **(answers or {})}
    for _ in range(max_turns):
        ui = out.get("ui") or {}
        if ui.get("type") != "form":
            return out
        out = turn(answers[ui["field"]])
    raise AssertionError("the agent kept asking form questions")


def to_confirm(turn, out: dict, **kw) -> dict:
    """From the first form question to the confirm pause: answers + OTP."""
    out = answer_form(turn, out, **kw)
    assert (out.get("pause") or {}).get("type") == "otp", out
    out = turn(OTP)
    assert (out.get("pause") or {}).get("type") == "confirm", out
    return out


class CaseClient(TestClient):
    """A TestClient that sends the case's session token on /cases/{case_id}/... requests,
    like the web app does. Auth itself (missing / wrong / expired tokens) is tested in
    tests/test_auth.py with a plain TestClient."""

    def request(self, method, url, *args, **kwargs):
        m = _CASE_PATH.match(str(url))
        if m:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("Authorization", bearer(m.group(1))["Authorization"])
            kwargs["headers"] = headers
        return super().request(method, url, *args, **kwargs)
