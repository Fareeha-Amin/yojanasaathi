"""GET /cases/{case_id}/summary: what the web app's screens show, from the case memory.

Deterministic, no LLM: the same rules, templates and checklist mapper the graph uses, so
the screens and the spoken replies always agree. Rendered in the case's language with the
English text alongside (`*_en`), because the UI shows Kannada / Hindi first with English
underneath. A reload or a second device sees the same screens (the `ui` payload of /turn
is only this turn's).

Labels on screen are display labels (`field_*`: "Annual income"), not the lowercase ones the
spoken sentences use. Never in here: document contents (metadata only, Aadhaar as last 4),
tokens, message text. Phase 4 fills the rest of `review.form_fields[*].value` and
`progress`; Phase 6 adds status checks to `applications[*].timeline` and `checked_at`.
"""

from typing import Any

from agent import config, rules
from agent.facts import FIELDS
from agent.graph import (FIELD_ORDER, MISSING_DOCS, _checklist, _ordered, _reask, _screen, read_back,
                         why_asking)
from agent.replies import day, strings, t, value
from agent.vault import public

DOC_ORDER = {"missing": 0, "needed": 1, "have": 2, "uploaded": 3}
# Audit actions shown on a scheme's timeline (My applications), oldest first.
TIMELINE = ("eligibility_decided", "confirm_requested", "fields_edited", "citizen_approved",
            "submitted", "status_changed")


def label(field: str, lang: str) -> str:
    """Display label for a screen ("Annual income"); falls back to the sentence label."""
    key = f"field_{field}"
    return t(key, lang) if key in strings(lang if lang in ("kn", "hi", "en") else "en") else t(f"label_{field}", lang)


def _missing(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in items if i["status"] in MISSING_DOCS]


def _two(lang: str, fn) -> tuple[Any, Any]:
    """(fn(lang), fn("en"))"""
    return fn(lang), fn("en") if lang != "en" else fn(lang)


def _schemes(state: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    ordered = _ordered(state)
    local = _screen(state, ordered, lang)["schemes"]
    en = _screen(state, ordered, "en")["schemes"] if lang != "en" else local
    for a, b in zip(local, en):
        a["title_en"] = b["title"]
        a["reasons_en"] = b["reasons"]
        a["effective_date_text_en"] = b["effective_date_text"]
        scheme = rules.load_schemes()[a["scheme_id"]]
        a["category"] = t(f"topic_{scheme['topic']}", lang).capitalize()  # Pension / ಪಿಂಚಣಿ
        a["category_en"] = scheme.get("category") or scheme["topic"].capitalize()
        a["missing_fields"] = [{"field": f, "label": label(f, lang), "label_en": label(f, "en")}
                               for f in a["missing_fields"]]
        for c in a["clauses"]:  # WHY box: rule vs the citizen's value
            c["label"], c["label_en"] = label(c["field"], lang), label(c["field"], "en")
        for d, e in zip(a["documents"], b["documents"]):
            d["label_en"] = e["label"]
    return local


def _profile(state: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    profile = state.get("profile") or {}
    sources = state.get("sources") or {}
    unsure = set(state.get("readback") or [])
    return [{"field": f, "label": label(f, lang), "label_en": label(f, "en"), "value": profile[f],
             "text": value(f, profile[f], lang), "text_en": value(f, profile[f], "en"),
             "source": sources.get(f), "unsure": f in unsure}
            for f in FIELDS if profile.get(f) is not None]


def _asking(state: dict[str, Any], lang: str) -> dict[str, Any] | None:
    asking = state.get("asking")
    if asking in FIELD_ORDER:
        q, q_en = _two(lang, lambda l: t(f"ask_{asking}", l))
        why, why_en = _two(lang, lambda l: why_asking(state, l))
        return {"kind": "field", "field": asking, "question": q, "question_en": q_en,
                "why": why, "why_en": why_en}
    if asking in ("choose", "proceed", "others"):
        q, q_en = _two(lang, lambda l: _reask(state, l))
        return {"kind": asking, "question": q, "question_en": q_en, "why": None, "why_en": None}
    return None


def _doc_items(scheme: dict[str, Any], state: dict[str, Any], lang: str,
               docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    local = _checklist(scheme, state, lang)
    en = _checklist(scheme, state, "en")
    return [{**a, "label_en": b["label"], "document": docs.get(a["doc"])} for a, b in zip(local, en)]


def _union(sids: list[str], state: dict[str, Any], lang: str, docs: dict[str, dict[str, Any]],
           skip: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Every document these schemes need (once each, naming the schemes), missing first."""
    schemes = rules.load_schemes()
    items: dict[str, dict[str, Any]] = {}
    for sid in sids:
        title, title_en = rules.title(schemes[sid], lang), rules.title(schemes[sid], "en")
        for item in _doc_items(schemes[sid], state, lang, docs):
            if item["doc"] in skip:
                continue
            got = items.setdefault(item["doc"], {**item, "schemes": [], "schemes_en": []})
            got["schemes"].append(title)
            got["schemes_en"].append(title_en)
    return sorted(items.values(), key=lambda i: DOC_ORDER.get(i["status"], 9))


def _checklist_screen(state: dict[str, Any], lang: str, docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The Documents screen. A scheme chosen (and not applied yet): its documents, titled
    "For <scheme>", the other qualifying schemes' extra documents in `others` (collapsed).
    Otherwise every document the qualifying schemes need. Missing first."""
    schemes = rules.load_schemes()
    applications = state.get("applications") or {}
    profile = state.get("profile") or {}
    # the rules now, like the Schemes screen (also mid-interview: age alone decides some)
    eligible = [s["scheme_id"] for s in _ordered(state) if rules.status(s, profile) == "eligible"]
    open_ = [s for s in eligible if s not in applications]
    sel = state.get("selected")
    if sel and sel not in applications:
        items = _union([sel], state, lang, docs)
        others = _union([s for s in open_ if s != sel], state, lang, docs, {i["doc"] for i in items})
        title, title_en = rules.title(schemes[sel], lang), rules.title(schemes[sel], "en")
        sids = [sel]
    else:
        sids = open_ or eligible
        items, others, title, title_en = _union(sids, state, lang, docs), [], None, None
    return {"scheme_ids": sids, "title": title, "title_en": title_en, "items": items, "others": others,
            "ready": sum(i["status"] in ("uploaded", "have") for i in items), "total": len(items),
            "missing": len(_missing(items))}


def _review(state: dict[str, Any], pause: dict[str, Any] | None, lang: str,
            docs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not pause or pause.get("type") != "confirm":
        return None
    preview = pause.get("preview") or {}
    sid = preview.get("scheme_id")
    scheme = rules.load_schemes().get(sid)
    if scheme is None:
        return None
    fields = preview.get("fields") or {}
    unsure = list(preview.get("needs_readback") or [])
    documents = _doc_items(scheme, state, lang, docs)  # now, so an upload counts at once
    missing = _missing(documents)
    title, title_en = _two(lang, lambda l: rules.title(scheme, l))
    text, text_en = _two(lang, lambda l: read_back(scheme, fields, unsure, l, len(missing)))
    profile = state.get("profile") or {}
    return {
        "scheme_id": sid, "title": title, "title_en": title_en,
        "readback": text, "readback_en": text_en,
        "fields": [{"field": f, "label": label(f, lang), "label_en": label(f, "en"), "value": v,
                    "text": value(f, v, lang), "text_en": value(f, v, "en"),
                    "unsure": f in unsure, "editable": f in FIELD_ORDER} for f, v in fields.items()],
        # The portal's own application form (labels from the seed). A field the citizen
        # already answered (annual_income) is pre-filled "from your answers"; the rest come
        # from the browser agent's pre-fill in Phase 4 (null until then: placeholder).
        "form_fields": [_form_field(f, profile, lang) for f in scheme.get("application_fields", [])],
        "screenshots": [],  # Phase 4: one per browser step
        "documents": documents,
        "documents_missing": len(missing),
        "source_url": scheme["source_url"], "effective_date": scheme["effective_date"],
        "effective_date_text": day(scheme["effective_date"], lang),
        "effective_date_text_en": day(scheme["effective_date"], "en"),
    }


def _form_field(f: dict[str, Any], profile: dict[str, Any], lang: str) -> dict[str, Any]:
    v = profile.get(f["name"])
    return {"name": f["name"], "type": f["type"], "required": f["required"],
            "label": f["label"].get(lang) or f["label"]["en"], "label_en": f["label"]["en"],
            "value": v, "text": None if v is None else value(f["name"], v, lang),
            "from_answers": v is not None}


def _timeline(sid: str, audit_rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_eligible = False
    for row in audit_rows:
        action, detail = row["action"], row.get("detail") or {}
        if action == "eligibility_decided":
            if seen_eligible or sid not in (detail.get("eligible") or []):
                continue
            seen_eligible = True
        elif action not in TIMELINE or row.get("scheme_id") != sid:
            continue
        item = {"at": row["at"], "kind": action}
        if action == "submitted":
            item["app_id"] = detail.get("app_id")
        out.append(item)
    for ev in events:  # Phase 6: status changes polled from the portal
        if ev["kind"] == "status_changed" and ev.get("scheme_id") == sid:
            out.append({"at": ev["at"], "kind": "status_changed", "status": (ev.get("detail") or {}).get("status")})
    return sorted(out, key=lambda i: i["at"])


def _applications(state: dict[str, Any], lang: str, data: dict[str, Any] | None,
                  docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    schemes = rules.load_schemes()
    audit_rows = (data or {}).get("audit") or []
    events = (data or {}).get("events") or []
    out = []
    for sid, a in (state.get("applications") or {}).items():
        code = a.get("status") or "SUBMITTED"
        out.append({
            "scheme_id": sid, "title": rules.title(schemes[sid], lang),
            "title_en": rules.title(schemes[sid], "en"), "app_id": a.get("app_id"), "status": code,
            "status_text": t(f"badge_{code}", lang), "status_text_en": t(f"badge_{code}", "en"),
            "timeline": _timeline(sid, audit_rows, events),
            # "What to do": documents of this scheme not provided yet
            "missing_documents": [{"doc": i["doc"], "label": i["label"], "label_en": i["label_en"]}
                                  for i in _missing(_doc_items(schemes[sid], state, lang, docs))],
            "checked_at": None,  # Phase 6: last time the portal's status was polled
        })
    return out


def build(case_id: str, state: dict[str, Any], pause: dict[str, Any] | None,
          documents: list[dict[str, Any]], consent: dict[str, Any], data: dict[str, Any] | None,
          lang: str | None = None) -> dict[str, Any]:
    """The summary for one case. `state` = graph state values, `documents` = metadata rows,
    `data` = store.case_data() (events + audit) or None for a case with no turns yet."""
    lang = lang or state.get("lang") or "en"
    stored = {d["doc_type"]: public(d) for d in documents}
    state = {**state, "docs_stored": sorted(stored)}  # current vault, not the last turn's
    schemes = rules.load_schemes()
    applications = state.get("applications") or {}
    offered = [s for s in state.get("offered") or [] if s not in applications]
    reply = state.get("reply")
    return {
        "case_id": case_id,
        "lang": lang,
        "case_lang": state.get("lang"),
        "status": state.get("status"),
        "selected": state.get("selected"),
        "profile": _profile(state, lang),
        "asking": _asking(state, lang),
        "schemes": _schemes(state, lang),
        "checklist": _checklist_screen(state, lang, stored),
        "documents": list(stored.values()),
        "consent": consent,
        "pause": pause,
        "review": _review(state, pause, lang, stored),
        "progress": None,  # Phase 4: browser-agent steps (pre-fill screen)
        "applications": _applications(state, lang, data, stored),
        "next": [{"scheme_id": s, "title": rules.title(schemes[s], lang),
                  "title_en": rules.title(schemes[s], "en")} for s in offered],
        "last_reply": {"text": reply, "en": state.get("subtitle")} if reply else None,
        "limits": {"doc_retention_hours": config.DOC_RETENTION_HOURS,
                   "doc_max_bytes": config.DOC_MAX_BYTES},
    }
