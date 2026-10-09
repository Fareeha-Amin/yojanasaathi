"""FastAPI app: the one contract every channel uses (web, voice, phone).

POST /turn/{case_id}  {"text": "...", "lang"?: "kn"|"hi"|"en"}
  -> {"reply": "...", "pause": null | {"type": ..., ...}, "ui": null | {"type": ..., ...},
      "subtitle": null | "..."}
"reply" is what gets spoken (short); "ui" is this turn's screen payload (full reasons,
sources, checklists); "subtitle" is the reply in English when the case's language is kn/hi
(null for English and for free-form LLM answers). Clients that only read reply/pause keep
working. If the case's graph is paused at an interrupt, the text resumes it
(Command(resume=text)). "lang" is optional (voice passes the STT-detected language); when
given it updates the case.

Phase 3: case memory is the Postgres checkpointer (one thread per case), so a pause and
the per-scheme `applications` survive restarts. Startup fails clearly without Postgres or
MASTER_KEY. Aadhaar numbers are masked before the graph sees the text.
Phase 5: POST /session gives the web app a case + JWT (agent/auth.py); every /cases/{id}
endpoint (summary, edit, consent, documents, view / delete my data) needs that token.
/turn stays keyed by case ID (voice bot, agent.cli).
"""

import logging
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent import audit, auth, config, db, privacy, rules, summary, tts
from agent.facts import AGE_MAX, AGE_MIN
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
auth.set_key(_master_key)
store = db.open_store(config.DATABASE_URL)
vault = Vault(store, config.VAULT_DIR, _master_key)
audit.set_store(store)
graph = build_graph(store.checkpointer)
speech = tts.SarvamTTS(config.SARVAM_API_KEY, cache_size=config.TTS_CACHE_SIZE)

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
    for warning in config.portal_url_warnings():
        _log.warning("config: %s", warning)
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
    subtitle: str | None = None


def _cfg(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


def _pause(case_id: str) -> dict | None:
    interrupts = graph.get_state(_cfg(case_id)).interrupts
    return interrupts[0].value if interrupts else None


def _run(case_id: str, inp, resumed: bool) -> TurnOut:
    """Invoke the graph for one turn (caller holds the case lock) and record it."""
    # durability="sync": each step's checkpoint is written before the next step runs
    out = graph.invoke(inp, _cfg(case_id), version="v2", durability="sync")
    pause = out.interrupts[0].value if out.interrupts else None
    store.record_turn(case_id, out.value, pause, resumed=resumed)
    return TurnOut(reply=out.value.get("reply", ""), pause=pause, ui=out.value.get("ui"),
                   subtitle=out.value.get("subtitle"))


@app.post("/turn/{case_id}", response_model=TurnOut)
def turn(case_id: str, m: TurnIn) -> TurnOut:
    # Before anything stores or forwards the text (checkpoint, LLM, logs).
    text, aadhaar = privacy.mask_aadhaar(m.text, privacy.HIDDEN)
    with _case_locks[case_id]:
        store.ensure_case(case_id)
        if aadhaar:
            audit.log_event("agent", "aadhaar_masked", case_id=case_id,
                            detail={"aadhaar_last4": aadhaar})
        paused = _pause(case_id) is not None
        update = {"docs_stored": store.document_types(case_id)}
        if m.lang:
            update["lang"] = m.lang
        # ui/subtitle are per turn: a resume skips the router, so clear the previous turn's here
        inp = (Command(resume=text, update={**update, "ui": None, "subtitle": None}) if paused
               else {"msg": text, **update})
        return _run(case_id, inp, resumed=paused)


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


# --- web app session (JWT) ----------------------------------------------------------------


class SessionOut(BaseModel):
    case_id: str
    token: str
    expires_at: int  # unix seconds


@app.post("/session", response_model=SessionOut)
def session(authorization: str | None = Header(None)) -> SessionOut:
    """No token: a new random case + its token. A valid token: a fresh token for the same
    case (the web app calls this at start-up). An invalid / expired token: 401, and the app
    starts a new case. Nothing is stored until the case's first turn."""
    if authorization:
        try:
            case_id = auth.verify(auth.bearer(authorization))
        except auth.AuthError as e:
            raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from None
    else:
        case_id = auth.new_case_id()
    token, exp = auth.issue(case_id)
    return SessionOut(case_id=case_id, token=token, expires_at=exp)


def case_auth(case_id: str, authorization: str | None = Header(None)) -> str:
    """Every /cases/{case_id} endpoint: a valid token for exactly this case."""
    try:
        sub = auth.verify(auth.bearer(authorization))
    except auth.AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from None
    if sub != case_id:
        raise HTTPException(403, "this token is for another case")
    return case_id


CaseAuth = [Depends(case_auth)]


# --- screens: summary + review edits -------------------------------------------------------


@app.get("/cases/{case_id}/summary", dependencies=CaseAuth)
def get_summary(case_id: str, lang: Literal["kn", "hi", "en"] | None = None) -> dict:
    """What the web app's screens show (agent/summary.py). Read-only, nothing logged; no
    case lock (reads the last checkpoint), so it never waits for a slow turn."""
    state = graph.get_state(_cfg(case_id))
    pause = state.interrupts[0].value if state.interrupts else None
    return summary.build(case_id, state.values or {}, pause, store.documents(case_id),
                         store.consent(case_id), store.case_data(case_id), lang)


class EditIn(BaseModel):
    field: Literal["age", "annual_income"]
    value: int


@app.post("/cases/{case_id}/edit", response_model=TurnOut, dependencies=CaseAuth)
def edit(case_id: str, e: EditIn) -> TurnOut:
    """Change one reviewed fact on the review screen. Only while the case is paused at the
    confirm gate; resumes it with {"edit": ...}: the rules run again and a new read-back +
    confirm pause follows (an edit never submits)."""
    lo, hi = (AGE_MIN, AGE_MAX) if e.field == "age" else (0, 10**9)
    if not lo <= e.value <= hi:
        raise HTTPException(422, f"{e.field} must be between {lo} and {hi}")
    with _case_locks[case_id]:
        pause = _pause(case_id)
        if not pause or pause.get("type") != "confirm":
            raise HTTPException(409, "nothing to review: the case is not waiting for confirmation")
        update = {"docs_stored": store.document_types(case_id), "ui": None, "subtitle": None}
        return _run(case_id, Command(resume={"edit": {e.field: e.value}}, update=update), resumed=True)


# --- read aloud (Sarvam Bulbul, same voice as the bot) ----------------------------------


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=tts.MAX_CHARS)
    lang: Literal["kn", "hi", "en"] | None = None


@app.post("/tts")
def speak(t: TTSIn, authorization: str | None = Header(None)) -> Response:
    """WAV audio of `text` for the web app (read aloud, replay, typed-turn replies when the
    voice bot is off). Needs a valid session token (any case): it spends Sarvam credit.
    503 when SARVAM_API_KEY is not set, 502 when Sarvam fails: the app then falls back."""
    try:
        auth.verify(auth.bearer(authorization))
    except auth.AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from None
    if not speech.available:
        raise HTTPException(503, "read aloud is not configured (SARVAM_API_KEY)")
    try:
        wav = speech.synthesize(t.text, t.lang)
    except tts.TTSError as e:
        _log.warning("tts failed: %s", e)
        raise HTTPException(502, "read aloud failed") from None
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})


# --- the citizen's data: consent, documents, view / delete ------------------------------
# Need the case's session token (Phase 5). There is deliberately no endpoint that returns a
# document's contents: the browser only ever sees metadata (Aadhaar as last 4).


class ConsentIn(BaseModel):
    profile: bool | None = None  # save my details for next time
    documents: bool | None = None  # keep my uploaded documents (encrypted) for the application


@app.get("/cases/{case_id}/consent", dependencies=CaseAuth)
def get_consent(case_id: str) -> dict:
    return store.consent(case_id)


@app.put("/cases/{case_id}/consent", dependencies=CaseAuth)
def put_consent(case_id: str, c: ConsentIn) -> dict:
    with _case_locks[case_id]:
        store.ensure_case(case_id)
        if c.documents is False:
            vault.delete_case(case_id)  # withdrawing consent deletes what it covered
        profile = graph.get_state(_cfg(case_id)).values.get("profile") or {}
        return store.set_consent(case_id, c.profile, c.documents, profile)


@app.put("/cases/{case_id}/documents/{doc_type}", dependencies=CaseAuth)
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


@app.get("/cases/{case_id}/documents", dependencies=CaseAuth)
def list_documents(case_id: str) -> list[dict]:
    return [public(r) for r in store.documents(case_id)]


@app.delete("/cases/{case_id}/documents/{doc_id}", dependencies=CaseAuth)
def delete_document(case_id: str, doc_id: str) -> dict:
    with _case_locks[case_id]:
        if not vault.delete(case_id, doc_id):
            raise HTTPException(404, "no such document")
    return {"deleted": doc_id}


@app.get("/cases/{case_id}/data", dependencies=CaseAuth)
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


@app.delete("/cases/{case_id}/data", dependencies=CaseAuth)
def delete_data(case_id: str) -> dict:
    """Delete my data: documents (files + metadata), saved profile, case, timeline and the
    case memory. The audit log keeps its rows (append-only; no personal values in it).
    Idempotent: a case with no data gets 200 {"deleted": false} (the demo reset never errors)."""
    with _case_locks[case_id]:
        if store.case_data(case_id) is None:
            return {"deleted": False}
        docs = vault.delete_case(case_id)
        counts = {"documents": docs, **store.delete_case_data(case_id), "case_memory": 1}
        audit.log_event("citizen", "data_deleted", case_id=case_id, detail=counts)
    return {"deleted": True, "counts": counts}
