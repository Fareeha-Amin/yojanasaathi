"""The case graph. Phase 0: stub interview -> confirm (human gate) -> submit.

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


def submit(state: CaseState) -> CaseState:
    # TODO (Phase 4): click the portal's final Submit via Playwright, store the real app ID
    app_id = "DEMO-0001"
    return {"app_id": app_id, "status": "submitted", "reply": f"Submitted! Application ID {app_id}."}


def build_graph(checkpointer: BaseCheckpointSaver):
    g = StateGraph(CaseState)
    g.add_node("interview", interview)
    g.add_node("confirm", confirm)
    g.add_node("submit", submit)
    g.add_edge(START, "interview")
    g.add_edge("interview", "confirm")
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer)
