"""GET /cases/{case_id}/summary: what the web app's screens show, from the case memory.

Deterministic, no LLM: the same rules, templates and checklist mapper the graph uses, so
the screens and the spoken replies always agree. Rendered in the case's language with the
English text alongside (`*_en`), because the UI shows Kannada / Hindi first with English
underneath. A reload or a second device sees the same screens (the `ui` payload of /turn
is only this turn's).

Labels on screen are display labels (`field_*`: "Annual income"), not the lowercase ones the
spoken sentences use. Never in here: document contents (metadata only, Aadhaar as last 4),
tokens, message text, the full date of birth / mobile / account number / IFSC (masked).
Phase 4: `progress` (browser steps, live while a turn is filling the form), the portal's
form on Review with the values the page showed, screenshot IDs (GET
/cases/{id}/screenshots/{shot_id}), the declaration. Phase 6 adds status checks to
`applications[*].timeline` and `checked_at`.
"""

from typing import Any

from agent import config, rules
from agent.facts import FIELDS
from agent.graph import (FIELD_ORDER, MISSING_DOCS, _checklist, _ordered, _reask, _screen, read_back,
                         why_asking)
from agent.portal import edge, progress
from agent.portal import fields as form_fields
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
        a["missing_fields"] = [{"field": f, "label": label(f, lang), "label_en": label(f, "en"),
                                "question": t(f"ask_{f}", lang), "question_en": t(f"ask_{f}", "en")}
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
        return {"kind": "field", "field": asking, "label": label(asking, lang), "label_en": label(asking, "en"),
                "question": q, "question_en": q_en, "why": why, "why_en": why_en}
    if asking in ("choose", "proceed", "others"):
        q, q_en = _two(lang, lambda l: _reask(state, l))
        return {"kind": asking, "question": q, "question_en": q_en, "why": None, "why_en": None}
    if asking and asking.startswith("form:") and state.get("selected"):
        f = asking[5:]
        scheme = rules.load_schemes()[state["selected"]]
        q, q_en = _two(lang, lambda l: _reask(state, l))
        return {"kind": "form", "field": f, "label": form_fields.label(scheme, f, lang),
                "label_en": form_fields.label(scheme, f, "en"), "sensitive": f in form_fields.SENSITIVE,
                "question": q, "question_en": q_en, "why": None, "why_en": None}
    return None


def _progress(case_id: str, state: dict[str, Any], lang: str) -> dict[str, Any] | None:
    """The Pre-fill screen: live while the browser works, else the last turn's steps."""
    steps = progress.get(case_id) or state.get("progress")
    if not steps:
        return None
    return {"scheme_id": state.get("selected"),
            "steps": [{"key": s["key"], "label": t(f"step_{s['key']}", lang), "label_en": t(f"step_{s['key']}", "en"),
                       "status": s["status"], "screenshot": s.get("screenshot")} for s in steps]}


def _form(state: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    """The application-form answers so far (sensitive ones masked), for the screens."""
    scheme = rules.load_schemes().get(state.get("selected") or "")
    shown = state.get("form_shown") or {}
    return [{"field": f, "label": form_fields.label(scheme, f, lang), "label_en": form_fields.label(scheme, f, "en"),
             "text": text, "sensitive": f in form_fields.SENSITIVE} for f, text in shown.items()]


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


def _review(case_id: str, state: dict[str, Any], pause: dict[str, Any] | None, lang: str,
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
    if preview.get("readback"):  # the portal read-back: name, date of birth (masked here), ...
        text = edge.expand(case_id, state, preview["readback"], lang, masked=True)
        text_en = edge.expand(case_id, state, preview.get("readback_en"), "en", masked=True)
    else:
        text, text_en = _two(lang, lambda l: read_back(scheme, fields, unsure, l, len(missing)))
    profile = state.get("profile") or {}
    page = {f["field"]: f for f in preview.get("form") or []}
    return {
        "scheme_id": sid, "title": title, "title_en": title_en,
        "readback": text, "readback_en": text_en,
        "fields": [{"field": f, "label": label(f, lang), "label_en": label(f, "en"), "value": v,
                    "text": value(f, v, lang), "text_en": value(f, v, "en"),
                    "unsure": f in unsure, "editable": f in FIELD_ORDER} for f, v in fields.items()],
        # The portal's own application form (labels from the seed), with the values the
        # browser agent read back from the page (sensitive ones masked). Before the page is
        # filled, a field the citizen already answered (annual_income) is "from your answers".
        "form_fields": [_form_field(f, profile, lang, page.get(f["name"]), scheme)
                        for f in scheme.get("application_fields", [])],
        "screenshots": list(preview.get("screenshots") or []),  # GET /cases/{id}/screenshots/{shot_id}
        "declaration": {"text": form_fields.label(scheme, "declaration_consent", lang),
                        "text_en": form_fields.label(scheme, "declaration_consent", "en")}
        if preview.get("declaration") else None,
        "delegation": preview.get("delegation"),
        "documents": documents,
        "documents_missing": len(missing),
        "source_url": scheme["source_url"], "effective_date": scheme["effective_date"],
        "effective_date_text": day(scheme["effective_date"], lang),
        "effective_date_text_en": day(scheme["effective_date"], "en"),
    }


def _form_field(f: dict[str, Any], profile: dict[str, Any], lang: str,
                page: dict[str, Any] | None = None, scheme: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {"name": f["name"], "type": f["type"], "required": f["required"],
            "label": f["label"].get(lang) or f["label"]["en"], "label_en": f["label"]["en"]}
    if page is not None:  # what the portal page shows (filled by the browser agent)
        if page.get("sensitive") or page.get("value") is None:  # masked text only
            text, text_en = page.get("text"), page.get("text_en")
        else:
            text = form_fields.shown(scheme, f["name"], page["value"], lang)
            text_en = form_fields.shown(scheme, f["name"], page["value"], "en")
        return {**base, "value": text, "text": text, "text_en": text_en,
                "from_answers": True, "from_page": True, "sensitive": bool(page.get("sensitive"))}
    v = profile.get(f["name"])
    return {**base, "value": v, "text": None if v is None else value(f["name"], v, lang),
            "from_answers": v is not None, "from_page": False, "sensitive": f["name"] in form_fields.SENSITIVE}


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
        "review": _review(case_id, state, pause, lang, stored),
        "progress": _progress(case_id, state, lang),  # browser-agent steps (Pre-fill screen)
        "form": _form(state, lang),
        "applications": _applications(state, lang, data, stored),
        "next": [{"scheme_id": s, "title": rules.title(schemes[s], lang),
                  "title_en": rules.title(schemes[s], "en")} for s in offered],
        "last_reply": {"text": edge.expand(case_id, state, reply, lang, masked=True),
                       "en": edge.expand(case_id, state, state.get("subtitle"), "en", masked=True)} if reply else None,
        "limits": {"doc_retention_hours": config.DOC_RETENTION_HOURS,
                   "doc_max_bytes": config.DOC_MAX_BYTES},
    }
