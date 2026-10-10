"""The portal's agent API for a citizen (docs: backend/docs/YOJANASAATHI_AGENT_INTEGRATION.md
in the portal repo). Headers X-Agent-API-Key + X-Citizen-Delegation-Token; scopes
applications:read and notifications:read; read-only by construction (GET only).

    GET {MOCK_PORTAL_API}/agent/v1/citizen/applications/                  -> {count, results[]}
    GET .../applications/{no}/history/        -> {current_status, lifecycle_events[], ...}
    GET .../applications/{no}/documents/      -> {results[{document_type, verification_status, remarks}]}
    GET .../notifications/

Errors are {error, code}: 403 DELEGATION_TOKEN_INVALID = expired (TokenExpired), 429
(Throttled, with Retry-After when given), timeouts / 5xx / 401 / anything else
(TrackUnavailable). Response bodies and tokens are never logged.
"""

from typing import Any

import httpx

from agent import config
from agent.tracking import Throttled, TokenExpired, TrackUnavailable

PREFIX = "/agent/v1/citizen"


class CitizenAPI:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client  # tests: httpx.Client(transport=httpx.MockTransport(...))

    def _get(self, path: str, token: str, timeout: float | None, what: str) -> Any:
        if not config.MOCK_PORTAL_API or not config.MOCK_PORTAL_AGENT_KEY:
            raise TrackUnavailable("portal settings missing (MOCK_PORTAL_API / MOCK_PORTAL_AGENT_KEY)")
        headers = {"X-Agent-API-Key": config.MOCK_PORTAL_AGENT_KEY, "X-Citizen-Delegation-Token": token}
        url = f"{config.MOCK_PORTAL_API}{PREFIX}{path}"
        try:
            if self._client is not None:
                r = self._client.get(url, headers=headers, timeout=timeout or config.TRACK_TIMEOUT)
            else:
                r = httpx.get(url, headers=headers, timeout=timeout or config.TRACK_TIMEOUT)
        except httpx.HTTPError as e:
            raise TrackUnavailable(f"{what}: portal not reachable ({type(e).__name__})") from None
        if r.status_code == 429:
            raise Throttled(_retry_after(r))
        if r.status_code >= 500:
            raise TrackUnavailable(f"{what}: portal error {r.status_code}")
        code = _error_code(r)
        if r.status_code == 403 and code == "DELEGATION_TOKEN_INVALID":
            raise TokenExpired("delegation expired or revoked")
        if r.status_code == 404 and code == "APPLICATION_NOT_FOUND":
            return None
        if r.status_code >= 400:
            raise TrackUnavailable(f"{what}: refused ({r.status_code} {code or ''})".strip())
        try:
            return r.json()
        except ValueError:
            raise TrackUnavailable(f"{what}: not JSON") from None

    def applications(self, token: str, timeout: float | None = None) -> list[dict[str, Any]]:
        data = self._get("/applications/", token, timeout, "applications")
        results = data.get("results", data) if isinstance(data, dict) else data
        return [a for a in results or [] if isinstance(a, dict)]

    def history(self, token: str, app_no: str, timeout: float | None = None) -> dict[str, Any] | None:
        return self._get(f"/applications/{app_no}/history/", token, timeout, "history")

    def documents(self, token: str, app_no: str, timeout: float | None = None) -> list[dict[str, Any]]:
        data = self._get(f"/applications/{app_no}/documents/", token, timeout, "documents")
        return [d for d in (data or {}).get("results", []) if isinstance(d, dict)]

    def notifications(self, token: str, timeout: float | None = None) -> list[dict[str, Any]]:
        data = self._get("/notifications/", token, timeout, "notifications")
        results = data.get("results", data) if isinstance(data, dict) else data
        return [n for n in results or [] if isinstance(n, dict)]


def _error_code(r: httpx.Response) -> str | None:
    try:
        body = r.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return max(0.0, float(r.headers.get("Retry-After", "")))
    except ValueError:
        return None
