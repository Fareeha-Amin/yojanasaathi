"""FastAPI app: the one contract every channel uses (web, voice, phone).

POST /turn/{case_id}  {"text": "...", "lang"?: "kn"|"hi"|"en"}
  -> {"reply": "...", "pause": null | {"type": ..., ...}, "ui": null | {"type": ..., ...}}
"reply" is what gets spoken (short); "ui" is this turn's screen payload (full reasons,
sources, checklists). Clients that only read reply/pause keep working.
If the case's graph is paused at an interrupt, the text resumes it (Command(resume=text)).
"lang" is optional (voice passes the STT-detected language); when given it updates the case.
"""

import logging
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from agent import config
from agent.graph import build_graph
from agent.llm import get_llm

_log = logging.getLogger("yojanasaathi")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# TODO (Phase 3): swap for the Postgres checkpointer = durable case memory
graph = build_graph(InMemorySaver())

# One turn at a time per case: a double-send (e.g. voice barge-in) must not race
# between reading "is it paused?" and resuming.
_case_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the local model once at startup and keep it loaded (keep_alive=-1). Runs in the
    # background: /turn answers deterministically until the model is ready.
    if config.LLM_WARMUP:
        threading.Thread(target=get_llm().warmup, name="llm-warmup", daemon=True).start()
    yield


app = FastAPI(title="YojanaSaathi agent", lifespan=lifespan)
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
    ui: dict | None = None


@app.post("/turn/{case_id}", response_model=TurnOut)
def turn(case_id: str, m: TurnIn) -> TurnOut:
    cfg = {"configurable": {"thread_id": case_id}}
    with _case_locks[case_id]:
        paused = bool(graph.get_state(cfg).interrupts)
        lang = {"lang": m.lang} if m.lang else {}
        # ui is per turn: a resume skips the router, so clear the previous turn's payload here
        inp = Command(resume=m.text, update={**lang, "ui": None}) if paused else {"msg": m.text, **lang}
        out = graph.invoke(inp, cfg, version="v2")
    return TurnOut(
        reply=out.value.get("reply", ""),
        pause=out.interrupts[0].value if out.interrupts else None,
        ui=out.value.get("ui"),
    )


@app.get("/health")
def health() -> dict:
    # llm: off | cold | warming | ready | error ("ok" is about the agent, not the LLM)
    return {"ok": True, "llm": get_llm().state}
