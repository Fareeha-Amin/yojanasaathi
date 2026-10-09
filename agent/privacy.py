"""Aadhaar is shown and logged as its last 4 digits only.

mask_aadhaar() runs on every /turn message BEFORE the graph sees it (replaced by HIDDEN;
the last 4 go to the audit log), so the full number never reaches the checkpoint (case
memory), the LLM, the logs or the audit log. It
over-masks on purpose: any run of 12 digits (4-4-4 with optional space / hyphen, or
contiguous; ASCII, Kannada or Devanagari digits) is treated as an Aadhaar number. No
field this agent needs is 12 digits long (income has commas or words, phone numbers are 10).
Not covered: an Aadhaar number spoken as separate number WORDS ("ಒಂದು ಎರಡು ...") that the
STT does not turn into digits. The agent never asks for the number.

scrub() is the same guard for anything written to the audit log.
"""

import re
from typing import Any

# "+91" + 10 digits is a phone number, not an Aadhaar number: not matched.
AADHAAR_RE = re.compile(r"(?<![\d+])(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})(?!\d)")


def last4(number: str) -> str:
    digits = [c for c in number if c.isdigit()]
    return "".join(digits[-4:])


def masked(last_four: str) -> str:
    return f"XXXX XXXX {last_four}"


# What the graph / LLM sees instead of the number: no digits at all, so the number parsers
# (agent/numbers.py) can never read the last 4 as an age or an income.
HIDDEN = "[Aadhaar number hidden]"


def mask_aadhaar(text: str, placeholder: str | None = None) -> tuple[str, list[str]]:
    """(text with every Aadhaar-like number masked as "XXXX XXXX 1234", or replaced by
    `placeholder`; the last 4 digits of each)."""
    found: list[str] = []

    def repl(m: re.Match) -> str:
        found.append(m.group(3))
        return placeholder or masked(m.group(3))

    return AADHAAR_RE.sub(repl, text), found


def scrub(obj: Any) -> Any:
    """A JSON-safe copy with Aadhaar-like numbers masked; raw bytes are never kept."""
    if isinstance(obj, str):
        return mask_aadhaar(obj)[0]
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return "<bytes withheld>"
    if isinstance(obj, dict):
        return {str(k): scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [scrub(v) for v in obj]
    if isinstance(obj, int) and not isinstance(obj, bool) and len(str(abs(obj))) == 12:
        return masked(str(abs(obj))[-4:])
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return mask_aadhaar(str(obj))[0]
