"""The follow-up agent's one function: compose() writes the 1-2 sentence update the citizen
hears (kn / hi / en, reviewed templates in agent/i18n): what changed + the one next action.

Used by the next turn's spoken update (agent/main.py), the push notification
(agent/tracking/poller.py), the "what's my status?" answer and the summary's "What to do".
Phase 7 (phone callback) calls the same function. No LLM: a status code decides, a template
speaks. The portal's own words (remark, reason) are quoted, never translated or rewritten.
"""

from typing import Any

from agent import rules
from agent.replies import day, t

REMARK_MAX = 160  # spoken: keep the portal's remark short


def _title(scheme_id: str | None, lang: str) -> str:
    scheme = rules.load_schemes().get(scheme_id or "")
    return rules.title(scheme, lang) if scheme else ""


def _doc_name(doc: dict[str, Any], scheme_id: str | None, lang: str) -> str:
    scheme = rules.load_schemes().get(scheme_id or "")
    if scheme is not None and doc.get("doc"):
        return rules.doc_label(scheme, doc["doc"], lang)
    return doc.get("type") or ""


def _clip(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= REMARK_MAX else text[: REMARK_MAX - 1].rstrip() + "…"


def docs_line(docs: list[dict[str, Any]], scheme_id: str | None, lang: str) -> str:
    """"Identity Proof: photo is blurry." for every document the portal flagged."""
    out = []
    for d in docs:
        name, remark = _doc_name(d, scheme_id, lang), _clip(d.get("remark"))
        out.append(t("flagged_doc", lang, doc=name, remark=remark.rstrip(".।")) if remark
                   else t("flagged_doc_plain", lang, doc=name))
    return " ".join(out)


def _flagged(data: dict[str, Any], scheme_id: str | None, lang: str) -> str:
    """What the portal asked for: the flagged documents, else its own note."""
    if data.get("docs"):
        return docs_line(data["docs"], scheme_id, lang)
    if data.get("reason"):
        return t("portal_note", lang, reason=_clip(data["reason"]).rstrip(".।"))
    return ""


def doc_names(docs: list[dict[str, Any]], scheme_id: str | None, lang: str) -> list[str]:
    return [n for n in (_doc_name(d, scheme_id, lang) for d in docs) if n]


def next_step(status: str, correction: dict[str, Any] | None, scheme_id: str | None, lang: str) -> str:
    """The one next action for a status ("What to do")."""
    data = correction or {}
    if status == "CORRECTION_REQUIRED":
        return t("next_CORRECTION_REQUIRED", lang, flagged=_flagged(data, scheme_id, lang)).replace("  ", " ").strip()
    if status == "REJECTED":
        return t("next_REJECTED", lang, flagged=_flagged(data, scheme_id, lang)).replace("  ", " ").strip()
    key = f"next_{status}"
    return t(key, lang) if key in _keys(lang) else t("next_other", lang)


def _keys(lang: str) -> set[str]:
    from agent.replies import strings

    return set(strings(lang if lang in ("kn", "hi") else "en"))


def _build(update: dict[str, Any], lang: str) -> str:
    kind, sid, data = update["kind"], update.get("scheme_id"), update.get("data") or {}
    title = _title(sid, lang)
    if kind == "delegation_expired":
        return t("upd_delegation_expired", lang)
    if kind == "document_problem":
        return t("upd_document_problem", lang, title=title, flagged=_flagged(data, sid, lang))
    status = data.get("status") or ""
    key = f"upd_{status}"
    if key not in _keys(lang):
        return t("upd_other", lang, title=title, status=t(f"status_{status}", lang)
                 if f"status_{status}" in _keys(lang) else status)
    if status == "CORRECTION_REQUIRED":
        return t(key, lang, title=title, flagged=_flagged(data, sid, lang)).replace("  ", " ")
    if status == "REJECTED":
        return t(key, lang, title=title, flagged=_flagged(data, sid, lang)).replace("  ", " ").strip()
    return t(key, lang, title=title, app_id=update.get("app_id") or "")


def compose(update: dict[str, Any], lang: str | None) -> tuple[str, str]:
    """(text in `lang`, English text) for one update row {kind, scheme_id, app_id, data}."""
    lang = lang if lang in ("kn", "hi", "en") else "en"
    text = _build(update, lang)
    return text, text if lang == "en" else _build(update, "en")


def status_line(app: dict[str, Any], lang: str, paused: bool = False) -> str:
    """"Senior Citizen Pension Scheme: under review, last updated 9 October 2026. ..." for the
    status question. `app` = a tracked_apps row (status, portal_updated_at, correction)."""
    sid, status = app["scheme_id"], app["status"]
    when = (app.get("portal_updated_at") or "")[:10]
    try:
        date = day(when, lang) if when else ""
    except ValueError:
        date = ""
    label = t(f"status_{status}", lang) if f"status_{status}" in _keys(lang) else status
    nxt = next_step(status, app.get("correction"), sid, lang)
    if paused:
        nxt = f"{nxt} {t('status_expired_note', lang)}".strip()
    key = "status_full" if date else "status_full_nodate"
    return t(key, lang, title=_title(sid, lang), status=label, date=date, next=nxt).strip()
