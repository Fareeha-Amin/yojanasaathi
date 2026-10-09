"""The edge of /turn: what must never reach the graph (and so the checkpoint) as text.

prepare(): runs on the raw message, before the graph, the LLM or any log sees it.
- OTP pause: the 6-digit code goes into an in-memory inbox for this case (the otp node
  takes it out and types it into the portal); the graph only gets "[code given]". The
  code is never in the checkpoint, the audit log or a log line.
- A sensitive form answer (date of birth, mobile, account number, IFSC; agent/portal/
  fields.SENSITIVE), when the agent asked for it or a confirm-time correction names it:
  parsed here, sealed (agent/sealed.py), passed on as `form_input` = ciphertext + masked
  text; the graph gets "[bank_account_number given]".
- Not readable: long digit runs are hidden before the graph sees the message.
expand(): the read-back markers in a reply ("I heard ⟦bank_account_number⟧") become the
spoken digits only in the /turn response, so the checkpoint keeps the marker. Screens
(summary) expand them masked.
"""

import re
import threading
from typing import Any

from agent import rules, sealed
from agent.portal import fields

MARKER = re.compile(r"⟦(\w+)⟧")
LONG_DIGITS = re.compile(r"(?:\d[\s\-]?){6,}")  # 6+ digits, maybe spaced: OTP, account, mobile
RESEND = ("send again", "resend", "new code", "another code", "ಮತ್ತೆ ಕಳುಹಿಸಿ", "ಹೊಸ ಕೋಡ್", "ಮತ್ತೊಮ್ಮೆ ಕಳುಹಿಸಿ",
          "फिर से भेजो", "दोबारा भेजो", "नया कोड", "फिर से भेजें", "दोबारा भेजें")

_inbox: dict[str, str] = {}
_lock = threading.Lock()


def marker(field: str) -> str:
    return f"⟦{field}⟧"


def hide_digits(text: str) -> str:
    return LONG_DIGITS.sub("[number hidden] ", text).strip()


def take_otp(case_id: str) -> str | None:
    with _lock:
        return _inbox.pop(case_id, None)


def _put_otp(case_id: str, code: str) -> None:
    with _lock:
        _inbox[case_id] = code


def wants_resend(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in RESEND)


def form_input(case_id: str, field: str, value: Any, scheme: dict[str, Any] | None, lang: str) -> dict[str, Any]:
    """What the graph stores for one answer: sealed value + display text (masked)."""
    if isinstance(value, bool):
        return {"field": field, "value": value}
    return {"field": field, "sealed": sealed.seal(case_id, field, str(value)),
            "shown": fields.shown(scheme, field, value, lang)}


def prepare(case_id: str, values: dict[str, Any], pause: dict[str, Any] | None,
            text: str) -> tuple[str, dict[str, Any]]:
    """(text for the graph, extra state update)."""
    kind = (pause or {}).get("type")
    if kind == "otp":
        code = fields.parse_otp(text)
        if code:
            _put_otp(case_id, code)
            return "[code given]", {}
        return hide_digits(text), {}
    sid = values.get("selected")
    scheme = rules.load_schemes().get(sid) if sid else None
    lang = values.get("lang") or "en"
    asking = values.get("asking") or ""
    field = None
    if asking.startswith("form:") and pause is None:
        field = asking[5:]
    elif kind == "confirm" and scheme is not None:
        named = fields.mentioned(text, [*fields.form_fields(scheme), "mobile"])
        field = named if named in fields.SENSITIVE else None
    if field in fields.SENSITIVE:
        ans = fields.parse(field, text, scheme)
        if ans is not None:
            return f"[{field} given]", {"form_input": form_input(case_id, field, ans.value, scheme, lang)}
        return hide_digits(text), {}
    return text, {}


def _open(case_id: str, values: dict[str, Any], field: str) -> str | None:
    s = (values.get("form") or {}).get(field)
    if not s:
        return None
    try:
        return sealed.open_(case_id, field, s)
    except sealed.SealedError:
        return None


def expand(case_id: str, values: dict[str, Any], text: str | None, lang: str, *, masked: bool = False) -> str | None:
    """Replace ⟦field⟧ with the value: spoken in full for the /turn reply, masked for screens."""
    if not text or "⟦" not in text:
        return text

    def repl(m: re.Match) -> str:
        field = m.group(1)
        v = _open(case_id, values, field)
        if v is None:
            return "…"
        return fields.masked(field, v) if masked else fields.spoken(field, v, lang)

    return MARKER.sub(repl, text)
