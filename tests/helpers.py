"""Shared test helpers (not conftest.py: importing that twice would recreate the test DB)."""

import re

from fastapi.testclient import TestClient

_CASE_PATH = re.compile(r"^/cases/([^/?]+)")


def bearer(case_id: str) -> dict[str, str]:
    """The Authorization header the web app sends for this case (agent.main sets the key)."""
    from agent import auth

    return {"Authorization": f"Bearer {auth.issue(case_id)[0]}"}


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
