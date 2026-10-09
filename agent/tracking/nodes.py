"""Graph nodes for Phase 6 (agent/graph.py wires them in).

Renewing the read-only access (after the 24-hour delegation ended):
  router ("renew") -> renew (Phase 4 login: portal OTP SMS) -> otp (interrupt, the citizen says
  the code; state `flow` = "renew") -> renew_delegate (new read-only delegation, sealed, polling resumes)

Correcting an application the portal sent back (CORRECTION_REQUIRED):
  router ("correct": "resubmit", or yes / the scheme name once the corrected documents are
  uploaded) -> correct (are the documents there? login + OTP) -> otp (`flow` = "correct")
  -> correct_check (the portal still asks for the correction; the read-back) -> correct_confirm
  (interrupt: the human gate, explicit yes only) -> correct_submit (replace the documents on the
  portal, resubmit). Nothing is replaced or resubmitted before the yes, and never twice: the
  portal's own status and ours must both say CORRECTION_REQUIRED at that moment.
The portal has no correction screen to script (no data-testid), so the replace + resubmit use the
portal's own citizen API with the token of this case's OTP login: same login, same gate, same
audit; never the agent API (read-only by design).
Every step calls log_event() (field names / IDs only).
"""

import time
from typing import Any, Literal

from langgraph.types import Command, interrupt

from agent import rules, sealed
from agent.audit import log_event
from agent.gate import parse_decision
from agent.numbers import tokenize
from agent.portal import PortalError, SessionGone, Unavailable, fields, get_driver, progress
from agent.portal import nodes as portal
from agent.portal.api import DELEGATION_HOURS, DELEGATION_SCOPES
from agent.replies import join, t
from agent.tracking import get_store

END = "__end__"
_say, _lang, _case_id = portal._say, portal._lang, portal._case_id

CLEAR = {"flow": None, "correcting": None}


def _blocked(sid: str, app_id: str, where: str) -> None:
    log_event("agent", "resubmit_blocked", scheme_id=sid, detail={"app_id": app_id, "guard": where})


def _title(sid: str, lang: str) -> str:
    return rules.title(rules.load_schemes()[sid], lang)


def _login(state: dict[str, Any], flow: str) -> Command:
    """Open the portal login for this case's registered mobile and ask for the code."""
    lang = _lang(state)
    case_id = _case_id()
    mobile = portal._open(case_id, state, "mobile")
    if not mobile:
        key = "renew_no_mobile" if flow == "renew" else "correct_no_mobile"
        log_event("agent", f"{flow}_no_mobile")
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t(key, l))})
    try:
        result = get_driver().start_login(case_id, mobile)
    except Unavailable as e:
        return portal._unavailable(state, e, "login")
    except PortalError as e:
        return portal._to_safe_stop(state, e, "login")
    if not result.sent:
        log_event("browser_agent", "mobile_refused", detail={"mobile_last4": mobile[-4:], "for": flow})
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("mobile_refused", l))})
    log_event("browser_agent", "otp_requested", detail={"mobile_last4": mobile[-4:], "for": flow})
    return Command(goto="otp", update={
        "flow": flow, "otp": {"attempts": 0, "sent_at": time.time(), "masked": fields.masked("mobile", mobile)},
        "status": "awaiting_otp", "asking": None, "preview": None, "progress": progress.get(case_id),
        **_say(lang, lambda l: t("otp_sent", l, last4=mobile[-4:]))})


# --- renewing the read-only access -----------------------------------------------------------


def renew(state: dict[str, Any]) -> Command[Literal["otp", "safe_stop", "__end__"]]:
    if not state.get("applications"):
        return Command(goto=END, update=_say(_lang(state), lambda l: t("renew_nothing", l)))
    return _login(state, "renew")


def renew_delegate(state: dict[str, Any]) -> Command[Literal["renew", "safe_stop", "__end__"]]:
    lang = _lang(state)
    case_id = _case_id()
    driver = get_driver()
    try:
        d = driver.delegate(case_id)
    except SessionGone:
        return Command(goto="renew")
    except Unavailable as e:
        return portal._unavailable(state, e, "delegation")
    except PortalError as e:
        return portal._to_safe_stop(state, e, "delegation")
    get_store().save_delegation(case_id, sealed.seal(case_id, "delegation", d.token), d.expires_at, d.scopes)
    log_event("browser_agent", "delegation_renewed",
              detail={"scopes": d.scopes or DELEGATION_SCOPES, "hours": DELEGATION_HOURS, "expires_at": d.expires_at})
    driver.close(case_id, "renewed")
    return Command(goto=END, update={**CLEAR, "delegation": {"expires_at": d.expires_at, "scopes": d.scopes},
                                     "progress": progress.get(case_id), "asking": None,
                                     **_say(lang, lambda l: t("renew_done", l))})


# --- correction after CORRECTION_REQUIRED -----------------------------------------------------


def _doc_names(sid: str, docs: list[str], lang: str) -> str:
    scheme = rules.load_schemes()[sid]
    return join([rules.doc_label(scheme, d, lang) for d in docs], lang)


def correct(state: dict[str, Any]) -> Command[Literal["otp", "safe_stop", "__end__"]]:
    """Is there something to correct, and are the corrected documents uploaded? Then log in."""
    lang = _lang(state)
    sid = (state.get("correcting") or {}).get("scheme_id")
    apps = state.get("applications") or {}
    if not sid or sid not in apps:
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_none", l))})
    if apps[sid].get("status") != "CORRECTION_REQUIRED":
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_not_needed", l, title=_title(sid, l)))})
    corr = (state.get("corrections") or {}).get(sid) or {}
    if not corr.get("ready"):
        waiting = corr.get("waiting") or []
        log_event("agent", "correction_waiting_for_documents", scheme_id=sid, detail={"waiting": waiting})
        if waiting:
            return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t(
                "correct_need_upload", l, docs=_doc_names(sid, waiting, l)))})
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_need_upload_any", l))})
    cmd = _login(state, "correct")
    if cmd.goto == "otp":
        cmd.update["correcting"] = {"scheme_id": sid, "app_id": apps[sid]["app_id"], "docs": corr["docs"]}
    return cmd


def _targets(state: dict[str, Any], case_id: str) -> tuple[list, dict[str, Any]]:
    c = state.get("correcting") or {}
    scheme = rules.load_schemes()[c["scheme_id"]]
    uploads, _, _ = portal._documents(scheme, case_id)
    return [u for u in uploads if u.doc in c.get("docs", []) and u.doc_id], c


def correct_check(state: dict[str, Any]) -> Command[Literal["correct_confirm", "correct", "safe_stop", "__end__"]]:
    """After the OTP: the portal still asks for the correction; read back what will change."""
    lang = _lang(state)
    case_id = _case_id()
    c = state.get("correcting") or {}
    sid, app_id = c.get("scheme_id"), c.get("app_id")
    driver = get_driver()
    try:
        live = driver.application_status(case_id, app_id)
    except SessionGone:
        return Command(goto="correct")
    except Unavailable as e:
        return portal._unavailable(state, e, "correction")
    except PortalError as e:
        return portal._to_safe_stop(state, e, "correction")
    if live != "CORRECTION_REQUIRED":
        driver.close(case_id, "correction_not_needed")
        log_event("agent", "correction_not_needed", scheme_id=sid, detail={"portal_status": live})
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_already", l, title=_title(sid, l)))})
    targets, _ = _targets(state, case_id)
    if not targets:  # deleted since
        driver.close(case_id, "documents_missing")
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_need_upload_any", l))})
    docs = [u.doc for u in targets]
    log_event("agent", "correction_confirm_requested", scheme_id=sid, detail={"documents": docs, "app_id": app_id})
    return Command(goto="correct_confirm", update={
        "correcting": {**c, "docs": docs}, "status": "awaiting_correction_confirmation", "asking": None, "ui": None,
        "progress": progress.get(case_id),
        **_say(lang, lambda l: t("correct_readback", l, docs=_doc_names(sid, docs, l), title=_title(sid, l)))})


def correct_confirm(state: dict[str, Any]) -> Command[Literal["correct_submit", "correct_confirm", "__end__"]]:
    # The human gate for a correction: nothing is replaced or resubmitted until an explicit yes.
    # (Default-deny, like the confirm node: anything unclear asks again.)
    c = state.get("correcting") or {}
    sid = c.get("scheme_id")
    answer = interrupt({"type": "confirm", "kind": "correction", "scheme_id": sid, "app_id": c.get("app_id"),
                        "documents": [rules.doc_label(rules.load_schemes()[sid], d, "en") for d in c.get("docs", [])]})
    lang = _lang(state)
    if isinstance(answer, dict):  # a review-screen call: nothing to edit here
        return Command(goto="correct_confirm", update=_say(lang, lambda l: t("correct_reask", l)))
    decision = parse_decision(str(answer))
    detail: dict[str, Any] = {"decision": decision}
    if decision != "unclear" and len(tokenize(str(answer))) <= 3:
        detail["said"] = str(answer).strip()
    log_event("citizen", {"yes": "correction_approved", "no": "correction_declined"}.get(decision, "correction_unclear"),
              scheme_id=sid, detail=detail)
    if decision == "yes":
        return Command(goto="correct_submit")
    if decision == "no":
        try:
            get_driver().close(_case_id(), "correction_cancelled")
        except PortalError:
            pass
        return Command(goto=END, update={**CLEAR, "status": "submitted",
                                         **_say(lang, lambda l: t("correct_cancelled", l))})
    return Command(goto="correct_confirm", update=_say(lang, lambda l: t("correct_reask", l)))


def correct_submit(state: dict[str, Any]) -> Command[Literal["correct", "safe_stop", "__end__"]]:
    """Only reached through the gate's explicit yes. Replace the documents, resubmit, once."""
    lang = _lang(state)
    case_id = _case_id()
    c = state.get("correcting") or {}
    sid, app_id = c.get("scheme_id"), c.get("app_id")
    apps = dict(state.get("applications") or {})
    driver = get_driver()
    if (apps.get(sid) or {}).get("status") != "CORRECTION_REQUIRED":  # last line of defence: never twice
        _blocked(sid, app_id, "correct_submit")
        driver.close(case_id, "resubmit_blocked")
        return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_already", l, title=_title(sid, l)))})
    try:
        if driver.application_status(case_id, app_id) != "CORRECTION_REQUIRED":
            _blocked(sid, app_id, "correct_submit_portal")
            driver.close(case_id, "resubmit_blocked")
            return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_already", l, title=_title(sid, l)))})
        targets, _ = _targets(state, case_id)
        if not targets:
            driver.close(case_id, "documents_missing")
            return Command(goto=END, update={**CLEAR, **_say(lang, lambda l: t("correct_need_upload_any", l))})
        for u in targets:
            driver.replace_document(case_id, app_id, u)
        log_event("browser_agent", "documents_replaced", scheme_id=sid,
                  detail={"app_id": app_id, "documents": [u.doc for u in targets]})
        new_status = driver.resubmit(case_id, app_id)
    except SessionGone:
        return Command(goto="correct")
    except Unavailable as e:
        return portal._unavailable(state, e, "correction")
    except PortalError as e:
        return portal._to_safe_stop(state, e, "correction")
    docs = [u.doc for u in targets]
    log_event("browser_agent", "correction_resubmitted", scheme_id=sid,
              detail={"app_id": app_id, "status": new_status, "documents": docs})
    get_store().note_resubmitted(case_id, sid, new_status)
    apps[sid] = {**apps[sid], "status": new_status}
    driver.close(case_id, "corrected")
    return Command(goto=END, update={**CLEAR, "applications": apps, "status": "submitted", "progress": progress.get(case_id),
                                     "corrections": {}, **_say(lang, lambda l: t(
                                         "correct_done", l, docs=_doc_names(sid, docs, l), title=_title(sid, l)))})
