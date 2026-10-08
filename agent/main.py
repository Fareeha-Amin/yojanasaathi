"""FastAPI app: the one contract every channel uses (web, voice, phone).

POST /turn/{case_id}  {"text": "...", "lang"?: "kn"|"hi"|"en"}
  -> {"reply": "...", "pause": null | {"type": ..., ...}}
If the case's graph is paused at an interrupt, the text resumes it (Command(resume=text)).
"lang" is optional (voice passes the STT-detected language); when given it updates the case.
"""

import threading
from collections import defaultdict
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from agent import config
from agent.graph import build_graph

# TODO (Phase 3): swap for the Postgres checkpointer = durable case memory
graph = build_graph(InMemorySaver())

# One turn at a time per case: a double-send (e.g. voice barge-in) must not race
# between reading "is it paused?" and resuming.
_case_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

app = FastAPI(title="YojanaSaathi agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TurnIn(BaseModel):
    text: str
    lang: Literal["kn", "hi", "en"] | None = None


class TurnOut(BaseModel):
    reply: str
    pause: dict | None = None


@app.post("/turn/{case_id}", response_model=TurnOut)
def turn(case_id: str, m: TurnIn) -> TurnOut:
    cfg = {"configurable": {"thread_id": case_id}}
    with _case_locks[case_id]:
        paused = bool(graph.get_state(cfg).interrupts)
        lang = {"lang": m.lang} if m.lang else {}
        inp = Command(resume=m.text, update=lang or None) if paused else {"msg": m.text, **lang}
        out = graph.invoke(inp, cfg, version="v2")
    return TurnOut(
        reply=out.value.get("reply", ""),
        pause=out.interrupts[0].value if out.interrupts else None,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}
