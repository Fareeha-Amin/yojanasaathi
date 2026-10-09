"""FakeDriver: an in-process stand-in for the PlaywrightDriver (agent/portal/browser.py),
like FakeLLM for the LLM. Every test gets one (tests/conftest.py); the browser tests in
tests/test_portal_browser.py use the real PlaywrightDriver against tests/fake_portal.py.

It keeps the same contract: one session per case, OTP 123456 (only after start_login),
fill() reads the documents through the recorder (as the browser would), stops at the
review with a fingerprint, submit() only from the review, numbers YJS-XXXXXXXXXX. A test
can count calls (`calls`), see what was submitted (`submitted`), or make a step fail
(`fail[step] = SafeStop(...)`).
"""

import hashlib
import json
from typing import Any

from agent.portal import Delegation, FillResult, LoginResult, SafeStop, SessionGone, progress
from agent.portal.api import DELEGATION_SCOPES

OTP = "123456"


class FakeDriver:
    def __init__(self, requirements: dict[str, dict[str, Any]] | None = None):
        from agent import rules

        self._reqs = requirements or {
            sid: {"application_fields": [{k: v for k, v in f.items() if k != "label"} | {"label": f["label"]["en"]}
                                         for f in s["application_fields"]],
                  "required_documents": [d["label"]["en"] for d in s["documents"]]}
            for sid, s in rules.load_schemes().items()}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.submitted: dict[tuple[str, str], str] = {}  # (mobile, scheme) -> app number (the "portal")
        self.submits: list[tuple[str, str, str]] = []  # every Submit click: (case, scheme, number)
        self.calls: list[str] = []
        self.fail: dict[str, Exception] = {}
        self.refuse_mobiles: set[str] = set()
        self.uploaded: list[tuple[str, str, int]] = []  # (case, doc, size)
        self.values: dict[str, dict[str, Any]] = {}  # case -> what was "typed" (tests only)
        self._n = 0

    def _maybe_fail(self, step: str) -> None:
        self.calls.append(step)
        if step in self.fail:
            raise self.fail.pop(step)

    def state(self) -> str:
        return "ready"

    def warmup(self, wait: bool = False) -> None:
        self.calls.append("warmup")

    def requirements(self, scheme_id: str) -> dict[str, Any]:
        self._maybe_fail("requirements")
        return self._reqs[scheme_id]

    def start_login(self, case_id: str, mobile: str) -> LoginResult:
        self._maybe_fail("login")
        progress.start(case_id)
        progress.recorder().step(case_id, None, "login", "done", b"\x89PNG fake login")
        if mobile in self.refuse_mobiles:
            return LoginResult(sent=False, refused=True)
        self.sessions[case_id] = {"stage": "otp", "mobile": mobile}
        return LoginResult(sent=True)

    def _session(self, case_id: str, *stages: str) -> dict[str, Any]:
        s = self.sessions.get(case_id)
        if s is None or (stages and s["stage"] not in stages):
            raise SessionGone("no session")
        return s

    def verify_otp(self, case_id: str, code: str) -> bool:
        self._maybe_fail("verify")
        s = self._session(case_id, "otp")
        if code != OTP:
            return False
        s["stage"] = "logged_in"
        progress.recorder().step(case_id, None, "otp", "done", b"\x89PNG fake otp")
        return True

    def resend_otp(self, case_id: str) -> None:
        self._maybe_fail("resend")
        self._session(case_id, "otp")

    def existing_application(self, case_id: str, scheme_id: str) -> dict[str, Any] | None:
        s = self._session(case_id, "logged_in", "review")
        number = self.submitted.get((s["mobile"], scheme_id))
        return {"app_id": number, "status": "SUBMITTED"} if number else None

    def fill(self, case_id: str, scheme: dict[str, Any], values: dict[str, Any], docs) -> FillResult:
        self._maybe_fail("fill")
        s = self._session(case_id, "logged_in", "review")
        sid = scheme["scheme_id"]
        rec = progress.recorder()
        for key in ("applicant", "details"):
            rec.step(case_id, sid, key, "done", b"\x89PNG fake " + key.encode())
        names = []
        for d in docs:
            if d.doc_id is None:
                continue
            data = rec.read_doc(case_id, d.doc_id)
            self.uploaded.append((case_id, d.doc, len(data)))
            names.append(d.name)
        rec.step(case_id, sid, "documents", "done", b"\x89PNG fake documents")
        missing = [f["name"] for f in scheme["application_fields"] if f["required"] and values.get(f["name"]) in (None, "")]
        if missing or values.get("declaration_consent") is not True:
            raise SafeStop("no value for a required field", step="details", detail={"fields": missing})
        shown = {k: v for k, v in values.items()}
        self.values[case_id] = shown
        fp = hashlib.sha256(json.dumps({"s": sid, "v": shown, "d": names}, sort_keys=True).encode()).hexdigest()
        s.update(stage="review", scheme=sid, fingerprint=fp)
        rec.step(case_id, sid, "review", "done", b"\x89PNG fake review")
        return FillResult(shown, names, fp)

    def fresh(self, case_id: str, scheme_id: str, fingerprint: str) -> bool:
        s = self.sessions.get(case_id)
        return bool(s and s["stage"] == "review" and s.get("scheme") == scheme_id and s.get("fingerprint") == fingerprint)

    def submit(self, case_id: str, scheme_id: str) -> str:
        self._maybe_fail("submit")
        s = self._session(case_id, "review")
        self._n += 1
        number = f"YJS-{self._n:010X}"
        self.submitted[(s["mobile"], scheme_id)] = number
        self.submits.append((case_id, scheme_id, number))
        s["stage"] = "done"
        progress.recorder().step(case_id, scheme_id, "submit", "done", b"\x89PNG fake submit")
        return number

    def delegate(self, case_id: str) -> Delegation:
        self._maybe_fail("delegate")
        self._session(case_id)
        return Delegation(token=f"yjs_del_fake{self._n}", expires_at="2026-10-10T12:00:00Z", scopes=DELEGATION_SCOPES)

    def stage(self, case_id: str) -> str | None:
        s = self.sessions.get(case_id)
        return s["stage"] if s else None

    def close(self, case_id: str, reason: str = "done") -> None:
        self.calls.append(f"close:{reason}")
        self.sessions.pop(case_id, None)

    def expire(self, case_id: str) -> None:
        """Tests: the browser session times out (idle) or the agent restarted."""
        self.sessions.pop(case_id, None)

    def shutdown(self) -> None:
        pass

    def submit_count(self, scheme_id: str | None = None) -> int:
        """Every Submit click, also a second one for the same scheme (must never happen)."""
        return sum(1 for (_, sid, _) in self.submits if scheme_id in (None, sid))
