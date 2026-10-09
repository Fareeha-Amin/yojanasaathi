"""Phase 4: the browser agent on the mock portal (github.com/ayush81233/mock), never any
other site (design rule 6).

The graph's portal nodes (agent/portal/nodes.py) talk to a Driver:
  - PlaywrightDriver (agent/portal/browser.py): the real one. Scripted Playwright Chromium
    on MOCK_PORTAL_URL (data-testid selectors only) + the portal API (agent/portal/api.py).
  - tests use an in-process fake (tests/fake_driver.py), like the FakeLLM; the browser
    tests drive the real PlaywrightDriver against a local fake portal (tests/fake_portal.py).

Errors the driver raises (the nodes turn them into replies / pauses):
  SafeStop     the portal is not what the script expects (missing element, unexpected
               page, a dialog, a changed form): stop and hand control back (design rule 4)
  SessionGone  no live, logged-in browser session for this case (expired, restarted)
  Unavailable  the portal can't be reached (or is not configured)
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


class PortalError(Exception):
    def __init__(self, message: str, *, step: str | None = None, detail: dict[str, Any] | None = None,
                 screenshot: str | None = None):
        super().__init__(message)
        self.step = step
        self.detail = detail or {}  # names and IDs only: it is logged
        self.screenshot = screenshot


class SafeStop(PortalError):
    pass


class SessionGone(PortalError):
    pass


class Unavailable(PortalError):
    pass


@dataclass
class DocUpload:
    doc: str  # our document id (rules/*.json), e.g. "identity_proof"
    name: str  # the portal's document name ("Identity Proof"): its slug selects the input
    doc_id: str | None  # vault document id; None = optional and not provided
    content_type: str | None = None


@dataclass
class FillResult:
    values: dict[str, Any]  # what the page shows after filling (read back by data-testid)
    documents: list[str]  # portal document names with an "uploaded" badge
    fingerprint: str  # hash of values + documents: the preview the citizen approves
    already: dict[str, Any] | None = None  # {"app_id", "status"} if the scheme was submitted


@dataclass
class LoginResult:
    sent: bool  # the portal sent an OTP
    refused: bool = False  # the portal did not accept the mobile number


@dataclass
class Delegation:
    token: str  # yjs_del_...: sealed before it is stored, never logged
    expires_at: str | None
    scopes: list[str] = field(default_factory=list)


class Driver(Protocol):
    def state(self) -> str: ...  # portal API: cold | warming | ready | error
    def warmup(self, wait: bool = False) -> None: ...
    def requirements(self, scheme_id: str) -> dict[str, Any]: ...
    def start_login(self, case_id: str, mobile: str) -> LoginResult: ...
    def verify_otp(self, case_id: str, code: str) -> bool: ...
    def resend_otp(self, case_id: str) -> None: ...
    def existing_application(self, case_id: str, scheme_id: str) -> dict[str, Any] | None: ...
    def fill(self, case_id: str, scheme: dict[str, Any], values: dict[str, Any],
             docs: list[DocUpload]) -> FillResult: ...
    def fresh(self, case_id: str, scheme_id: str, fingerprint: str) -> bool: ...
    def submit(self, case_id: str, scheme_id: str) -> str: ...
    def delegate(self, case_id: str) -> Delegation: ...
    # Phase 6: a correction after CORRECTION_REQUIRED, through the portal's citizen API with
    # the token from this case's OTP login (the portal has no correction screen to script)
    def application_status(self, case_id: str, app_id: str) -> str | None: ...
    def replace_document(self, case_id: str, app_id: str, doc: DocUpload) -> None: ...
    def resubmit(self, case_id: str, app_id: str) -> str: ...
    def stage(self, case_id: str) -> str | None: ...  # None | otp | logged_in | review
    def close(self, case_id: str, reason: str = "done") -> None: ...
    def shutdown(self) -> None: ...


_driver: Driver | None = None


def set_driver(driver: Driver | None) -> None:
    global _driver
    _driver = driver


def get_driver() -> Driver:
    if _driver is None:
        raise Unavailable("no portal driver (agent.main sets the Playwright driver at startup)")
    return _driver
