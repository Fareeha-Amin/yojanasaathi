"""The portal part of the case graph (Phase 4). agent/graph.py wires these nodes in:

  eligibility -> prepare: documents in the vault? (else: list what's missing, stop)
                  -> ask the form fields still missing, one per turn (router -> collect
                     -> prepare), then the portal-registered mobile, then the declaration
                  -> portal_login: drift check, open /citizen-access, send the OTP
  otp          interrupt({"type": "otp"}): the citizen says the code (edge.prepare keeps
               it out of the graph); wrong -> again (3 tries), expired -> "send again"
  portal_fill  drift check, already submitted? (portal API), fill steps 1-3, upload the
               documents, stop at step 4 with the declaration unticked -> confirm
  confirm      (agent/graph.py) the human gate; an edit -> collect / prepare -> portal_fill
  submit       (agent/graph.py) explicit yes only: tick the declaration, Submit, store the
               YJS-... number, ask the portal for read-only delegation (Phase 6)
  safe_stop    interrupt({"type": "safe_stop"}): the portal is not what the script expects

The OTP and sensitive answers never reach these nodes as text (agent/portal/edge.py);
every consequential step calls log_event() with field names / IDs only.
"""

import time
from typing import Any, Literal

from langgraph.config import get_config
from langgraph.types import Command, interrupt

from agent import config, rules, sealed
from agent.audit import log_event
from agent.facts import extract
from agent.gate import parse_decision
from agent.numbers import tokenize
from agent.portal import DocUpload, PortalError, SafeStop, SessionGone, Unavailable, drift, edge, fields
from agent.portal import get_driver, progress
from agent.portal.api import DELEGATION_HOURS, DELEGATION_SCOPES
from agent.replies import day, join, money, t
from agent.tracking import get_store
from agent.replies import readback as readback_text

OTP_TRIES = 3
PORTAL_TYPES = {"application/pdf", "image/jpeg", "image/png"}  # what the portal takes
PORTAL_MAX_BYTES = 5 * 1024 * 1024
CANCEL = {"cancel", "stop", "quit", "ರದ್ದು", "ರದ್ದುಮಾಡಿ", "ನಿಲ್ಲಿಸಿ", "रद्द", "रोको", "बंद"}
RETRY = ("try again", "retry", "again", "ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ", "ಮತ್ತೆ", "फिर से", "दोबारा")
QUESTION_WORDS = {"what", "why", "how", "which", "who", "when", "where", "ಏನು", "ಏಕೆ", "ಯಾಕೆ", "ಹೇಗೆ",
                  "ಯಾವ", "क्या", "क्यों", "कैसे", "कौन", "कब", "कहां"}


# --- helpers --------------------------------------------------------------------------

def _case_id() -> str:
    return get_config()["configurable"]["thread_id"]


def _lang(state: dict[str, Any]) -> str:
    return state.get("lang") or "en"


def _say(lang: str, build) -> dict[str, Any]:
    return {"reply": build(lang), "subtitle": None if lang == "en" else build("en")}


def _scheme(state: dict[str, Any]) -> dict[str, Any]:
    return rules.load_schemes()[state["selected"]]


def _open(case_id: str, state: dict[str, Any], field: str) -> str | None:
    s = (state.get("form") or {}).get(field)
    return sealed.open_(case_id, field, s) if s else None


def _is_cancel(text: str) -> bool:
    return any(tok in CANCEL for tok in tokenize(text))


def _wants_retry(text: str) -> bool:
    low = text.lower()
    return parse_decision(text) == "yes" or any(p in low for p in RETRY)


def _questionish(text: str) -> bool:
    toks = tokenize(text)
    return "?" in text or bool(toks and toks[0] in QUESTION_WORDS)


def question(field: str, scheme: dict[str, Any], lang: str) -> str:
    """One short question for a form field, with the portal's own label and choices."""
    label = fields.label(scheme, field, lang)
    kind = fields.FIELD_KINDS.get(field)
    if field in fields.NAME_FIELDS:
        return t("ask_form_name", lang, label=label)
    if kind in ("date", "mobile", "account", "ifsc"):
        return t(f"ask_form_{kind}", lang)
    if kind == "choice":
        return t("ask_form_choice", lang, label=label, options=fields.options_text(scheme, field, lang))
    if kind == "declaration":
        return t("ask_form_declaration", lang, text=label)
    return t("ask_form_field", lang, label=label)


def _ack(state: dict[str, Any], scheme: dict[str, Any], lang: str) -> str | None:
    """"I heard ..." for the answer just stored (sensitive values as a marker: the digits
    are put into the /turn reply only, see edge.expand)."""
    f = state.get("ack_field")
    if not f:
        return None
    if f == "declaration_consent":
        return t("form_declared", lang)
    if f in fields.SENSITIVE:
        return t("form_heard", lang, value=edge.marker(f))
    shown = (state.get("form_shown") or {}).get(f)
    if fields.FIELD_KINDS.get(f) == "choice":
        v = _open(_case_id(), state, f)
        shown = fields.choice_text(scheme, f, v, lang) if v else shown
    return t("form_noted", lang, value=shown) if shown else None


def _documents(scheme: dict[str, Any], case_id: str) -> tuple[list[DocUpload], list[str], list[str]]:
    """(uploads in the portal's order, required documents missing, documents the portal
    won't take: not PDF / JPG / PNG, or over 5 MB). Optional ("where applicable")
    documents are uploaded when present and usable, else skipped."""
    stored = {d["doc_type"]: d for d in progress.recorder().documents(case_id)}
    uploads, missing, bad = [], [], []
    for d in scheme["documents"]:
        name = d["label"]["en"]
        optional = "where applicable" in name.lower()
        row = stored.get(d["doc"])
        usable = row is not None and row["content_type"] in PORTAL_TYPES and row["size_bytes"] <= PORTAL_MAX_BYTES
        if usable:
            uploads.append(DocUpload(d["doc"], name, str(row["id"]), row["content_type"]))
            continue
        uploads.append(DocUpload(d["doc"], name, None))
        if not optional:
            (bad if row is not None else missing).append(d["doc"])
    return uploads, missing, bad


def _todo(state: dict[str, Any], scheme: dict[str, Any]) -> list[str]:
    """Form fields still to ask, in the portal's order; then the mobile, then the declaration."""
    form = state.get("form") or {}
    profile = state.get("profile") or {}
    out = []
    for f in fields.form_fields(scheme):
        if f == "declaration_consent" or f in form:
            continue
        if f == "annual_income" and profile.get("annual_income") is not None:
            continue
        out.append(f)
    if "mobile" not in form:
        out.append("mobile")
    if "declaration_consent" in fields.form_fields(scheme) and scheme["scheme_id"] not in (state.get("declared") or []):
        out.append("declaration_consent")
    return out


def _values(state: dict[str, Any], scheme: dict[str, Any], case_id: str) -> dict[str, Any]:
    """What the browser types: our answers (opened only here, in memory), the income from
    the profile, the declaration the citizen affirmed."""
    out: dict[str, Any] = {}
    for f in fields.form_fields(scheme):
        if f == "declaration_consent":
            out[f] = scheme["scheme_id"] in (state.get("declared") or [])
        elif f == "annual_income" and (state.get("profile") or {}).get("annual_income") is not None:
            out[f] = str(int(state["profile"]["annual_income"]))
        else:
            out[f] = _open(case_id, state, f)
    return out


def _to_safe_stop(state: dict[str, Any], e: PortalError, step: str) -> Command:
    sid = state.get("selected")
    step = e.step or step
    log_event("browser_agent", "safe_stop", scheme_id=sid,
              detail={"step": step, "reason": str(e), **e.detail})
    lang = _lang(state)
    stop = {"step": step, "reason": str(e), "screenshot": e.screenshot,
            "message": t("safe_stop", lang, step=t(f"step_{step}", lang)),
            "message_en": t("safe_stop", "en", step=t(f"step_{step}", "en"))}
    return Command(goto="safe_stop", update={
        "stop": stop, "status": "safe_stop", "asking": None, "progress": progress.get(_case_id()),
        **_say(lang, lambda l: t("safe_stop", l, step=t(f"step_{step}", l)))})


def _unavailable(state: dict[str, Any], e: PortalError, step: str) -> Command:
    log_event("browser_agent", "portal_unavailable", scheme_id=state.get("selected"),
              detail={"step": step, "reason": str(e)})
    return Command(goto="__end__", update={
        "asking": None, "status": "portal_unavailable",
        **_say(_lang(state), lambda l: t("portal_unavailable", l))})


def _restart(state: dict[str, Any], why: str) -> Command:
    """The browser session is gone (idle, restart): log in again, never reuse a preview."""
    log_event("agent", "portal_session_restart", scheme_id=state.get("selected"), detail={"why": why})
    goto = {"renew": "renew", "correct": "correct"}.get(state.get("flow"), "portal_login")  # Phase 6 flows
    return Command(goto=goto, update={"portal_note": "session_restart", "preview": None})


def _drift(state: dict[str, Any], scheme: dict[str, Any], step: str) -> Command | None:
    diffs = drift.compare(get_driver().requirements(scheme["scheme_id"]), scheme)
    if not diffs:
        return None
    log_event("browser_agent", "portal_drift", scheme_id=scheme["scheme_id"], detail={"differences": diffs[:20]})
    return _to_safe_stop(state, SafeStop("the portal's form changed", step=step,
                                         detail={"differences": len(diffs)}), step)


# --- the router's part: an answer to a form question -------------------------------------

def route_form(state: dict[str, Any], msg: str, lang: str | None) -> Command | None:
    """The message after a form question. None = not about the form (e.g. another scheme
    named): the router carries on as usual. `lang`: the case's, or the message script's."""
    field = state["asking"][5:]
    sid = state.get("selected")
    scheme = rules.load_schemes().get(sid) if sid else None
    base: dict[str, Any] = {"ui": None, "subtitle": None, "intent": "form"}
    if lang:
        base["lang"] = lang
    lang = lang or "en"
    fi = state.get("form_input")
    if fi and fi.get("field") == field:  # read and sealed by edge.prepare
        return Command(goto="collect", update=base)
    if field not in fields.SENSITIVE:
        ans = fields.parse(field, msg, scheme)
        if ans is not None:
            return Command(goto="collect", update={
                **base, "form_input": edge.form_input(_case_id(), field, ans.value, scheme, lang)})
    facts = extract(msg, None)
    if facts.chosen and facts.chosen != sid:
        return None
    if facts.status and not facts.values:
        return Command(goto="status", update=base)
    if _is_cancel(msg):
        return Command(goto="declined", update={**base, "asking": None})
    if parse_decision(msg) == "no" and state.get("last_field"):
        return Command(goto="collect", update={**base, "form_input": {"redo": state["last_field"]}})
    if _questionish(msg):
        return Command(goto="respond", update=base)
    return Command(goto="collect", update={**base, "form_input": {"field": field, "error": True}})


# --- nodes ---------------------------------------------------------------------------

def prepare(state: dict[str, Any]) -> Command[Literal["portal_login", "portal_fill", "__end__"]]:
    """Before the browser: documents, then the form questions, then log in."""
    lang = _lang(state)
    sid = state["selected"]
    scheme = _scheme(state)
    case_id = _case_id()
    if not config.portal_ready():
        log_event("agent", "portal_not_configured", scheme_id=sid)
        return Command(goto="__end__", update={"asking": None, **_say(lang, lambda l: t("portal_not_configured", l))})
    driver = get_driver()
    driver.warmup()
    update: dict[str, Any] = {"ack_field": None, "portal_note": None, "flow": None}  # flow: Phase 6 renew / correct

    # gender already said ("I am a woman"): the portal's option, read back at review
    form, shown = dict(state.get("form") or {}), dict(state.get("form_shown") or {})
    g = (state.get("profile") or {}).get("gender")
    if "gender" in fields.form_fields(scheme) and "gender" not in form and g in ("female", "male"):
        fi = edge.form_input(case_id, "gender", g.capitalize(), scheme, lang)
        form["gender"], shown["gender"] = fi["sealed"], fi["shown"]
        update.update(form=form, form_shown=shown)
        state = {**state, "form": form, "form_shown": shown}

    _, missing, bad = _documents(scheme, case_id)
    if missing or bad:
        log_event("agent", "portal_documents_missing", scheme_id=sid, detail={"missing": missing, "unusable": bad})

        def docs(l: str) -> str:
            parts = []
            if missing:
                parts.append(t("docs_needed_portal", l, title=rules.title(scheme, l),
                               docs=join([rules.doc_label(scheme, d, l) for d in missing], l)))
            if bad:
                parts.append(t("docs_bad_format", l, docs=join([rules.doc_label(scheme, d, l) for d in bad], l)))
            return " ".join(parts)

        return Command(goto="__end__", update={**update, "asking": "proceed", "status": "needs_documents",
                                               **_say(lang, docs)})

    todo = _todo(state, scheme)
    if todo:
        f = todo[0]
        starting = not any(x in (state.get("form") or {}) for x in [*fields.form_fields(scheme), "mobile"]) \
            and state.get("ack_field") is None
        slow = driver.state() != "ready" and not state.get("portal_told_slow")

        def ask(l: str) -> str:
            parts = [_ack(state, scheme, l)]
            if starting:
                parts.append(t("form_intro", l, title=rules.title(scheme, l)))
            if slow:
                parts.append(t("portal_slow", l))
            parts.append(question(f, scheme, l))
            return " ".join(p for p in parts if p)

        return Command(goto="__end__", update={
            **update, "asking": f"form:{f}", "status": "collecting_form",
            "portal_told_slow": bool(state.get("portal_told_slow") or slow),
            "ui": {"type": "form", "field": f, "sensitive": f in fields.SENSITIVE,
                   "label": fields.label(scheme, f, lang), "label_en": fields.label(scheme, f, "en")},
            **_say(lang, ask)})

    if driver.stage(case_id) in ("logged_in", "review"):  # an edit at the review: fill again
        return Command(goto="portal_fill", update={**update, "asking": None})
    return Command(goto="portal_login", update={**update, "asking": None})


def collect(state: dict[str, Any]) -> Command[Literal["prepare", "__end__"]]:
    """Store one form answer (sealed), or ask again."""
    lang = _lang(state)
    scheme = _scheme(state)
    sid = scheme["scheme_id"]
    case_id = _case_id()
    fi = state.get("form_input") or {}
    upd: dict[str, Any] = {"form_input": None}
    if fi.get("redo"):
        f = fi["redo"]
        return Command(goto="__end__", update={**upd, "asking": f"form:{f}", "last_field": None,
                                               **_say(lang, lambda l: f"{t('form_redo', l)} {question(f, scheme, l)}")})
    f = fi.get("field") or (state.get("asking") or "form:")[5:]
    if fi.get("error") or not f:
        return Command(goto="__end__", update={**upd, **_say(lang, lambda l: f"{t('form_retry', l)} {question(f, scheme, l)}")})

    if f == "declaration_consent":
        if not fi.get("value"):
            log_event("citizen", "declaration_declined", scheme_id=sid)
            return Command(goto="__end__", update={**upd, "asking": None, "status": "cancelled",
                                                   **_say(lang, lambda l: t("declaration_declined", l))})
        log_event("citizen", "declaration_affirmed", scheme_id=sid, detail={"said": "yes"})
        declared = [*(state.get("declared") or []), sid]
        return Command(goto="prepare", update={**upd, "declared": declared, "ack_field": f, "last_field": None})

    if f == "dob":
        age = fields.age_from(sealed.open_(case_id, "dob", fi["sealed"]))
        said = (state.get("profile") or {}).get("age")
        if said is not None and abs(age - said) > 1:
            log_event("agent", "form_answer_rejected", scheme_id=sid, detail={"field": f, "why": "age_mismatch"})
            return Command(goto="__end__", update={**upd, **_say(lang, lambda l: t("dob_mismatch", l, age=age, said=said))})
    form = {**(state.get("form") or {}), f: fi["sealed"]}
    shown = {**(state.get("form_shown") or {}), f: fi["shown"]}
    upd.update(form=form, form_shown=shown, last_field=f, ack_field=f)
    if f == "annual_income":
        upd["profile"] = {**(state.get("profile") or {}), "annual_income": int(sealed.open_(case_id, f, fi["sealed"]))}
    log_event("citizen", "form_answered", scheme_id=sid, detail={"field": f, "sensitive": f in fields.SENSITIVE})
    return Command(goto="prepare", update=upd)


def portal_login(state: dict[str, Any]) -> Command[Literal["otp", "safe_stop", "__end__"]]:
    lang = _lang(state)
    scheme = _scheme(state)
    sid = scheme["scheme_id"]
    case_id = _case_id()
    driver = get_driver()
    mobile = _open(case_id, state, "mobile")
    try:
        stop = _drift(state, scheme, "login")
        if stop:
            return stop
        result = driver.start_login(case_id, mobile)
    except Unavailable as e:
        return _unavailable(state, e, "login")
    except PortalError as e:
        return _to_safe_stop(state, e, "login")
    if not result.sent:
        form = {k: v for k, v in (state.get("form") or {}).items() if k != "mobile"}
        log_event("browser_agent", "mobile_refused", scheme_id=sid, detail={"mobile_last4": mobile[-4:]})
        return Command(goto="__end__", update={
            "form": form, "asking": "form:mobile", "progress": progress.get(case_id),
            **_say(lang, lambda l: f"{t('mobile_refused', l)} {question('mobile', scheme, l)}")})
    log_event("browser_agent", "otp_requested", scheme_id=sid, detail={"mobile_last4": mobile[-4:]})
    note = state.get("portal_note")

    def sent(l: str) -> str:
        return " ".join(p for p in (t(note, l) if note else None, t("otp_sent", l, last4=mobile[-4:])) if p)

    return Command(goto="otp", update={
        "otp": {"attempts": 0, "sent_at": time.time(), "masked": fields.masked("mobile", mobile)},
        "status": "awaiting_otp", "asking": None, "portal_note": None, "progress": progress.get(case_id),
        **_say(lang, sent)})


def otp(state: dict[str, Any]) -> Command[Literal["otp", "portal_fill", "portal_login", "safe_stop", "renew",
                                                  "renew_delegate", "correct", "correct_check", "__end__"]]:
    info = dict(state.get("otp") or {})
    # Only the citizen supplies the code (design rule 3). Nothing above this line: the node
    # runs again from the top when the graph resumes.
    answer = interrupt({"type": "otp", "masked_mobile": info.get("masked"), "scheme_id": state.get("selected"),
                        "attempts_left": OTP_TRIES - info.get("attempts", 0)})
    lang = _lang(state)
    sid = state.get("selected")
    case_id = _case_id()
    text = str(answer)
    driver = get_driver()
    code = edge.take_otp(case_id)
    try:
        if code:
            if driver.verify_otp(case_id, code):
                log_event("citizen", "otp_verified", scheme_id=sid)
                nxt = {"renew": "renew_delegate", "correct": "correct_check"}.get(state.get("flow"), "portal_fill")
                return Command(goto=nxt, update={"otp": {**info, "verified": True},
                                                 "progress": progress.get(case_id)})
            info["attempts"] = info.get("attempts", 0) + 1
            log_event("citizen", "otp_rejected", scheme_id=sid, detail={"attempt": info["attempts"]})
            if info["attempts"] >= OTP_TRIES:
                driver.close(case_id, "otp_failed")
                log_event("agent", "otp_failed", scheme_id=sid, detail={"attempts": info["attempts"]})
                return Command(goto="__end__", update={
                    "otp": info, "asking": None, "status": "stopped", "progress": progress.get(case_id), "flow": None,
                    **_say(lang, lambda l: t("otp_failed", l, title=rules.title(_scheme(state), l)))})
            expired = time.time() - info.get("sent_at", 0) > config.OTP_TTL_SECONDS
            left = OTP_TRIES - info["attempts"]
            return Command(goto="otp", update={"otp": info, **_say(
                lang, lambda l: t("otp_expired", l) if expired else t("otp_wrong", l, left=left))})
        if edge.wants_resend(text):
            driver.resend_otp(case_id)
            log_event("browser_agent", "otp_resent", scheme_id=sid)
            return Command(goto="otp", update={"otp": {**info, "sent_at": time.time()},
                                               **_say(lang, lambda l: t("otp_resent", l))})
    except SessionGone:
        return _restart(state, "otp")
    except Unavailable as e:
        return _unavailable(state, e, "otp")
    except PortalError as e:
        return _to_safe_stop(state, e, "otp")
    if parse_decision(text) == "no" or _is_cancel(text):
        driver.close(case_id, "cancelled")
        log_event("citizen", "otp_cancelled", scheme_id=sid)
        return Command(goto="__end__", update={"asking": None, "status": "cancelled", "flow": None,
                                               **_say(lang, lambda l: t("cancelled", l))})
    return Command(goto="otp", update=_say(lang, lambda l: t("otp_unclear", l)))


def already(state: dict[str, Any], existing: dict[str, Any]) -> Command:
    """The portal already has a submitted application for this scheme (e.g. after "delete
    my data", or a submit that finished just before a crash): store its number, never apply
    again."""
    lang = _lang(state)
    sid = state["selected"]
    case_id = _case_id()
    applications = dict(state.get("applications") or {})
    applications[sid] = {"app_id": existing["app_id"], "status": existing.get("status") or "SUBMITTED"}
    log_event("browser_agent", "already_submitted_on_portal", scheme_id=sid, detail={"app_id": existing["app_id"]})
    get_driver().close(case_id, "already_submitted")
    remaining = [s for s in state.get("offered") or [] if s not in applications]
    return Command(goto="__end__", update={
        "applications": applications, "app_id": existing["app_id"], "last_submitted": sid,
        "offered": remaining, "asking": "choose" if remaining else None, "status": "submitted",
        "progress": progress.get(case_id),
        **_say(lang, lambda l: t("already_on_portal", l, app_id=existing["app_id"])),
        "ui": {"type": "submitted", "scheme_id": sid, "title": rules.title(_scheme(state), lang),
               "app_id": existing["app_id"], "next": [{"scheme_id": s, "title": rules.title(
                   rules.load_schemes()[s], lang)} for s in remaining]}})


def portal_fill(state: dict[str, Any]) -> Command[Literal["confirm", "portal_login", "safe_stop", "__end__"]]:
    lang = _lang(state)
    scheme = _scheme(state)
    sid = scheme["scheme_id"]
    case_id = _case_id()
    driver = get_driver()
    if sid in (state.get("applications") or {}):
        return Command(goto="__end__", update=_say(lang, lambda l: t(
            "already_submitted", l, app_id=state["applications"][sid]["app_id"])))
    uploads, missing, bad = _documents(scheme, case_id)
    if missing or bad:  # deleted while the form was being filled
        driver.close(case_id, "documents_missing")
        return Command(goto="prepare")
    try:
        stop = _drift(state, scheme, "applicant")
        if stop:
            return stop
        existing = driver.existing_application(case_id, sid)
        if existing:
            return already(state, existing)
        result = driver.fill(case_id, scheme, _values(state, scheme, case_id), uploads)
        if result.already:
            return already(state, result.already)
    except SessionGone:
        return _restart(state, "fill")
    except Unavailable as e:
        return _unavailable(state, e, "fill")
    except PortalError as e:
        return _to_safe_stop(state, e, "applicant")
    preview = build_preview(state, scheme, result, case_id, lang)
    log_event("browser_agent", "form_filled", scheme_id=sid,
              detail={"fields": sorted(result.values), "documents": [u.doc for u in uploads if u.doc_id]})
    # Logged here, not in confirm: an interrupted node re-runs from the top on resume.
    log_event("agent", "confirm_requested", scheme_id=sid, detail={
        "fields": sorted(result.values), "documents": [u.doc for u in uploads if u.doc_id],
        "declaration": "read back", "delegation_hours": DELEGATION_HOURS})
    return Command(goto="confirm", update={
        "preview": preview, "status": "awaiting_confirmation", "asking": None, "progress": progress.get(case_id),
        "reply": preview["readback"], "subtitle": None if lang == "en" else preview["readback_en"]})


def readback(state: dict[str, Any], scheme: dict[str, Any], case_id: str, lang: str) -> str:
    """The spoken read-back before the yes: name, date of birth (marker: the date is put in
    only at the edge), income, account (last 4), the declaration and the 24-hour read-only
    access, then the question. 3 sentences."""
    name_field = next((f for f in fields.NAME_FIELDS if f in fields.form_fields(scheme)), None)
    name = _open(case_id, state, name_field) if name_field else ""
    income = (state.get("profile") or {}).get("annual_income")
    account = _open(case_id, state, "bank_account_number") or ""
    decl = fields.label(scheme, "declaration_consent", lang)
    profile = state.get("profile") or {}
    unsure = {f: profile[f] for f in scheme["required_fields"] if f in (state.get("readback") or []) and f in profile}
    parts = [
        t("portal_readback", lang, name=name, dob=edge.marker("dob"),
          income=money(income) if income is not None else "-", last4=account[-4:]),
    ]
    if unsure:  # a number the parser did not read for sure (e.g. from the LLM): say so
        parts.append(t("readback_unsure", lang, fields=readback_text(unsure, lang)))
    parts += [t("portal_declare", lang, declaration=decl.rstrip(".।"), hours=DELEGATION_HOURS),
              t("confirm_ask", lang, title=rules.title(scheme, lang))]
    return " ".join(parts)


def build_preview(state: dict[str, Any], scheme: dict[str, Any], result, case_id: str, lang: str) -> dict[str, Any]:
    """What the citizen approves: the values the PAGE shows (read back by data-testid),
    sensitive ones masked; the uploaded documents; the screenshots; the declaration."""
    profile = state.get("profile") or {}
    shown_now = dict(state.get("form_shown") or {})

    def text(f: str, v: Any, l: str) -> str:
        if f in fields.SENSITIVE:
            return shown_now.get(f) or fields.masked(f, str(v))
        return fields.shown(scheme, f, v, l)

    # `value`: what the page shows, for non-sensitive fields only (screens render it in any
    # language); sensitive ones keep only their masked text.
    form = [{"field": f, "label": fields.label(scheme, f, lang), "label_en": fields.label(scheme, f, "en"),
             "value": None if f in fields.SENSITIVE else v,
             "text": text(f, v, lang), "text_en": text(f, v, "en"), "sensitive": f in fields.SENSITIVE}
            for f, v in result.values.items()]
    form.append({"field": "mobile", "label": fields.label(scheme, "mobile", lang),
                 "label_en": fields.label(scheme, "mobile", "en"), "value": None,
                 "text": shown_now.get("mobile"), "text_en": shown_now.get("mobile"), "sensitive": True})
    shots = [s["screenshot"] for s in progress.get(case_id) or [] if s.get("screenshot")]
    docs_by_name = {d["label"]["en"]: d["doc"] for d in scheme["documents"]}
    return {
        "portal": True,
        "scheme_id": scheme["scheme_id"],
        "title": rules.title(scheme, lang),
        "fields": {f: profile[f] for f in scheme["required_fields"] if f in profile},  # editable on Review
        "needs_readback": [f for f in scheme["required_fields"] if f in (state.get("readback") or [])],
        "form": form,
        "documents": [{"doc": docs_by_name.get(n, n), "label": rules.doc_label(scheme, docs_by_name.get(n, n), lang),
                       "status": "uploaded"} for n in result.documents],
        "documents_missing": 0,
        "screenshots": shots,
        "declaration": {"text": fields.label(scheme, "declaration_consent", lang),
                        "text_en": fields.label(scheme, "declaration_consent", "en")},
        "delegation": {"hours": DELEGATION_HOURS, "scopes": DELEGATION_SCOPES},
        "fingerprint": result.fingerprint,
        "readback": readback(state, scheme, case_id, lang),
        "readback_en": readback(state, scheme, case_id, "en"),
        "source_url": scheme["source_url"],
        "effective_date": scheme["effective_date"],
        "effective_date_text": day(scheme["effective_date"], lang),
    }


def safe_stop(state: dict[str, Any]) -> Command[Literal["prepare", "__end__"]]:
    stop = state.get("stop") or {}
    answer = interrupt({"type": "safe_stop", "scheme_id": state.get("selected"), "step": stop.get("step"),
                        "message": stop.get("message"), "message_en": stop.get("message_en"),
                        "screenshot": stop.get("screenshot")})
    lang = _lang(state)
    sid = state.get("selected")
    case_id = _case_id()
    try:
        get_driver().close(case_id, "safe_stop")
    except PortalError:
        pass
    if _wants_retry(str(answer)):
        log_event("citizen", "safe_stop_retry", scheme_id=sid)
        goto = {"renew": "renew", "correct": "correct"}.get(state.get("flow"), "prepare")
        return Command(goto=goto, update={"stop": None})
    log_event("citizen", "safe_stop_ended", scheme_id=sid)
    return Command(goto="__end__", update={"stop": None, "asking": None, "status": "stopped", "flow": None,
                                           **_say(lang, lambda l: t("safe_stop_ended", l))})


# --- submit (called by agent/graph.py's submit node, after the gate) ----------------------

def submit_to_portal(state: dict[str, Any]) -> tuple[str | None, Command | None]:
    """(application number, None) or (None, where to go instead). Never submits a preview
    that is not exactly what the open page shows."""
    sid = state["selected"]
    case_id = _case_id()
    driver = get_driver()
    preview = state.get("preview") or {}
    if not driver.fresh(case_id, sid, preview.get("fingerprint") or ""):
        log_event("agent", "submit_blocked_stale", scheme_id=sid)
        return None, _restart(state, "stale_preview")
    # Write-ahead: if the agent dies between the click and the checkpoint, the next attempt
    # finds the submission through the portal API (portal_fill) instead of submitting again.
    log_event("browser_agent", "submit_clicked", scheme_id=sid, detail={"preview": preview["fingerprint"][:12]})
    try:
        return driver.submit(case_id, sid), None
    except SessionGone:
        return None, _restart(state, "submit")
    except Unavailable as e:
        return None, _unavailable(state, e, "submit")
    except PortalError as e:
        return None, _to_safe_stop(state, e, "submit")


def after_submit(state: dict[str, Any], app_id: str) -> dict[str, Any]:
    """Read-only delegation for status tracking (Phase 6), then close the browser."""
    sid = state["selected"]
    case_id = _case_id()
    driver = get_driver()
    update: dict[str, Any] = {"progress": progress.get(case_id)}
    try:
        d = driver.delegate(case_id)
        # Phase 6: the poller's table (agent/tracking/store.py), sealed; the case memory keeps
        # only when it ends and what it may read, so deleting a final application's token never
        # means editing a checkpoint
        get_store().save_delegation(case_id, sealed.seal(case_id, "delegation", d.token), d.expires_at, d.scopes)
        update["delegation"] = {"expires_at": d.expires_at, "scopes": d.scopes}
        log_event("browser_agent", "delegation_granted", scheme_id=sid,
                  detail={"scopes": d.scopes, "hours": DELEGATION_HOURS, "expires_at": d.expires_at})
    except PortalError as e:
        log_event("browser_agent", "delegation_failed", scheme_id=sid, detail={"reason": str(e)})
    driver.close(case_id, "submitted")
    return update
