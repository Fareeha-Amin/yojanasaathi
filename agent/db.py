"""Postgres: case memory (LangGraph checkpointer, one thread per case) + our tables
(agent/schema.sql: citizens, profiles, cases, case_events, documents, audit_log).

Sync psycopg on purpose: it does not depend on an asyncio event loop, so it cannot clash
with async Playwright (Phase 4), which needs the ProactorEventLoop on Windows. /turn is a
sync endpoint (FastAPI runs it in a worker thread), so nothing blocks the event loop.

open_store() fails at startup with a StartupError that says what to fix; the agent never
falls back to in-memory case memory.
"""

import logging
from pathlib import Path
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from agent.privacy import scrub

log = logging.getLogger("yojanasaathi.db")

SCHEMA = Path(__file__).with_name("schema.sql")
CONNECT_TIMEOUT = 5  # seconds


class StartupError(RuntimeError):
    """The agent cannot start; the message says what to fix."""


def redact(url: str | None) -> str:
    """The connection string without its password (safe to print)."""
    if not url:
        return "<not set>"
    try:
        info = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return "<unparseable DATABASE_URL>"
    user = info.get("user", "")
    return f"postgresql://{user}{':***' if info.get('password') else ''}@" \
           f"{info.get('host', 'localhost')}:{info.get('port', 5432)}/{info.get('dbname', '')}"


def _explain(e: Exception, url: str) -> str:
    msg = str(e).strip()
    low = msg.lower()
    if "password authentication failed" in low or "no password supplied" in low:
        hint = "Check the user and password in DATABASE_URL (.env)."
    elif "does not exist" in low and "database" in low:
        db = conninfo_to_dict(url).get("dbname", "yojanasaathi")
        hint = f'Create the database first, e.g. in psql: CREATE DATABASE "{db}";'
    elif "refused" in low or "timeout" in low or "could not connect" in low or "translate host" in low:
        hint = ("Is PostgreSQL running? Windows: Get-Service postgresql* "
                "(start it with Start-Service postgresql-x64-17), and check host/port in DATABASE_URL.")
    else:
        hint = "Check DATABASE_URL in .env and that PostgreSQL is running."
    return (f"Cannot connect to PostgreSQL at {redact(url)}.\n  {hint}\n  ({msg.splitlines()[0] if msg else type(e).__name__})\n"
            "  The agent does not start without Postgres: case memory must survive restarts.")


def check_url(url: str | None) -> str:
    if not url:
        raise StartupError("DATABASE_URL is not set. Add it to .env, e.g. "
                           "DATABASE_URL=postgresql://postgres:<password>@localhost:5432/yojanasaathi")
    try:
        conninfo_to_dict(url)
    except psycopg.ProgrammingError as e:
        raise StartupError(f"DATABASE_URL is not a valid PostgreSQL URL ({redact(url)}): {e}") from None
    return url


class Store:
    """Connection pool + checkpointer + the queries for our tables."""

    def __init__(self, pool: ConnectionPool, url: str):
        self.pool = pool
        self.url = url
        self.checkpointer = PostgresSaver(pool)

    def close(self) -> None:
        self.pool.close()

    # --- audit log (append-only) -------------------------------------------------------

    @staticmethod
    def _audit(conn: psycopg.Connection, actor: str, action: str, case_id: str | None,
               scheme_id: str | None, detail: dict[str, Any] | None) -> None:
        conn.execute(
            "INSERT INTO audit_log (actor, action, case_id, scheme_id, detail) VALUES (%s, %s, %s, %s, %s)",
            (actor, action, case_id, scheme_id, Jsonb(scrub(detail or {}))))

    def log_event(self, actor: str, action: str, case_id: str | None = None,
                  scheme_id: str | None = None, detail: dict[str, Any] | None = None) -> None:
        with self.pool.connection() as conn:
            self._audit(conn, actor, action, case_id, scheme_id, detail)

    # --- cases ---------------------------------------------------------------------------

    def ensure_case(self, case_id: str) -> str:
        """The case's citizen ID; creates citizen + case on first contact."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT citizen_id FROM cases WHERE case_id = %s", (case_id,)).fetchone()
            if row:
                return str(row["citizen_id"])
            citizen = conn.execute("INSERT INTO citizens DEFAULT VALUES RETURNING id").fetchone()["id"]
            conn.execute("INSERT INTO cases (case_id, citizen_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (case_id, citizen))
            self._audit(conn, "agent", "case_opened", case_id, None, {})
            return str(citizen)

    def record_turn(self, case_id: str, values: dict[str, Any], pause: dict | None,
                    resumed: bool) -> None:
        """After every turn: update the case row, add a timeline event (no message text),
        note new submissions (and start their documents' retention clock), and save the
        profile if, and only if, the citizen consented."""
        apps = values.get("applications") or {}
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT c.applications, c.citizen_id, p.consent_profile, p.data FROM cases c "
                "LEFT JOIN profiles p ON p.citizen_id = c.citizen_id WHERE c.case_id = %s FOR UPDATE OF c",
                (case_id,)).fetchone()
            if row is None:
                return  # data deleted mid-turn
            new = {sid: a for sid, a in apps.items() if sid not in (row["applications"] or {})}
            conn.execute(
                "UPDATE cases SET lang = %s, status = %s, selected_scheme = %s, paused = %s, "
                "applications = %s, updated_at = now() WHERE case_id = %s",
                (values.get("lang"), values.get("status"), values.get("selected"),
                 (pause or {}).get("type"), Jsonb(apps), case_id))
            conn.execute(
                "INSERT INTO case_events (case_id, kind, scheme_id, detail) VALUES (%s, 'turn', %s, %s)",
                (case_id, values.get("selected"), Jsonb({
                    "intent": None if resumed else values.get("intent"),
                    "resumed": resumed, "lang": values.get("lang"), "status": values.get("status"),
                    "asking": values.get("asking"), "pause": (pause or {}).get("type")})))
            for sid, a in new.items():
                conn.execute(
                    "INSERT INTO case_events (case_id, kind, scheme_id, detail) VALUES (%s, 'submitted', %s, %s)",
                    (case_id, sid, Jsonb({"app_id": a.get("app_id"), "status": a.get("status")})))
            if new:
                from agent import config  # late: tests change the retention

                n = conn.execute(
                    "UPDATE documents SET expires_at = now() + make_interval(secs => %s) "
                    "WHERE case_id = %s", (config.DOC_RETENTION_HOURS * 3600, case_id)).rowcount
                if n:
                    self._audit(conn, "system", "documents_expiry_set", case_id, None,
                                {"documents": n, "hours_after_submission": config.DOC_RETENTION_HOURS})
            profile = values.get("profile") or {}
            if row["consent_profile"] and profile != (row["data"] or {}):
                conn.execute("UPDATE profiles SET data = %s, updated_at = now() WHERE citizen_id = %s",
                             (Jsonb(profile), row["citizen_id"]))
                self._audit(conn, "agent", "profile_saved", case_id, None, {"fields": sorted(profile)})

    # --- consent + profile ---------------------------------------------------------------

    def consent(self, case_id: str) -> dict[str, Any]:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT p.consent_profile, p.consent_documents, p.consent_at FROM cases c "
                "LEFT JOIN profiles p ON p.citizen_id = c.citizen_id WHERE c.case_id = %s",
                (case_id,)).fetchone()
        row = row or {}
        return {"profile": bool(row.get("consent_profile")),
                "documents": bool(row.get("consent_documents")),
                "at": row["consent_at"].isoformat() if row.get("consent_at") else None}

    def set_consent(self, case_id: str, profile: bool | None, documents: bool | None,
                    current_profile: dict[str, Any]) -> dict[str, Any]:
        """Give or withdraw consent. Giving profile consent saves the case's profile now;
        withdrawing it empties the saved profile. (Withdrawing document consent: the caller
        deletes the files first, see agent/main.py.)"""
        citizen = self.ensure_case(case_id)
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO profiles (citizen_id) VALUES (%s) ON CONFLICT DO NOTHING", (citizen,))
            old = conn.execute("SELECT consent_profile, consent_documents FROM profiles "
                               "WHERE citizen_id = %s FOR UPDATE", (citizen,)).fetchone()
            p = old["consent_profile"] if profile is None else profile
            d = old["consent_documents"] if documents is None else documents
            conn.execute(
                "UPDATE profiles SET consent_profile = %s, consent_documents = %s, consent_at = now(), "
                "data = %s, updated_at = now() WHERE citizen_id = %s",
                (p, d, Jsonb(current_profile if p else {}), citizen))
            self._audit(conn, "citizen", "consent_changed", case_id, None,
                        {"profile": p, "documents": d,
                         "was": {"profile": old["consent_profile"], "documents": old["consent_documents"]}})
            if p and current_profile:
                self._audit(conn, "agent", "profile_saved", case_id, None, {"fields": sorted(current_profile)})
        return self.consent(case_id)

    # --- documents (metadata; agent/vault.py owns the files) ---------------------------

    def add_document(self, case_id: str, doc_type: str, content_type: str, size: int, sha256: str,
                     storage_key: str, aadhaar_last4: str | None) -> tuple[dict[str, Any], str | None]:
        """Insert the metadata row, replacing an earlier upload of the same type.
        Returns (row, storage key of the replaced file or None)."""
        with self.pool.connection() as conn, conn.transaction():
            case = conn.execute("SELECT citizen_id FROM cases WHERE case_id = %s", (case_id,)).fetchone()
            old = conn.execute("DELETE FROM documents WHERE case_id = %s AND doc_type = %s RETURNING storage_key",
                               (case_id, doc_type)).fetchone()
            row = conn.execute(
                "INSERT INTO documents (case_id, citizen_id, doc_type, content_type, size_bytes, sha256, "
                "storage_key, aadhaar_last4) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (case_id, case["citizen_id"], doc_type, content_type, size, sha256, storage_key,
                 aadhaar_last4)).fetchone()
            self._audit(conn, "citizen", "document_stored", case_id, None, {
                "doc_id": str(row["id"]), "doc_type": doc_type, "content_type": content_type,
                "size_bytes": size, "aadhaar_last4": aadhaar_last4, "replaced": old is not None})
        return row, (old["storage_key"] if old else None)

    def documents(self, case_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM documents WHERE case_id = %s ORDER BY created_at",
                                (case_id,)).fetchall()

    def document(self, case_id: str, doc_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM documents WHERE case_id = %s AND id::text = %s",
                                (case_id, doc_id)).fetchone()

    def document_types(self, case_id: str) -> list[str]:
        return [d["doc_type"] for d in self.documents(case_id)]

    def delete_document_row(self, doc_id: str, actor: str, action: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("DELETE FROM documents WHERE id::text = %s RETURNING *", (doc_id,)).fetchone()
            if row:
                self._audit(conn, actor, action, row["case_id"], None,
                            {"doc_id": doc_id, "doc_type": row["doc_type"]})
        return row

    def expired_documents(self) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM documents WHERE expires_at <= now()").fetchall()

    def storage_keys(self) -> set[str]:
        with self.pool.connection() as conn:
            return {r["storage_key"] for r in conn.execute("SELECT storage_key FROM documents")}

    # --- the citizen's own data ----------------------------------------------------------

    def case_data(self, case_id: str) -> dict[str, Any] | None:
        """Everything our tables hold about this case (document metadata, never contents)."""
        with self.pool.connection() as conn:
            case = conn.execute("SELECT * FROM cases WHERE case_id = %s", (case_id,)).fetchone()
            if case is None:
                return None
            profile = conn.execute("SELECT * FROM profiles WHERE citizen_id = %s",
                                   (case["citizen_id"],)).fetchone()
            events = conn.execute("SELECT at, kind, scheme_id, detail FROM case_events "
                                  "WHERE case_id = %s ORDER BY id", (case_id,)).fetchall()
            audit = conn.execute("SELECT at, actor, action, scheme_id, detail FROM audit_log "
                                 "WHERE case_id = %s ORDER BY id", (case_id,)).fetchall()
        return {"case": case, "profile": profile, "events": events, "audit": audit}

    def delete_case_data(self, case_id: str) -> dict[str, int]:
        """Delete the case, its timeline, the citizen's saved profile (and the citizen row
        when no other case uses it) and the case memory. Documents: vault.delete_case()
        first. The audit log keeps its rows (append-only, no personal values)."""
        counts = {"cases": 0, "case_events": 0, "profiles": 0, "citizens": 0}
        with self.pool.connection() as conn, conn.transaction():
            case = conn.execute("SELECT citizen_id FROM cases WHERE case_id = %s FOR UPDATE",
                                (case_id,)).fetchone()
            if case:
                cid = case["citizen_id"]
                counts["case_events"] = conn.execute("DELETE FROM case_events WHERE case_id = %s",
                                                     (case_id,)).rowcount
                conn.execute("DELETE FROM documents WHERE case_id = %s", (case_id,))
                counts["cases"] = conn.execute("DELETE FROM cases WHERE case_id = %s", (case_id,)).rowcount
                counts["profiles"] = conn.execute("DELETE FROM profiles WHERE citizen_id = %s",
                                                  (cid,)).rowcount
                others = conn.execute("SELECT 1 FROM cases WHERE citizen_id = %s LIMIT 1", (cid,)).fetchone()
                if not others:
                    counts["citizens"] = conn.execute("DELETE FROM citizens WHERE id = %s", (cid,)).rowcount
        self.checkpointer.delete_thread(case_id)
        return counts


def open_store(url: str | None, *, min_size: int = 1, max_size: int = 10) -> Store:
    """Connect, create/upgrade the tables, return the Store. Raises StartupError."""
    url = check_url(url)
    # One direct connection first: a pool only says "timeout", this says why.
    try:
        psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT).close()
    except psycopg.Error as e:
        raise StartupError(_explain(e, url)) from None
    pool = ConnectionPool(
        url, min_size=min_size, max_size=max_size, open=False, name="yojanasaathi",
        # what PostgresSaver needs (see PostgresSaver.from_conn_string)
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                "connect_timeout": CONNECT_TIMEOUT})
    try:
        pool.open(wait=True, timeout=CONNECT_TIMEOUT * 2)
    except Exception as e:
        pool.close()
        raise StartupError(_explain(e, url)) from e
    try:
        store = Store(pool, url)
        store.checkpointer.setup()
        with pool.connection() as conn:
            # several statements in one go: simple query protocol (ClientCursor)
            psycopg.ClientCursor(conn).execute(SCHEMA.read_text(encoding="utf-8"))
    except Exception as e:
        pool.close()
        raise StartupError(f"Connected to PostgreSQL at {redact(url)}, but creating the tables failed:\n"
                           f"  {e}\n  Does the user in DATABASE_URL own the database (CREATE rights)?") from e
    log.info("postgres ready: %s", redact(url))
    return store


# --- test database helpers (tests/conftest.py) -----------------------------------------


def url_for_tests(url: str | None) -> str:
    """DATABASE_URL with "_test" appended to the database name (not named test_*: pytest
    would collect it wherever it is imported)."""
    info = conninfo_to_dict(check_url(url))
    return make_conninfo(url, dbname=f"{info.get('dbname') or 'yojanasaathi'}_test")


def recreate_database(url: str) -> None:
    """Drop and create the database in `url`. Refuses anything not named *_test."""
    db = conninfo_to_dict(url).get("dbname", "")
    if not db.endswith("_test"):
        raise ValueError(f"refusing to recreate {db!r}: only *_test databases")
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True,
                         connect_timeout=CONNECT_TIMEOUT) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{db}"')
