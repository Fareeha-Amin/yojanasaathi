"""The mock portal's API (MOCK_PORTAL_API, ends with /api), over httpx.

- warm-up: the API sleeps when idle (Render); GET /status/ wakes it. Started at agent
  startup and again when a citizen picks a scheme. The first call may take
  PORTAL_FIRST_TIMEOUT (90 s); later calls PORTAL_TIMEOUT.
- requirements: GET /agent/v1/schemes/{id}/requirements/ with X-Agent-API-Key
  (application_fields, required_documents), fetched fresh and compared with rules/ before
  the login and again before every pre-fill.
- the citizen's applications: GET /applications/mine/ with the citizen's own token (from
  the OTP login in the browser): has this scheme been submitted already?
- delegation: POST /auth/agent-delegation/ with the citizen's token -> a read-only,
  24-hour delegation token for status tracking (Phase 6). The agent API can only read and
  draft, never submit; submitting is the citizen's "yes" + the browser.

Keys and tokens travel in headers only; nothing here logs them (or any response body).
"""

import logging
import threading
import time
from typing import Any

import httpx

from agent import config
from agent.portal import Delegation, SafeStop, Unavailable

log = logging.getLogger("yojanasaathi.portal")

DELEGATION_SCOPES = ["applications:read", "notifications:read"]
DELEGATION_HOURS = 24


class PortalAPI:
    def __init__(self) -> None:
        self._state = "cold"  # cold | warming | ready | error
        self._lock = threading.Lock()
        self._warm_thread: threading.Thread | None = None
        self._ready_at = 0.0

    # --- warm-up ---------------------------------------------------------------------

    def state(self) -> str:
        return self._state

    def _timeout(self) -> float:
        return config.PORTAL_TIMEOUT if self._state == "ready" else config.PORTAL_FIRST_TIMEOUT

    def _warm(self) -> None:
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=config.PORTAL_FIRST_TIMEOUT) as c:
                c.get(f"{config.MOCK_PORTAL_API}/status/").raise_for_status()
                if config.MOCK_PORTAL_URL:  # the static site (does not sleep, but cheap)
                    c.get(config.MOCK_PORTAL_URL + "/")
            self._state, self._ready_at = "ready", time.monotonic()
            log.info("portal ready (warm-up %.1f s)", time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001 (logged without URL or body)
            self._state = "error"
            log.warning("portal warm-up failed after %.1f s: %s", time.monotonic() - t0, type(e).__name__)

    def warmup(self, wait: bool = False) -> None:
        """Wake the API in the background (or wait for it). Skipped when it answered in the
        last 5 minutes or a warm-up is already running."""
        if not config.portal_ready():
            return
        with self._lock:
            recent = self._state == "ready" and time.monotonic() - self._ready_at < 300
            running = self._warm_thread is not None and self._warm_thread.is_alive()
            if not recent and not running:
                self._state = "warming"
                self._warm_thread = threading.Thread(target=self._warm, name="portal-warmup", daemon=True)
                self._warm_thread.start()
            thread = self._warm_thread
        if wait and thread is not None:
            thread.join(config.PORTAL_FIRST_TIMEOUT + 5)

    # --- calls -------------------------------------------------------------------------

    def _request(self, method: str, path: str, *, headers: dict[str, str], json: Any = None,
                 what: str) -> Any:
        if not config.portal_ready():
            raise Unavailable("portal settings missing or invalid (see the agent's startup warnings)")
        try:
            r = httpx.request(method, f"{config.MOCK_PORTAL_API}{path}", headers=headers, json=json,
                              timeout=self._timeout())
        except httpx.HTTPError as e:
            self._state = "error"
            raise Unavailable(f"{what}: portal not reachable ({type(e).__name__})") from None
        if r.status_code >= 500:
            self._state = "error"
            raise Unavailable(f"{what}: portal error {r.status_code}")
        self._state, self._ready_at = "ready", time.monotonic()
        if r.status_code in (401, 403):
            raise Unavailable(f"{what}: portal refused the credentials ({r.status_code})")
        if r.status_code >= 400:
            raise SafeStop(f"{what}: unexpected answer {r.status_code}", detail={"status": r.status_code})
        try:
            return r.json()
        except ValueError:
            raise SafeStop(f"{what}: not JSON") from None

    def requirements(self, scheme_id: str) -> dict[str, Any]:
        """Always fresh (no cache): the drift check runs right before each pre-fill."""
        return self._request("GET", f"/agent/v1/schemes/{scheme_id}/requirements/",
                             headers={"X-Agent-API-Key": config.MOCK_PORTAL_AGENT_KEY or ""},
                             what="requirements")

    def my_applications(self, citizen_token: str) -> list[dict[str, Any]]:
        data = self._request("GET", "/applications/mine/",
                             headers={"Authorization": f"Token {citizen_token}"}, what="my applications")
        return data.get("results", data) if isinstance(data, dict) else data

    def delegate(self, citizen_token: str) -> Delegation:
        data = self._request("POST", "/auth/agent-delegation/",
                             headers={"Authorization": f"Token {citizen_token}"},
                             json={"duration_hours": DELEGATION_HOURS, "scopes": DELEGATION_SCOPES},
                             what="delegation")
        token = data.get("delegation_token") if isinstance(data, dict) else None
        if not token or not str(token).startswith("yjs_del_"):
            raise SafeStop("delegation: no delegation token in the answer")
        return Delegation(token=token, expires_at=data.get("expires_at"),
                          scopes=list(data.get("scopes") or DELEGATION_SCOPES))
