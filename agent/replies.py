"""Key replies come from reviewed templates, never from the LLM.

Text lives in agent/i18n/{en,kn,hi}.json (same keys in every file; tests/test_replies.py
checks keys and {placeholders}). Kannada and Hindi are marked "needs native-speaker
review" until a native speaker signs them off. This module only formats values into them:
Indian money grouping (₹1,20,000), localised dates, field labels, and rule reasons.
"""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.districts import local_name
from agent.rules import Clause

I18N_DIR = Path(__file__).resolve().parent / "i18n"
LANGS = ("en", "kn", "hi")
MONEY_FIELDS = {"annual_income"}


@lru_cache(maxsize=None)
def strings(lang: str) -> dict[str, str]:
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def _lang(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def t(key: str, lang: str | None, **kw: Any) -> str:
    """Render template `key` in `lang` (falls back to English for an unknown lang)."""
    return strings(_lang(lang))[key].format(**kw)


def money(amount: int | float) -> str:
    """Indian digit grouping: 120000 -> ₹1,20,000."""
    n = int(round(amount))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


def day(iso: str, lang: str | None) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {t(f'month_{d.month}', lang)} {d.year}"


def label(field: str, lang: str | None) -> str:
    return t(f"label_{field}", lang)


def value(field: str, v: Any, lang: str | None) -> str:
    if isinstance(v, bool):
        return t("value_yes" if v else "value_no", lang)
    if field in MONEY_FIELDS and isinstance(v, (int, float)):
        return money(v)
    if field == "gender" and v in ("female", "male"):
        return t(f"gender_{v}", lang)
    if field == "category" and v in ("SC", "ST", "OBC", "General"):
        return t(f"category_{v}", lang)
    if field == "district" and isinstance(v, str):
        return local_name(v, _lang(lang))
    return str(v)


def join(items: list[str], lang: str | None) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + t("join_and", lang) + items[-1]


def reason(c: Clause, lang: str | None) -> str:
    f, v = label(c.field, lang), value(c.field, c.value, lang)
    if c.op in (">=", "<=", ">", "<"):
        key = {">=": "reason_min", "<=": "reason_max", ">": "reason_above", "<": "reason_below"}[c.op]
        return t(key, lang, field=f, value=v, limit=value(c.field, c.limit, lang))
    if c.op == "in":
        return t("reason_in" if c.result else "reason_not_in", lang, field=f, value=v)
    if c.result is False and c.op == "==":
        return t("reason_not", lang, field=f, value=v, limit=value(c.field, c.limit, lang))
    return t("reason_is", lang, field=f, value=v)


def reasons(cs: list[Clause], lang: str | None) -> str:
    return join([reason(c, lang) for c in cs], lang)


def doc(doc_id: str, lang: str | None) -> str:
    key = f"doc_{doc_id}"
    return t(key, lang) if key in strings(_lang(lang)) else doc_id.replace("_", " ")


def readback(fields: dict[str, Any], lang: str | None) -> str:
    return ", ".join(f"{label(k, lang)} {value(k, v, lang)}" for k, v in fields.items())
