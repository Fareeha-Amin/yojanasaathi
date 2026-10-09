"""The pre-fill step list (web app Pre-fill screen) and the Recorder that stores what the
browser agent does.

Live: while a turn is filling the form, GET /cases/{id}/summary (no case lock) reads the
steps from here, so the screen moves along with the browser. The portal nodes also copy
them into the case state, which is what a reload after the turn sees.

Recorder.step(): screenshot -> encrypted vault (agent/vault.py), one `portal_step` row in
case_events (no values), and the live step list. Wired in agent/main.py; tests get one
without screenshots storage when they don't need it.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Callable

STEPS = ["login", "otp", "applicant", "details", "documents", "review", "submit"]

_lock = threading.Lock()
_live: dict[str, list[dict[str, Any]]] = {}


def start(case_id: str) -> None:
    with _lock:
        _live[case_id] = [{"key": k, "status": "waiting", "screenshot": None} for k in STEPS]


def update(case_id: str, key: str, status: str, screenshot: str | None = None) -> None:
    with _lock:
        steps = _live.setdefault(case_id, [{"key": k, "status": "waiting", "screenshot": None} for k in STEPS])
        for s in steps:
            if s["key"] == key:
                s["status"] = status
                if screenshot:
                    s["screenshot"] = screenshot
                s["at"] = datetime.now(timezone.utc).isoformat()


def get(case_id: str) -> list[dict[str, Any]] | None:
    with _lock:
        steps = _live.get(case_id)
        return [dict(s) for s in steps] if steps else None


def forget(case_id: str) -> None:
    with _lock:
        _live.pop(case_id, None)


class Recorder:
    """The portal side's link to our storage (wired in agent/main.py).
    save_shot(case_id, step, png) -> id   encrypt a screenshot into the vault
    event(case_id, kind, scheme_id, detail) a case_events row (no values)
    audit(actor, action, case_id=, scheme_id=, detail=)   = audit.log_event
    documents(case_id) -> metadata rows   what the citizen uploaded (never contents)
    read_doc(case_id, doc_id) -> bytes     decrypt one document to memory (audited)"""

    def __init__(self, save_shot: Callable[[str, str, bytes], str] | None = None,
                 event: Callable[..., None] | None = None, audit: Callable[..., None] | None = None,
                 documents: Callable[[str], list[dict[str, Any]]] | None = None,
                 read_doc: Callable[[str, str], bytes] | None = None):
        self._save_shot = save_shot
        self._event = event
        self._audit = audit
        self.documents = documents or (lambda case_id: [])
        self.read_doc = read_doc

    def step(self, case_id: str, scheme_id: str | None, key: str, status: str,
             png: bytes | None = None) -> str | None:
        shot = self._save_shot(case_id, key, png) if (png and self._save_shot) else None
        update(case_id, key, status, shot)
        if self._event:
            self._event(case_id, "portal_step", scheme_id, {"step": key, "status": status, "screenshot": shot})
        return shot

    def audit(self, actor: str, action: str, case_id: str, scheme_id: str | None = None,
              detail: dict[str, Any] | None = None) -> None:
        if self._audit:
            self._audit(actor, action, case_id=case_id, scheme_id=scheme_id, detail=detail)


_recorder = Recorder()


def set_recorder(recorder: Recorder) -> None:
    global _recorder
    _recorder = recorder


def recorder() -> Recorder:
    return _recorder
