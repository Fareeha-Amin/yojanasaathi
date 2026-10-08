"""The case graph (Phase 2: agent brain).

START -> route_entry: a submitted case (has app_id) -> already_submitted (idempotent).
Otherwise:
  router      deterministic facts (agent/facts.py) + one LLM extraction -> intent
  interview   asks the first field in `missing` (union of required_fields of in-scope
              schemes still possible, minus known fields)
  eligibility JSON Logic rules decide (agent/rules.py); reasons + source + date
  document    personal checklist; reply = result + checklist + "shall I fill the form?"
  respond     LLM free-form answer to a general question (the only LLM-written reply)
  status / declined
  proceed:    prepare (read-back preview; Phase 4: planner + browser) -> confirm
              (interrupt, deterministic gate) -> submit
Key replies come from templates (agent/replies.py), in the case's language.
"""

from dataclasses import asdict
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent import checklist as checklist_mod
from agent import rules
from agent.districts import normalize_district
from agent.facts import AGE_MAX, AGE_MIN, FIELDS, NUMERIC_FIELDS, Facts, extract
from agent.gate import parse_decision
from agent.llm import Extraction, get_llm, usable
from agent.numbers import tokenize
from agent.replies import day, doc, join, readback, reasons, t

# Order in which the interview asks: cheap, scheme-eliminating questions first.
FIELD_ORDER = ["age", "annual_income", "gender", "is_student", "category", "owns_farmland",
               "district", "is_family_head", "pays_income_tax", "govt_job_or_big_pension"]
assert set(FIELD_ORDER) == set(FIELDS)


class CaseState(TypedDict, total=False):
    msg: str  # latest citizen message
    lang: str  # "kn" | "hi" | "en"
    profile: dict[str, Any]
    missing: list[str]
    eligible: list[dict[str, Any]]  # each with reasons, source_url, effective_date
    ineligible: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    preview: dict[str, Any]  # what the citizen reviews before submit
    app_id: str
    status: str
    reply: str
    # Phase 2 bookkeeping
    intent: str
    asking: str | None  # field we asked about last, or "proceed"
    topics: list[str]  # kinds of scheme the citizen asked about; empty = all
    sources: dict[str, str]  # field -> how we know it (digits, number_words, answer, llm...)
    readback: list[str]  # fields to flag at review (LLM-sourced or converted)
    docs_have: list[str]
    docs_missing: list[str]
    selected: str | None  # scheme_id the application is for


def _lang(state: CaseState) -> str:
    return state.get("lang") or "en"


def _script_lang(text: str) -> str | None:
    kn = sum(0x0C80 <= ord(c) < 0x0D00 for c in text)
    hi = sum(0x0900 <= ord(c) < 0x0980 for c in text)
    if not kn and not hi:
        return None
    return "kn" if kn >= hi else "hi"


def _scope(state: CaseState) -> list[dict[str, Any]]:
    schemes = list(rules.load_schemes().values())
    topics = state.get("topics") or []
    scoped = [s for s in schemes if s.get("topic") in topics]
    return scoped or schemes


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


def _intent(facts: Facts, ext: Extraction | None, asking: str | None) -> str:
    if asking == "proceed" and facts.proceed is not None:
        return "proceed" if facts.proceed else "decline"
    if facts.proceed:
        return "proceed"
    if facts.status and not facts.values:
        return "status"
    if facts.values or facts.topics or facts.docs_have or facts.docs_missing:
        return "info"
    if ext is not None:
        if ext.intent == "proceed" and asking == "proceed":
            return "proceed"
        if ext.intent in ("question", "status", "proceed"):
            return ext.intent
    return "info"


def router(state: CaseState) -> Command[Literal["interview", "respond", "status", "declined"]]:
    msg = state.get("msg", "")
    lang = state.get("lang") or _script_lang(msg)
    asking = state.get("asking")
    facts = extract(msg, asking)

    ext = None
    llm = get_llm()
    # A short, deterministically understood answer ("62", "ಹೌದು") needs no LLM call.
    if usable(llm) and not (facts.answered and len(tokenize(msg)) <= 6):
        ext = llm.extract(msg, asking)
        if ext is not None:
            _merge_llm(facts, ext)
    intent = _intent(facts, ext, asking)

    topics = list(state.get("topics") or [])
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
        "intent": intent,
    }
    if lang:
        update["lang"] = lang
    goto = {"question": "respond", "status": "status", "decline": "declined"}.get(intent, "interview")
    return Command(goto=goto, update=update)


# --- interview -> eligibility -> document --------------------------------------------


def interview(state: CaseState) -> Command[Literal["eligibility", "__end__"]]:
    profile = state.get("profile", {})
    needed: set[str] = set()
    for scheme in _scope(state):
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
        "verification": scheme.get("verification", ""),
    }


def _reasons(result: dict[str, Any], lang: str, only_failed: bool = False) -> str:
    cs = [rules.Clause(**c) for c in result["reasons"]]
    if only_failed:
        cs = [c for c in cs if c.result is False]
    return reasons(cs, lang)


def eligibility(state: CaseState) -> Command[Literal["document", "__end__"]]:
    profile = state.get("profile", {})
    scope = _scope(state)
    eligible = [_result(s, profile) for s in scope if rules.status(s, profile) == "eligible"]
    ineligible = [_result(s, profile) for s in scope if rules.status(s, profile) == "not_eligible"]
    update: CaseState = {"eligible": eligible, "ineligible": ineligible}
    if eligible:
        return Command(goto="document", update={**update, "selected": eligible[0]["scheme_id"]})

    lang = _lang(state)
    schemes = rules.load_schemes()
    parts = [t("not_eligible", lang, title=rules.title(schemes[r["scheme_id"]], lang),
               reasons=_reasons(r, lang, only_failed=True)) for r in ineligible[:2]]
    if len(scope) < len(schemes):
        parts.append(t("check_others", lang))
    return Command(goto=END, update={**update, "reply": " ".join(parts), "asking": None,
                                     "status": "not_eligible", "selected": None})


def _checklist_text(items: list[dict[str, str]], lang: str) -> str:
    n = len(items)
    have = [i for i in items if i["status"] == "have"]
    still = [doc(i["doc"], lang) for i in items if i["status"] != "have"]
    if not still:
        return t("checklist_all", lang, n=n)
    if have or any(i["status"] == "missing" for i in items):
        return t("checklist_have", lang, n=n, have=len(have), missing=join(still, lang))
    return t("checklist", lang, n=n, docs=join(still, lang))


def document(state: CaseState) -> Command[Literal["prepare", "__end__"]]:
    lang = _lang(state)
    schemes = rules.load_schemes()
    first = state["eligible"][0]
    scheme = schemes[first["scheme_id"]]
    items = checklist_mod.build(scheme, state.get("profile", {}), state.get("docs_have"),
                                state.get("docs_missing"))
    if state.get("intent") == "proceed":
        return Command(goto="prepare", update={"checklist": items})

    parts = [t("eligible", lang, title=rules.title(scheme, lang), reasons=_reasons(first, lang),
               source=rules.source_name(scheme, lang), date=day(first["effective_date"], lang))]
    others = [rules.title(schemes[r["scheme_id"]], lang) for r in state["eligible"][1:]]
    if others:
        parts.append(t("eligible_more", lang, titles=join(others, lang)))
    parts += [_checklist_text(items, lang), t("ask_proceed", lang)]
    return Command(goto=END, update={"checklist": items, "reply": " ".join(parts),
                                     "asking": "proceed", "status": "eligible"})


# --- free-form, status, decline --------------------------------------------------------


def _kb() -> str:
    lines = []
    for s in rules.load_schemes().values():
        docs = ", ".join(d["doc"] for d in s["documents"])
        lines.append(f"- {s['title']}: {s.get('benefit', '')} Needs: "
                     f"{', '.join(s['required_fields'])}. Documents: {docs}.")
    return "\n".join(lines)


def _reask(state: CaseState, lang: str) -> str:
    asking = state.get("asking")
    if asking == "proceed":
        return t("ask_proceed", lang)
    return t(f"ask_{asking}", lang) if asking in FIELDS else ""


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
    sel = state.get("selected")
    if sel and state.get("eligible"):
        title = rules.title(rules.load_schemes()[sel], lang)
        return {"reply": t("status_ready", lang, title=title), "asking": "proceed"}
    return {"reply": " ".join(p for p in (t("status_none", lang), _reask(state, lang)) if p)}


def declined(state: CaseState) -> CaseState:
    return {"reply": t("proceed_declined", _lang(state)), "asking": None}


# --- proceed: prepare -> confirm (human gate) -> submit ------------------------------


def prepare(state: CaseState) -> CaseState:
    # TODO (Phase 4): planner + Playwright pre-fill on the mock portal (with OTP pause)
    lang = _lang(state)
    scheme = rules.load_schemes()[state["selected"]]
    profile = state.get("profile", {})
    fields = {f: profile[f] for f in scheme["required_fields"] if f in profile}
    unsure = [f for f in fields if f in state.get("readback", [])]
    parts = [t("readback", lang, fields=readback(fields, lang))]
    if unsure:
        parts.append(t("readback_unsure", lang, fields=readback({f: fields[f] for f in unsure}, lang)))
    parts.append(t("confirm_ask", lang, title=rules.title(scheme, lang)))
    preview = {
        "scheme_id": scheme["scheme_id"],
        "title": scheme["title"],
        "fields": fields,
        "needs_readback": unsure,
        "documents": state.get("checklist", []),
        "source_url": scheme["source_url"],
        "effective_date": scheme["effective_date"],
    }
    return {"preview": preview, "status": "awaiting_confirmation", "asking": None,
            "reply": " ".join(parts)}


def confirm(state: CaseState) -> Command[Literal["submit", "confirm", "__end__"]]:
    # Human gate: the graph stops here until /turn is called again. Only an explicit
    # yes reaches submit; anything unclear asks again, never submits.
    answer = interrupt({"type": "confirm", "preview": state.get("preview", {})})
    decision = parse_decision(str(answer))
    lang = _lang(state)
    if decision == "yes":
        return Command(goto="submit")
    if decision == "no":
        return Command(goto=END, update={"status": "cancelled", "reply": t("cancelled", lang)})
    return Command(goto="confirm", update={"reply": t("confirm_reask", lang)})


def _already_submitted(app_id: str, lang: str) -> str:
    return t("already_submitted", lang, app_id=app_id)


def route_entry(state: CaseState) -> Literal["router", "already_submitted"]:
    # Idempotent submission: a submitted case never goes back through the form and the
    # gate, so a later "yes" (any channel) has nothing to approve.
    return "already_submitted" if state.get("app_id") else "router"


def already_submitted(state: CaseState) -> CaseState:
    lang = state.get("lang") or _script_lang(state.get("msg", "")) or "en"
    return {"reply": _already_submitted(state["app_id"], lang)}


def _submit_to_portal(state: CaseState) -> str:
    # TODO (Phase 4): click the portal's final Submit via Playwright, return the real app ID
    return "DEMO-0001"


def submit(state: CaseState) -> CaseState:
    # Last line of defence right before the portal: never submit a case twice.
    lang = _lang(state)
    if state.get("app_id"):
        return {"reply": _already_submitted(state["app_id"], lang)}
    app_id = _submit_to_portal(state)
    return {"app_id": app_id, "status": "submitted", "reply": t("submitted", lang, app_id=app_id)}


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(CaseState)
    for name, fn in [("router", router), ("interview", interview), ("eligibility", eligibility),
                     ("document", document), ("respond", respond), ("status", status),
                     ("declined", declined), ("prepare", prepare), ("confirm", confirm),
                     ("submit", submit), ("already_submitted", already_submitted)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, route_entry)
    for name in ("respond", "status", "declined", "submit", "already_submitted"):
        g.add_edge(name, END)
    g.add_edge("prepare", "confirm")
    return g.compile(checkpointer=checkpointer)
