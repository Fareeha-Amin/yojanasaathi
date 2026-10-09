"""FastAPI app: the one contract every channel uses (web, voice, phone).

POST /turn/{case_id}  {"text": "...", "lang"?: "kn"|"hi"|"en"}
  -> {"reply": "...", "pause": null | {"type": ..., ...}, "ui": null | {"type": ..., ...}}
"reply" is what gets spoken (short); "ui" is this turn's screen payload (full reasons,
sources, checklists). Clients that only read reply/pause keep working.
If the case's graph is paused at an interrupt, the text resumes it (Command(resume=text)).
"lang" is optional (voice passes the STT-detected language); when given it updates the case.

Phase 3: case memory is the Postgres checkpointer (one thread per case), so a pause and
the per-scheme `applications` survive restarts. Startup fails clearly without Postgres or
MASTER_KEY. Aadhaar numbers are masked before the graph sees the text.
Citizen data endpoints (consent, documents, view / delete my data) are below /cases.
"""

import logging
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent import audit, config, db, privacy, rules
from agent.graph import build_graph
from agent.llm import get_llm
from agent.vault import Vault, load_master_key, public

_log = logging.getLogger("yojanasaathi")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

# Fails here, with a message that says what to fix, if MASTER_KEY or Postgres is not usable.
_master_key = load_master_key(config.MASTER_KEY)
store = db.open_store(config.DATABASE_URL)
vault = Vault(store, config.VAULT_DIR, _master_key)
audit.set_store(store)
graph = build_graph(store.checkpointer)

# One turn at a time per case: a double-send (e.g. voice barge-in) must not race
# between reading "is it paused?" and resuming. (One agent process; see CLAUDE.md.)
_case_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

DOC_TYPES = sorted({d["doc"] for s in rules.load_schemes().values() for d in s["documents"]})
CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


def _purge_loop(stop: threading.Event) -> None:
    while not stop.wait(config.VAULT_PURGE_SECONDS):
        try:
            vault.purge_expired()
        except Exception as e:  # keep the loop alive; the next run retries
            _log.error("vault purge failed: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.log_event("system", "agent_started", detail={"case_memory": "postgres",
                                                       "database": db.redact(config.DATABASE_URL)})
    vault.purge_expired()
    stop = threading.Event()
    threading.Thread(target=_purge_loop, args=(stop,), name="vault-purge", daemon=True).start()
    # Load the local model once at startup and keep it loaded (keep_alive=-1). Runs in the
    # background: /turn answers deterministically until the model is ready.
    if config.LLM_WARMUP:
        threading.Thread(target=get_llm().warmup, name="llm-warmup", daemon=True).start()
    yield
    stop.set()
    store.close()


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


def _cfg(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


@app.post("/turn/{case_id}", response_model=TurnOut)
def turn(case_id: str, m: TurnIn) -> TurnOut:
    cfg = _cfg(case_id)
    # Before anything stores or forwards the text (checkpoint, LLM, logs).
    text, aadhaar = privacy.mask_aadhaar(m.text, privacy.HIDDEN)
    with _case_locks[case_id]:
        store.ensure_case(case_id)
        if aadhaar:
            audit.log_event("agent", "aadhaar_masked", case_id=case_id,
                            detail={"aadhaar_last4": aadhaar})
        paused = bool(graph.get_state(cfg).interrupts)
        update = {"docs_stored": store.document_types(case_id)}
        if m.lang:
            update["lang"] = m.lang
        # ui is per turn: a resume skips the router, so clear the previous turn's payload here
        inp = Command(resume=text, update={**update, "ui": None}) if paused else {"msg": text, **update}
        # durability="sync": each step's checkpoint is written before the next step runs
        out = graph.invoke(inp, cfg, version="v2", durability="sync")
        pause = out.interrupts[0].value if out.interrupts else None
        store.record_turn(case_id, out.value, pause, resumed=paused)
    return TurnOut(reply=out.value.get("reply", ""), pause=pause, ui=out.value.get("ui"))


@app.get("/health")
def health() -> dict:
    # llm: off | cold | warming | ready | error ("ok" is about the agent, not the LLM)
    try:
        with store.pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        dbs = "ok"
    except Exception:
        dbs = "error"
    return {"ok": dbs == "ok", "llm": get_llm().state, "db": dbs}


# --- the citizen's data: consent, documents, view / delete ------------------------------
# Keyed by case ID like /turn (no login yet: JWT comes with the web app, Phase 5). For that
# reason there is deliberately no endpoint that returns a document's contents.


class ConsentIn(BaseModel):
    profile: bool | None = None  # save my details for next time
    documents: bool | None = None  # keep my uploaded documents (encrypted) for the application


@app.get("/cases/{case_id}/consent")
def get_consent(case_id: str) -> dict:
    return store.consent(case_id)


@app.put("/cases/{case_id}/consent")
def put_consent(case_id: str, c: ConsentIn) -> dict:
    with _case_locks[case_id]:
        store.ensure_case(case_id)
        if c.documents is False:
            vault.delete_case(case_id)  # withdrawing consent deletes what it covered
        profile = graph.get_state(_cfg(case_id)).values.get("profile") or {}
        return store.set_consent(case_id, c.profile, c.documents, profile)


@app.put("/cases/{case_id}/documents/{doc_type}")
async def put_document(case_id: str, doc_type: str, request: Request,
                       aadhaar_last4: str | None = Query(None, pattern=r"^\d{4}$")) -> dict:
    """Raw file bytes as the body (Content-Type: image/jpeg | image/png | image/webp |
    application/pdf). Needs documents consent. Aadhaar: send the last 4 digits only."""
    if doc_type not in DOC_TYPES:
        raise HTTPException(404, f"unknown document type; one of {DOC_TYPES}")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in CONTENT_TYPES:
        raise HTTPException(415, f"Content-Type must be one of {sorted(CONTENT_TYPES)}")
    if int(request.headers.get("content-length") or 0) > config.DOC_MAX_BYTES:
        raise HTTPException(413, f"file larger than {config.DOC_MAX_BYTES} bytes")
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > config.DOC_MAX_BYTES:
        raise HTTPException(413, f"file larger than {config.DOC_MAX_BYTES} bytes")

    def save() -> dict:
        with _case_locks[case_id]:
            store.ensure_case(case_id)
            if not store.consent(case_id)["documents"]:
                raise HTTPException(403, "documents consent required first: "
                                         f'PUT /cases/{case_id}/consent {{"documents": true}}')
            return public(vault.put(case_id, doc_type, data, content_type, aadhaar_last4))

    return await run_in_threadpool(save)


@app.get("/cases/{case_id}/documents")
def list_documents(case_id: str) -> list[dict]:
    return [public(r) for r in store.documents(case_id)]


@app.delete("/cases/{case_id}/documents/{doc_id}")
def delete_document(case_id: str, doc_id: str) -> dict:
    with _case_locks[case_id]:
        if not vault.delete(case_id, doc_id):
            raise HTTPException(404, "no such document")
    return {"deleted": doc_id}


@app.get("/cases/{case_id}/data")
def get_data(case_id: str) -> dict:
    """Everything we hold about this case: case row, consent, saved profile, case memory
    (graph state), document metadata (never contents), timeline and audit rows."""
    data = store.case_data(case_id)
    if data is None:
        raise HTTPException(404, "no data for this case")
    audit.log_event("citizen", "data_viewed", case_id=case_id)
    values = graph.get_state(_cfg(case_id)).values
    return {
        "case": data["case"],
        "consent": store.consent(case_id),
        "saved_profile": (data["profile"] or {}).get("data") or {},
        "case_memory": {k: values.get(k) for k in ("lang", "profile", "applications", "status",
                                                    "selected", "docs_have", "docs_missing")},
        "documents": [public(r) for r in store.documents(case_id)],
        "events": data["events"],
        "audit": data["audit"],
    }


@app.delete("/cases/{case_id}/data")
def delete_data(case_id: str) -> dict:
    """Delete my data: documents (files + metadata), saved profile, case, timeline and the
    case memory. The audit log keeps its rows (append-only; no personal values in it)."""
    with _case_locks[case_id]:
        if store.case_data(case_id) is None:
            raise HTTPException(404, "no data for this case")
        docs = vault.delete_case(case_id)
        counts = {"documents": docs, **store.delete_case_data(case_id), "case_memory": 1}
        audit.log_event("citizen", "data_deleted", case_id=case_id, detail=counts)
    return {"deleted": counts}
