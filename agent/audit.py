"""log_event(): the one helper for every consequential action -> audit_log (append-only).

Graph nodes call it without a case ID: it is the LangGraph thread ID of the running turn.
Details are scrubbed (agent/privacy.py) and must hold no personal values: field names,
scheme and application IDs, decisions, and Aadhaar as last 4 digits only. The audit log
outlives "delete my data", so it must never hold anything that deletion should remove.
If the write fails, the action fails with it (no unaudited submissions).
"""

import logging
from typing import Any

from langgraph.config import get_config

log = logging.getLogger("yojanasaathi.audit")

_store = None  # agent.db.Store, set at startup (agent/main.py)


def set_store(store) -> None:
    global _store
    _store = store


def _thread_id() -> str | None:
    try:
        return get_config()["configurable"].get("thread_id")
    except (RuntimeError, KeyError):
        return None  # called outside a graph run


def log_event(actor: str, action: str, *, case_id: str | None = None, scheme_id: str | None = None,
              detail: dict[str, Any] | None = None) -> None:
    case_id = case_id or _thread_id()
    if _store is None:  # only a node unit-tested on its own, without the app
        log.warning("audit (no store): %s %s case=%s scheme=%s", actor, action, case_id, scheme_id)
        return
    _store.log_event(actor, action, case_id, scheme_id, detail)
