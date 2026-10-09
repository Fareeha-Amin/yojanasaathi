"""The case graph (Phase 2: agent brain).

START -> router: deterministic facts (agent/facts.py) + one LLM extraction -> intent
  interview    asks the first field in `missing` (required_fields of in-scope schemes
               still possible, minus known fields)
  eligibility  JSON Logic rules decide (agent/rules.py) for every scheme; short spoken
               reply (count, top matches, one-line reason, question); full reasons,
               source and checklists go to the screen in `ui`
  respond      LLM free-form answer to a general question (the only LLM-written reply)
  status / declined / choose_reask / already_submitted
  proceed:     prepare (read-back preview; Phase 4: planner + browser) -> confirm
               (interrupt, deterministic gate) -> submit
Submission is idempotent PER SCHEME (`applications`): a request to submit a scheme that
already has an application ID gets that ID back; everything else routes normally.
Key replies come from templates (agent/replies.py), in the case's language.
Consequential steps call audit.log_event() (case ID = the thread ID). The state is
checkpointed in Postgres (agent/db.py), so a pause and `applications` survive restarts.
"""

from dataclasses import asdict
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent import checklist as checklist_mod
from agent import rules
from agent.audit import log_event
from agent.districts import normalize_district
from agent.facts import AGE_MAX, AGE_MIN, FIELDS, NUMERIC_FIELDS, Facts, extract
from agent.gate import parse_decision
from agent.llm import Extraction, get_llm, usable
from agent.numbers import tokenize
from agent.replies import day, facts_phrase, join, readback, reason, reasons, t

# Order in which the interview asks. Must cover every required_field in rules/.
FIELD_ORDER = ["age", "annual_income"]
TOP_SPOKEN = 2  # matches named aloud; the full list goes to the screen


class CaseState(TypedDict, total=False):
    msg: str  # latest citizen message
    lang: str  # "kn" | "hi" | "en"
    profile: dict[str, Any]
    missing: list[str]
    eligible: list[dict[str, Any]]  # each with reasons, source_url, effective_date
    ineligible: list[dict[str, Any]]
    checklist: list[dict[str, Any]]  # documents of the selected scheme
    preview: dict[str, Any]  # what the citizen reviews before submit
    app_id: str  # last application ID
    status: str
    reply: str  # spoken (1-3 short sentences)
    ui: dict[str, Any] | None  # this turn's screen payload (full reasons, sources, checklists)
    # Phase 2 bookkeeping
    intent: str
    asking: str | None  # field we asked about last, or "proceed" / "choose" / "others"
    topics: list[str]  # kinds of scheme the citizen asked about; empty = all
    sources: dict[str, str]  # field -> how we know it (digits, number_words, answer, llm...)
    readback: list[str]  # fields to flag at review (LLM-sourced or converted)
    docs_have: list[str]
    docs_missing: list[str]
    docs_stored: list[str]  # types in the encrypted vault (set by /turn from Postgres)
    selected: str | None  # scheme_id being applied for
    offered: list[str]  # eligible, not yet applied, in the order we said them
    applications: dict[str, dict[str, Any]]  # scheme_id -> {"app_id", "status"}
    last_submitted: str | None
    resubmit: str | None  # scheme the citizen asked to submit again


def _lang(state: CaseState) -> str:
    return state.get("lang") or "en"


def _script_lang(text: str) -> str | None:
    kn = sum(0x0C80 <= ord(c) < 0x0D00 for c in text)
    hi = sum(0x0900 <= ord(c) < 0x0980 for c in text)
    if not kn and not hi:
        return None
    return "kn" if kn >= hi else "hi"


def _ordered(state: CaseState) -> list[dict[str, Any]]:
    """All schemes: the citizen's topic first, then priority (pension-001 first)."""
    topics = state.get("topics") or []
    return sorted(rules.load_schemes().values(),
                  key=lambda s: (bool(topics) and s["topic"] not in topics, s["priority"]))


def _scope(state: CaseState) -> list[dict[str, Any]]:
    """Schemes the interview asks questions for: the citizen's topic, else all."""
    schemes = list(rules.load_schemes().values())
    topics = state.get("topics") or []
    return [s for s in schemes if s["topic"] in topics] or schemes


def _title(sid: str, lang: str) -> str:
    return rules.title(rules.load_schemes()[sid], lang)


# --- router ----------------------------------------------------------------------------


def _llm_number(name: str, v: int, facts: Facts) -> tuple[int, str] | None:
    """An LLM number is a fallback. If the parser read a number the LLM did not place,
    keep the parser's value and only take the LLM's choice of field."""
    plausible = AGE_MIN <= v <= AGE_MAX if name == "age" else 0 <= v <= 10**9
    match = [s for s in facts.unattributed if s.value == v]
    if match:
        facts.unattributed.remove(match[0])
        return v, "llm_attributed"
    if len(facts.unattributed) == 1:
        span = facts.unattributed.pop()
        return span.value, "llm_attributed"
    return (v, "llm") if plausible else None


def _merge_llm(facts: Facts, ext: Extraction) -> None:
    for name in FIELDS:
        v = getattr(ext, name)
        if v is None or name in facts.values:
            continue  # deterministic parsers win
        if name in NUMERIC_FIELDS:
            got = _llm_number(name, v, facts)
            if got:
                facts.set(name, got[0], got[1], flag=True)
        elif name == "district":
            facts.set(name, normalize_district(v) or v.strip().title(), "llm", flag=True)
        else:
            facts.set(name, v, "llm", flag=True)
    if facts.chosen is None and ext.scheme:
        facts.chosen = ext.scheme  # read back by name before submit, so the citizen sees it


def _pick(facts: Facts, offered: list[str]) -> str | None:
    """The scheme the message picks: by name, by position in what we offered, or "next"."""
    if facts.chosen:
        return facts.chosen
    if facts.ordinal is not None and facts.ordinal < len(offered):
        return offered[facts.ordinal]
    if facts.next_one and offered:
        return offered[0]
    return None


def _intent(state: CaseState, facts: Facts, ext: Extraction | None, msg: str,
            pick: str | None) -> tuple[str, str | None]:
    """(intent, scheme it is about)."""
    asking = state.get("asking")
    applications = state.get("applications") or {}
    last = state.get("last_submitted")
    if pick:
        return ("resubmit" if pick in applications else "proceed"), pick
    bare_yes = parse_decision(msg) == "yes" and not facts.values and not facts.docs_have \
        and not facts.docs_missing
    # A stray / repeated "yes" after a submission (any channel) never starts anything new.
    if bare_yes and last and asking not in ("proceed", "others"):
        return "resubmit", last
    if asking in ("proceed", "others", "choose") and facts.proceed is not None:
        if not facts.proceed:
            return "decline", None
        return {"proceed": "proceed", "others": "others", "choose": "choose_reask"}[asking], None
    if facts.proceed:
        return "proceed", None
    if facts.status and not facts.values:
        return "status", None
    if facts.values or facts.docs_have or facts.docs_missing:
        return "info", None
    # "what documents does the health scheme need?" names a topic but is a question
    if ext is not None and ext.intent in ("question", "status", "proceed"):
        return ext.intent, None
    return "info", None


Route = Literal["interview", "respond", "status", "declined", "choose_reask", "already_submitted"]


def router(state: CaseState) -> Command[Route]:
    msg = state.get("msg", "")
    lang = state.get("lang") or _script_lang(msg)
    asking = state.get("asking")
    facts = extract(msg, asking)

    ext = None
    llm = get_llm()
    # A short, deterministically understood answer ("62", "ಹೌದು") needs no LLM call.
    if usable(llm) and not ((facts.answered or facts.picks_scheme) and len(tokenize(msg)) <= 6):
        ext = llm.extract(msg, asking)
        if ext is not None:
            _merge_llm(facts, ext)
    pick = _pick(facts, state.get("offered") or [])
    intent, about = _intent(state, facts, ext, msg, pick)

    topics = [] if intent == "others" else list(state.get("topics") or [])
    new_topics = facts.topics or ([ext.topic] if ext is not None and ext.topic else [])
    topics += [x for x in new_topics if x not in topics]
    have = [d for d in state.get("docs_have", []) if d not in facts.docs_missing]
    have += [d for d in facts.docs_have if d not in have]
    missing_docs = [d for d in state.get("docs_missing", []) if d not in facts.docs_have]
    missing_docs += [d for d in facts.docs_missing if d not in missing_docs]

    update: CaseState = {
        "profile": {**state.get("profile", {}), **facts.values},
        "sources": {**state.get("sources", {}), **facts.sources},
        "readback": sorted((set(state.get("readback", [])) - set(facts.values)) | facts.readback),
        "topics": topics,
        "docs_have": have,
        "docs_missing": missing_docs,
        "intent": "info" if intent == "others" else intent,
        "ui": None,
        "resubmit": about if intent == "resubmit" else None,
    }
    if intent == "proceed" and about:
        update["selected"] = about
    if lang:
        update["lang"] = lang
    goto = {"question": "respond", "status": "status", "decline": "declined",
            "choose_reask": "choose_reask", "resubmit": "already_submitted"}.get(intent, "interview")
    return Command(goto=goto, update=update)


# --- interview -> eligibility ----------------------------------------------------------


def interview(state: CaseState) -> Command[Literal["eligibility", "__end__"]]:
    profile = state.get("profile", {})
    scope = _scope(state)
    sel = state.get("selected")
    if state.get("intent") == "proceed" and sel and sel not in (s["scheme_id"] for s in scope):
        scope = [*scope, rules.load_schemes()[sel]]  # a scheme picked by name is checked too
    needed: set[str] = set()
    for scheme in scope:
        if rules.status(scheme, profile) == "unknown":
            needed |= set(rules.missing_fields(scheme, profile))
    missing = [f for f in FIELD_ORDER if f in needed]
    if not missing:
        return Command(goto="eligibility", update={"missing": []})
    lang = _lang(state)
    reply = t(f"ask_{missing[0]}", lang)
    if not profile and not state.get("topics"):
        reply = f"{t('greeting', lang)} {reply}"
    return Command(goto=END, update={"missing": missing, "asking": missing[0], "reply": reply,
                                     "status": "collecting"})


def _result(scheme: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme_id": scheme["scheme_id"],
        "title": scheme["title"],
        "reasons": [asdict(c) for c in rules.clauses(scheme["rule"], profile)],
        "source_url": scheme["source_url"],
        "effective_date": scheme["effective_date"],
        "verification": scheme["verification"],
    }


def _checklist(scheme: dict[str, Any], state: CaseState, lang: str) -> list[dict[str, str]]:
    items = checklist_mod.build(scheme, state.get("profile", {}), state.get("docs_have"),
                                state.get("docs_missing"), state.get("docs_stored"))
    return [{**i, "label": rules.doc_label(scheme, i["doc"], lang)} for i in items]


def _screen(state: CaseState, ordered: list[dict[str, Any]], lang: str) -> dict[str, Any]:
    """The Schemes screen: every scheme with status, full reasons, source, date, checklist."""
    profile = state.get("profile", {})
    applications = state.get("applications") or {}
    out = []
    for s in ordered:
        cs = rules.clauses(s["rule"], profile)
        out.append({
            "scheme_id": s["scheme_id"],
            "title": rules.title(s, lang),
            "status": rules.status(s, profile),
            "reasons": [reason(c, lang) for c in cs if c.result is not None],
            "missing_fields": rules.missing_fields(s, profile),
            "clauses": [asdict(c) for c in cs],
            "source_url": s["source_url"],
            "effective_date": s["effective_date"],
            "effective_date_text": day(s["effective_date"], lang),
            "verification": s["verification"],
            "documents": _checklist(s, state, lang),
            "app_id": applications.get(s["scheme_id"], {}).get("app_id"),
        })
    return {"type": "eligibility", "lang": lang, "schemes": out}


def _known(fields: list[str], profile: dict[str, Any]) -> dict[str, Any]:
    return {f: profile[f] for f in FIELD_ORDER if f in fields and f in profile}


def _ids(results: list[dict[str, Any]] | None) -> list[str]:
    return [r["scheme_id"] for r in results or []]


def _audit_decision(state: CaseState, eligible: list[dict[str, Any]],
                    ineligible: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    """Logged when the rules' decision changes. Field names, not values: the audit log
    outlives "delete my data"."""
    if (_ids(eligible), _ids(ineligible)) == (_ids(state.get("eligible")), _ids(state.get("ineligible"))):
        return
    schemes = rules.load_schemes()
    log_event("agent", "eligibility_decided", detail={
        "eligible": _ids(eligible), "not_eligible": _ids(ineligible),
        "based_on": sorted(f for f in profile if f in FIELD_ORDER),
        "rules": {sid: schemes[sid]["effective_date"] for sid in _ids(eligible) + _ids(ineligible)},
        "decided_by": "json_logic"})


def eligibility(state: CaseState) -> Command[Literal["prepare", "__end__"]]:
    lang = _lang(state)
    profile = state.get("profile", {})
    applications = state.get("applications") or {}
    ordered = _ordered(state)
    status = {s["scheme_id"]: rules.status(s, profile) for s in ordered}
    eligible = [_result(s, profile) for s in ordered if status[s["scheme_id"]] == "eligible"]
    ineligible = [_result(s, profile) for s in ordered if status[s["scheme_id"]] == "not_eligible"]
    offered = [r["scheme_id"] for r in eligible if r["scheme_id"] not in applications]
    update: CaseState = {"eligible": eligible, "ineligible": ineligible, "offered": offered,
                         "ui": _screen(state, ordered, lang)}
    schemes = rules.load_schemes()
    _audit_decision(state, eligible, ineligible, profile)

    if state.get("intent") == "proceed":
        sel = state.get("selected")
        if sel and sel in offered:
            return Command(goto="prepare", update={**update, "selected": sel})
        if sel and status.get(sel) == "not_eligible" and sel not in applications:
            cs = [c for c in rules.clauses(schemes[sel]["rule"], profile) if c.result is False]
            return Command(goto=END, update={**update, "asking": None, "reply": t(
                "not_eligible_one", lang, title=_title(sel, lang), reasons=reasons(cs, lang))})
        if len(offered) == 1:
            return Command(goto="prepare", update={**update, "selected": offered[0]})

    if offered:
        fields: list[str] = []
        for sid in offered:
            fields += schemes[sid]["required_fields"]
        facts = facts_phrase(_known(fields, profile), lang)
        if len(offered) == 1:
            sid = offered[0]
            if applications:
                reply = t("match_more_one", lang, title=_title(sid, lang))
            else:
                reply = t("match_one", lang, title=_title(sid, lang), facts=facts,
                          n=len(schemes[sid]["documents"]))
            return Command(goto=END, update={**update, "reply": reply, "asking": "proceed",
                                             "selected": sid, "status": "eligible"})
        top = join([_title(sid, lang) for sid in offered[:TOP_SPOKEN]], lang)
        key = "match_more_many" if applications else "match_many"
        reply = t(key, lang, n=len(offered), facts=facts, top=top)
        return Command(goto=END, update={**update, "reply": reply, "asking": "choose",
                                         "status": "eligible"})
    if eligible:
        return Command(goto=END, update={**update, "reply": t("all_applied", lang), "asking": None})

    scope_ids = {s["scheme_id"] for s in _scope(state)}
    failed: list[str] = []
    for r in ineligible:
        if r["scheme_id"] in scope_ids:
            for c in r["reasons"]:
                text = reason(rules.Clause(**c), lang)
                if c["result"] is False and text not in failed:
                    failed.append(text)
    why = join(failed, lang)
    if len(scope_ids) < len(schemes):
        topic = join([t(f"topic_{x}", lang) for x in state.get("topics") or []], lang)
        return Command(goto=END, update={**update, "asking": "others", "status": "not_eligible",
                                         "reply": t("not_eligible_topic", lang, topic=topic, reasons=why)})
    return Command(goto=END, update={**update, "asking": None, "status": "not_eligible",
                                     "reply": t("not_eligible_all", lang, reasons=why)})


# --- free-form, status, decline, re-ask --------------------------------------------------


def _kb() -> str:
    lines = []
    for s in rules.load_schemes().values():
        docs = ", ".join(d["label"]["en"] for d in s["documents"])
        lines.append(f"- {s['title']} ({s['scheme_id']}, demo portal scheme): rules "
                     f"{s['portal_rules']}. Documents: {docs}.")
    return "\n".join(lines)


def _reask(state: CaseState, lang: str) -> str:
    asking = state.get("asking")
    offered = state.get("offered") or []
    if asking == "proceed" and state.get("selected"):
        return t("ask_start", lang, title=_title(state["selected"], lang))
    if asking == "choose" and offered:
        return t("choose_reask", lang, title=_title(offered[0], lang))
    if asking == "others":
        return t("ask_others", lang)
    return t(f"ask_{asking}", lang) if asking in FIELD_ORDER else ""


def respond(state: CaseState) -> CaseState:
    lang = _lang(state)
    llm = get_llm()
    answer = None
    if usable(llm):
        answer = llm.answer(state.get("msg", ""), lang, _kb(), state.get("profile", {}))
    reply = " ".join(p for p in (answer or t("llm_unavailable", lang), _reask(state, lang)) if p)
    return {"reply": reply}


def status(state: CaseState) -> CaseState:
    lang = _lang(state)
    applications = state.get("applications") or {}
    if applications:
        parts = [t("status_app", lang, title=_title(sid, lang), app_id=a["app_id"],
                   status=t(f"status_{a['status']}", lang))
                 for sid, a in list(applications.items())[-TOP_SPOKEN:]]
        return {"reply": " ".join(parts)}
    return {"reply": " ".join(p for p in (t("status_none", lang), _reask(state, lang)) if p)}


def declined(state: CaseState) -> CaseState:
    return {"reply": t("proceed_declined", _lang(state)), "asking": None}


def choose_reask(state: CaseState) -> CaseState:
    return {"reply": _reask(state, _lang(state))}


def already_submitted(state: CaseState) -> CaseState:
    sid = state["resubmit"]
    app = state["applications"][sid]
    _blocked(sid, app["app_id"], "router")
    return {"reply": t("already_submitted", _lang(state), app_id=app["app_id"])}


def _blocked(sid: str, app_id: str, where: str) -> None:
    log_event("agent", "resubmit_blocked", scheme_id=sid, detail={"app_id": app_id, "guard": where})


# --- proceed: prepare -> confirm (human gate) -> submit ------------------------------


def prepare(state: CaseState) -> Command[Literal["confirm", "__end__"]]:
    # TODO (Phase 4): planner + Playwright pre-fill on the mock portal (with OTP pause)
    lang = _lang(state)
    sel = state["selected"]
    applications = state.get("applications") or {}
    if sel in applications:  # never prepare a second application for the same scheme
        _blocked(sel, applications[sel]["app_id"], "prepare")
        return Command(goto=END, update={"reply": t("already_submitted", lang,
                                                    app_id=applications[sel]["app_id"])})
    scheme = rules.load_schemes()[sel]
    profile = state.get("profile", {})
    fields = {f: profile[f] for f in scheme["required_fields"] if f in profile}
    unsure = [f for f in fields if f in state.get("readback", [])]
    parts = [t("readback", lang, fields=readback(fields, lang))]
    if unsure:
        parts.append(t("readback_unsure", lang, fields=readback({f: fields[f] for f in unsure}, lang)))
    parts.append(t("confirm_ask", lang, title=rules.title(scheme, lang)))
    docs = _checklist(scheme, state, lang)
    preview = {
        "scheme_id": sel,
        "title": rules.title(scheme, lang),
        "fields": fields,
        "needs_readback": unsure,
        "documents": docs,
        "source_url": scheme["source_url"],
        "effective_date": scheme["effective_date"],
    }
    # Logged here, not in confirm: an interrupted node re-runs from the top on resume.
    log_event("agent", "confirm_requested", scheme_id=sel, detail={
        "fields": sorted(fields), "needs_readback": unsure, "documents": [d["doc"] for d in docs]})
    return Command(goto="confirm", update={
        "preview": preview, "checklist": docs, "status": "awaiting_confirmation",
        "asking": None, "reply": " ".join(parts)})


def confirm(state: CaseState) -> Command[Literal["submit", "confirm", "__end__"]]:
    # Human gate: the graph stops here until /turn is called again. Only an explicit
    # yes reaches submit; anything unclear asks again, never submits.
    answer = interrupt({"type": "confirm", "preview": state.get("preview", {})})
    decision = parse_decision(str(answer))
    lang = _lang(state)
    detail = {"decision": decision}
    if decision != "unclear" and len(tokenize(str(answer))) <= 3:
        detail["said"] = str(answer).strip()  # the bare "ಹೌದು" / "no": evidence, nothing personal
    action = {"yes": "citizen_approved", "no": "citizen_declined"}.get(decision, "confirm_unclear")
    log_event("citizen", action, scheme_id=state.get("selected"), detail=detail)
    if decision == "yes":
        return Command(goto="submit")
    if decision == "no":
        return Command(goto=END, update={"status": "cancelled", "reply": t("cancelled", lang)})
    return Command(goto="confirm", update={"reply": t("confirm_reask", lang)})


def _submit_to_portal(state: CaseState) -> str:
    # TODO (Phase 4): click the portal's final Submit via Playwright, return the real
    # application number (portal format YJS-XXXXXXXXXX)
    return f"DEMO-{len(state.get('applications') or {}) + 1:04d}"


def submit(state: CaseState) -> CaseState:
    # Last line of defence right before the portal: never submit a scheme twice.
    lang = _lang(state)
    sel = state["selected"]
    applications = dict(state.get("applications") or {})
    if sel in applications:
        _blocked(sel, applications[sel]["app_id"], "submit")
        return {"reply": t("already_submitted", lang, app_id=applications[sel]["app_id"])}
    app_id = _submit_to_portal(state)
    applications[sel] = {"app_id": app_id, "status": "SUBMITTED"}
    log_event("agent", "submitted", scheme_id=sel, detail={"app_id": app_id, "portal": "stub (Phase 4: Playwright)"})
    remaining = [s for s in state.get("offered") or [] if s not in applications]
    parts = [t("submitted", lang, app_id=app_id)]
    if remaining:
        parts.append(t("submitted_next", lang, title=_title(remaining[0], lang)))
    return {
        "applications": applications, "app_id": app_id, "last_submitted": sel,
        "offered": remaining, "asking": "choose" if remaining else None, "status": "submitted",
        "reply": " ".join(parts),
        "ui": {"type": "submitted", "scheme_id": sel, "title": _title(sel, lang), "app_id": app_id,
               "next": [{"scheme_id": s, "title": _title(s, lang)} for s in remaining]},
    }


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(CaseState)
    for name, fn in [("router", router), ("interview", interview), ("eligibility", eligibility),
                     ("respond", respond), ("status", status), ("declined", declined),
                     ("choose_reask", choose_reask), ("already_submitted", already_submitted),
                     ("prepare", prepare), ("confirm", confirm), ("submit", submit)]:
        g.add_node(name, fn)
    g.add_edge(START, "router")
    for name in ("respond", "status", "declined", "choose_reask", "already_submitted", "submit"):
        g.add_edge(name, END)
    return g.compile(checkpointer=checkpointer)
