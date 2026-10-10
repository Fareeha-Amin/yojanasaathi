"""Phase 6: status tracking and follow-up after a submission.

  api.py       the portal's agent API (read-only citizen delegation) over httpx
  store.py     Postgres: delegation token (sealed), tracked applications, updates, push subs
  poller.py    APScheduler job + check_case(): one list call per citizen, changes -> updates
  followup.py  compose(): the 1-2 sentence update in kn / hi / en (templates); Phase 7 reuses it
  push.py      web push (pywebpush, VAPID), backend only
  nodes.py     graph nodes: renew delegation (OTP login) and the document correction flow

State lives in Postgres, so polling, unread updates and the paused / expired state survive a
restart. Nothing here logs a token, a full number or a portal remark.
"""

from typing import Any

from agent.tracking.store import TrackStore

_store: TrackStore | None = None
_poller: Any = None  # agent.tracking.poller.Poller


class TrackingError(Exception):
    pass


class TokenExpired(TrackingError):
    """403 DELEGATION_TOKEN_INVALID: the 24-hour delegation ended or was revoked."""


class Throttled(TrackingError):
    def __init__(self, retry_after: float | None = None):
        super().__init__("portal rate limit (429)")
        self.retry_after = retry_after


class TrackUnavailable(TrackingError):
    """Timeout, network error, 5xx, refused agent key, unexpected answer."""


def set_store(store: TrackStore | None) -> None:
    global _store
    _store = store


def get_store() -> TrackStore:
    if _store is None:
        raise TrackingError("tracking is not set up (agent.main sets it at startup)")
    return _store


def set_poller(poller: Any) -> None:
    global _poller
    _poller = poller


def get_poller() -> Any:
    if _poller is None:
        raise TrackingError("tracking is not set up (agent.main sets it at startup)")
    return _poller
