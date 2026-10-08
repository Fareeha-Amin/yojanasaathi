"""The case graph. Phase 0: stub interview -> confirm (human gate) -> submit.
A submitted case (has app_id) routes straight to already_submitted: submission is idempotent.

Target graph (CLAUDE.md): router -> interview -> eligibility -> document -> respond;
proceed: planner -> browser -> confirm -> submit -> track. Replace stubs one by one.
"""

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.gate import parse_decision


class CaseState(TypedDict, total=False):
    msg: str  # latest citizen message
    lang: str  # "kn" | "hi" | "en"
    profile: dict[str, Any]
    missing: list[str]
    eligible: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    preview: dict[str, Any]  # what the citizen reviews before submit
    app_id: str
    status: str
    reply: str


def interview(state: CaseState) -> CaseState:
    # TODO (Phase 2): LLM structured extraction -> profile, missing fields
    return {
        "preview": {"scheme_id": "pension-001", "summary": "Demo form (stub)"},
        "status": "awaiting_confirmation",
        "reply": f"You said: '{state['msg']}'. Your form is ready. Shall I submit it?",
    }


def confirm(state: CaseState) -> Command[Literal["submit", "confirm", "__end__"]]:
    # Human gate: the graph stops here until /turn is called again. Only an explicit
    # yes reaches submit; anything unclear asks again, never submits.
    answer = interrupt({"type": "confirm", "preview": state.get("preview", {})})
    decision = parse_decision(str(answer))
    if decision == "yes":
        return Command(goto="submit")
    if decision == "no":
        return Command(goto=END, update={"status": "cancelled", "reply": "Okay, nothing was submitted."})
    return Command(goto="confirm", update={"reply": "Please say yes to submit, or no to stop."})


def _already_submitted(app_id: str) -> str:
    return f"Already submitted. Your application ID is {app_id}."


def route_entry(state: CaseState) -> Literal["interview", "already_submitted"]:
    # Idempotent submission: a submitted case never goes back through the form and the
    # gate, so a later "yes" (any channel) has nothing to approve.
    return "already_submitted" if state.get("app_id") else "interview"


def already_submitted(state: CaseState) -> CaseState:
    return {"reply": _already_submitted(state["app_id"])}


def _submit_to_portal(state: CaseState) -> str:
    # TODO (Phase 4): click the portal's final Submit via Playwright, return the real app ID
    return "DEMO-0001"


def submit(state: CaseState) -> CaseState:
    # Last line of defence right before the portal: never submit a case twice.
    if state.get("app_id"):
        return {"reply": _already_submitted(state["app_id"])}
    app_id = _submit_to_portal(state)
    return {"app_id": app_id, "status": "submitted", "reply": f"Submitted! Application ID {app_id}."}


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(CaseState)
    g.add_node("interview", interview)
    g.add_node("confirm", confirm)
    g.add_node("submit", submit)
    g.add_node("already_submitted", already_submitted)
    g.add_conditional_edges(START, route_entry)
    g.add_edge("already_submitted", END)
    g.add_edge("interview", "confirm")
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer)
