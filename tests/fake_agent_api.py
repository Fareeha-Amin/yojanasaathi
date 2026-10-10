"""A fake of the portal's agent API for a citizen (docs: YOJANASAATHI_AGENT_INTEGRATION.md in
the portal repo), served through httpx.MockTransport so the REAL client (agent/tracking/api.py)
and the real poller run against it.

It reads the FakeDriver's "portal" (the applications it submitted and their statuses), so a test
submits through /turn as usual and then changes the portal like an official in Django admin:

    fake_agent_api.set_status(app_no, "CORRECTION_REQUIRED", doc="Identity Proof", remark="Blurry")

Failures: fail_next(...) queues forced answers (429 with Retry-After, 500, a timeout),
revoke_all() makes every delegation token seen so far answer 403 DELEGATION_TOKEN_INVALID (a
renewed token is new and works). `calls` records (path, token-prefix) for call counting.
"""

import re
from typing import Any

import httpx

PREFIX = "/api/agent/v1/citizen"


class FakeAgentAPI:
    def __init__(self, driver):
        self.driver = driver
        self.meta: dict[str, dict[str, Any]] = {}  # app number -> {docs, events, updated_at}
        self.calls: list[str] = []
        self.revoked: set[str] = set()
        self.seen: set[str] = set()
        self._forced: list[Any] = []
        self._clock = 0

    # --- what an official does ------------------------------------------------------------

    def _stamp(self) -> str:
        self._clock += 1
        return f"2026-10-09T10:{self._clock // 60:02d}:{self._clock % 60:02d}Z"

    def set_status(self, app_no: str, status: str, *, doc: str | None = None, doc_status: str | None = None,
                   remark: str = "", note: str | None = None) -> None:
        """Change the status (and optionally flag one document) like the portal's admin."""
        self.driver.portal_status[app_no] = status
        m = self.meta.setdefault(app_no, {"docs": [], "events": [], "updated_at": None})
        m["updated_at"] = self._stamp()
        if doc:
            m["docs"] = [d for d in m["docs"] if d["document_type"] != doc] + [{
                "id": f"d-{len(m['docs'])}", "document_type": doc, "file_name": "x.pdf", "file_size": 10,
                "verification_status": doc_status or "CORRECTION_REQUIRED", "remarks": remark,
                "uploaded_at": m["updated_at"], "verified_at": None}]
        m["events"].append({"id": f"e-{len(m['events'])}", "type": "STATUS", "title": f"Status: {status}",
                            "message": note if note is not None else remark, "created_at": m["updated_at"]})

    def flag_document(self, app_no: str, doc: str, remark: str, status: str = "CORRECTION_REQUIRED") -> None:
        """A document flagged while the application's status stays the same."""
        m = self.meta.setdefault(app_no, {"docs": [], "events": [], "updated_at": None})
        m["updated_at"] = self._stamp()
        m["docs"] = [d for d in m["docs"] if d["document_type"] != doc] + [{
            "id": f"d-{len(m['docs'])}", "document_type": doc, "file_name": "x.pdf", "file_size": 10,
            "verification_status": status, "remarks": remark, "uploaded_at": m["updated_at"], "verified_at": None}]

    def revoke_all(self) -> None:
        self.revoked |= self.seen | {f"yjs_del_fake{i}" for i in range(1, self.driver._d + 1)}

    def fail_next(self, *answers: Any) -> None:
        """Each answer: an int status, (status, headers), (status, headers, json) or an Exception."""
        self._forced.extend(answers)

    def count(self, what: str) -> int:
        return sum(1 for c in self.calls if what in c)

    # --- the HTTP side ------------------------------------------------------------------------

    def _apps(self) -> list[dict[str, Any]]:
        out = []
        for (mobile, sid), number in self.driver.submitted.items():
            status = self.driver.portal_status.get(number, "SUBMITTED")
            m = self.meta.get(number, {})
            out.append({"application_number": number, "scheme_id": sid, "scheme_title": sid, "status": status,
                        "status_label": status.replace("_", " ").title(), "next_step": "…",
                        "submitted_at": "2026-10-09T09:45:00Z", "created_at": "2026-10-09T09:40:00Z",
                        "updated_at": m.get("updated_at") or "2026-10-09T09:45:00Z", "documents_count": 1,
                        "verified_documents_count": 0, "total_required_documents_count": 1,
                        "all_documents_verified": False})
        return out

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        token = request.headers.get("X-Citizen-Delegation-Token", "")
        self.calls.append(path.removeprefix(PREFIX))
        if self._forced:
            f = self._forced.pop(0)
            if isinstance(f, Exception):
                raise f
            status, headers, body = (f, {}, {"error": "forced", "code": "X"}) if isinstance(f, int) else (
                (f[0], f[1], {"error": "forced", "code": "THROTTLED" if f[0] == 429 else "X"}) if len(f) == 2 else f)
            return httpx.Response(status, headers=headers, json=body)
        if not request.headers.get("X-Agent-API-Key"):
            return httpx.Response(401, json={"error": "no key", "code": "AGENT_AUTHENTICATION_REQUIRED"})
        self.seen.add(token)
        if token in self.revoked or not token.startswith("yjs_del_"):
            return httpx.Response(403, json={"error": "Citizen delegation has expired or been revoked",
                                             "code": "DELEGATION_TOKEN_INVALID"})
        rel = path.removeprefix(PREFIX)
        if rel == "/applications/":
            apps = self._apps()
            return httpx.Response(200, json={"count": len(apps), "citizen_mobile": "XXXXXX3210", "results": apps})
        if rel == "/notifications/":
            return httpx.Response(200, json={"count": 0, "results": []})
        m = re.fullmatch(r"/applications/(YJS-[0-9A-F]+)/(history|documents)/", rel)
        if m:
            number, kind = m.groups()
            if number not in {a["application_number"] for a in self._apps()}:
                return httpx.Response(404, json={"error": "no", "code": "APPLICATION_NOT_FOUND"})
            meta = self.meta.get(number, {"docs": [], "events": [], "updated_at": None})
            if kind == "documents":
                return httpx.Response(200, json={"application_number": number, "total_documents": len(meta["docs"]),
                                                 "verified_documents": 0, "results": meta["docs"]})
            return httpx.Response(200, json={
                "application_number": number, "current_status": self.driver.portal_status.get(number, "SUBMITTED"),
                "lifecycle_events": meta["events"], "documents_summary": {}})
        return httpx.Response(404, json={"error": "unknown", "code": "NOT_FOUND"})
