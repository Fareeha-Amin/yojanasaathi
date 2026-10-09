"""Postgres side of status tracking (tables in agent/schema.sql): the sealed delegation token
and the poller's schedule, tracked applications, updates for the citizen, push subscriptions.

A portal change is applied in ONE transaction (status row + update row + timeline event +
audit row), and the update's dedupe_key is unique per case, so the same change can never be
announced twice, however often it is seen (two pollers, a check-now, a retry after a crash).
"""

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from agent import config
from agent.db import Store
from agent.privacy import scrub

FINAL = ("APPROVED", "REJECTED")


class TrackStore:
    def __init__(self, store: Store):
        self.pool = store.pool
        self._audit = Store._audit

    # --- delegation (the read-only portal token, sealed) + schedule -----------------------

    def save_delegation(self, case_id: str, sealed_token: str, expires_at: str | None,
                        scopes: list[str]) -> None:
        """New or renewed: polling resumes at once (paused_reason and fail_count reset)."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO delegations (case_id, sealed, expires_at, scopes) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (case_id) DO UPDATE SET sealed = EXCLUDED.sealed, expires_at = EXCLUDED.expires_at, "
                "scopes = EXCLUDED.scopes, created_at = now(), next_check_at = now(), fail_count = 0, "
                "paused_reason = NULL", (case_id, sealed_token, expires_at, scopes))

    def delegation(self, case_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM delegations WHERE case_id = %s", (case_id,)).fetchone()

    def delete_delegation(self, case_id: str, why: str) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            n = conn.execute("DELETE FROM delegations WHERE case_id = %s", (case_id,)).rowcount
            if n:
                self._audit(conn, "system", "delegation_deleted", case_id, None, {"why": why})
        return bool(n)

    def due_cases(self, limit: int = 50) -> list[str]:
        """Cases to poll now: a delegation that is not paused, an open application, due."""
        with self.pool.connection() as conn:
            return [r["case_id"] for r in conn.execute(
                "SELECT d.case_id FROM delegations d WHERE d.paused_reason IS NULL AND d.next_check_at <= now() "
                "AND EXISTS (SELECT 1 FROM tracked_apps a WHERE a.case_id = d.case_id AND NOT a.final) "
                "ORDER BY d.next_check_at LIMIT %s", (limit,))]

    def schedule_ok(self, case_id: str) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE delegations SET fail_count = 0, last_checked_at = now(), "
                "next_check_at = now() + make_interval(secs => %s) WHERE case_id = %s",
                (config.STATUS_POLL_SECONDS, case_id))
            conn.execute("UPDATE tracked_apps SET last_checked_at = now() WHERE case_id = %s AND NOT final",
                         (case_id,))

    def schedule_failure(self, case_id: str, retry_after: float | None = None) -> float:
        """Back off: 2x the poll interval per consecutive failure (the portal's Retry-After
        if longer), capped. Returns the delay in seconds."""
        with self.pool.connection() as conn:
            row = conn.execute("SELECT fail_count FROM delegations WHERE case_id = %s", (case_id,)).fetchone()
            fails = (row["fail_count"] if row else 0) + 1
            delay = min(max(retry_after or 0.0, config.STATUS_POLL_SECONDS * 2 ** min(fails, 10)),
                        max(config.TRACK_BACKOFF_MAX, retry_after or 0.0))
            conn.execute("UPDATE delegations SET fail_count = %s, next_check_at = now() + make_interval(secs => %s) "
                         "WHERE case_id = %s", (fails, delay, case_id))
        return delay

    def pause(self, case_id: str, reason: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE delegations SET paused_reason = %s WHERE case_id = %s", (reason, case_id))

    # --- applications -------------------------------------------------------------------------

    def apps(self, case_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM tracked_apps WHERE case_id = %s ORDER BY scheme_id",
                                (case_id,)).fetchall()

    def statuses(self, case_id: str) -> dict[str, str]:
        return {a["scheme_id"]: a["status"] for a in self.apps(case_id)}

    def apply_change(self, case_id: str, scheme_id: str, *, status: str, portal_updated_at: str | None,
                     correction: dict[str, Any] | None, history: list[dict[str, Any]] | None,
                     update: dict[str, Any] | None, mark_delivered: bool = False,
                     expect_old: str | None = None) -> dict[str, Any] | None:
        """Record what the portal shows for one application. `update` = {kind, data, dedupe_key}
        when this is something to tell the citizen. Returns the new update row, or None when
        there was nothing new (an already-recorded change is skipped, never repeated)."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT status, app_id FROM tracked_apps WHERE case_id = %s AND scheme_id = %s "
                               "FOR UPDATE", (case_id, scheme_id)).fetchone()
            if row is None:
                return None
            old = row["status"]
            if expect_old is not None and old != expect_old:
                return None  # another check recorded it first (a check-now beside the poller)
            conn.execute(
                "UPDATE tracked_apps SET status = %s, portal_updated_at = %s, final = %s, last_checked_at = now(), "
                "correction = %s, history = COALESCE(%s, history) WHERE case_id = %s AND scheme_id = %s",
                (status, portal_updated_at, status in FINAL, Jsonb(scrub(correction)) if correction else None,
                 Jsonb(scrub(history)) if history is not None else None, case_id, scheme_id))
            new = None
            if update:
                new = conn.execute(
                    "INSERT INTO case_updates (case_id, scheme_id, app_id, kind, data, dedupe_key, delivered_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END) "
                    "ON CONFLICT (case_id, dedupe_key) DO NOTHING RETURNING *",
                    (case_id, scheme_id, row["app_id"], update["kind"], Jsonb(scrub(update["data"])),
                     update["dedupe_key"], mark_delivered)).fetchone()
            if new is not None:
                if old != status:
                    conn.execute(
                        "INSERT INTO case_events (case_id, kind, scheme_id, detail) VALUES (%s, 'status_changed', %s, %s)",
                        (case_id, scheme_id, Jsonb({"status": status, "from": old})))
                    self._audit(conn, "system", "status_changed", case_id, scheme_id,
                                {"from": old, "to": status, "app_id": row["app_id"]})
                else:
                    self._audit(conn, "system", "document_flagged", case_id, scheme_id,
                                {"status": status, "app_id": row["app_id"],
                                 "documents": [d.get("type") for d in (update["data"].get("docs") or [])]})
            if status in FINAL and not conn.execute(
                    "SELECT 1 FROM tracked_apps WHERE case_id = %s AND NOT final LIMIT 1", (case_id,)).fetchone():
                if conn.execute("DELETE FROM delegations WHERE case_id = %s", (case_id,)).rowcount:
                    self._audit(conn, "system", "delegation_deleted", case_id, None, {"why": "all_applications_final"})
        return new

    def note_resubmitted(self, case_id: str, scheme_id: str, status: str) -> None:
        """After the citizen's corrected documents went to the portal and it was resubmitted."""
        with self.pool.connection() as conn, conn.transaction():
            old = conn.execute("SELECT status FROM tracked_apps WHERE case_id = %s AND scheme_id = %s FOR UPDATE",
                               (case_id, scheme_id)).fetchone()
            conn.execute("UPDATE tracked_apps SET status = %s, correction = NULL, final = false "
                         "WHERE case_id = %s AND scheme_id = %s", (status, case_id, scheme_id))
            conn.execute("UPDATE delegations SET next_check_at = now() WHERE case_id = %s", (case_id,))
            if old and old["status"] != status:
                conn.execute("INSERT INTO case_events (case_id, kind, scheme_id, detail) "
                             "VALUES (%s, 'status_changed', %s, %s)",
                             (case_id, scheme_id, Jsonb({"status": status, "from": old["status"], "by": "citizen"})))

    # --- updates for the citizen --------------------------------------------------------------

    def add_update(self, case_id: str, kind: str, dedupe_key: str, data: dict[str, Any],
                   scheme_id: str | None = None, app_id: str | None = None,
                   delivered: bool = False) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "INSERT INTO case_updates (case_id, scheme_id, app_id, kind, data, dedupe_key, delivered_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END) "
                "ON CONFLICT (case_id, dedupe_key) DO NOTHING RETURNING *",
                (case_id, scheme_id, app_id, kind, Jsonb(scrub(data)), dedupe_key, delivered)).fetchone()

    def case_lang(self, case_id: str) -> str:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT lang FROM cases WHERE case_id = %s", (case_id,)).fetchone()
        return (row or {}).get("lang") or "en"

    def register_missing(self, case_id: str, applications: dict[str, Any]) -> int:
        """Applications in the case memory that the poller does not track yet (submitted
        before Phase 6): start tracking them."""
        n = 0
        with self.pool.connection() as conn:
            for sid, a in applications.items():
                if a.get("app_id"):
                    n += conn.execute(
                        "INSERT INTO tracked_apps (case_id, scheme_id, app_id, status, final) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (case_id, scheme_id) DO NOTHING",
                        (case_id, sid, a["app_id"], a.get("status") or "SUBMITTED",
                         (a.get("status") or "SUBMITTED") in FINAL)).rowcount
        return n

    def unread(self, case_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM case_updates WHERE case_id = %s AND delivered_at IS NULL "
                                "ORDER BY id", (case_id,)).fetchall()

    def mark_delivered(self, ids: list[int]) -> None:
        if ids:
            with self.pool.connection() as conn:
                conn.execute("UPDATE case_updates SET delivered_at = now() WHERE id = ANY(%s) "
                             "AND delivered_at IS NULL", (ids,))

    def mark_pushed(self, update_id: int) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE case_updates SET pushed_at = now() WHERE id = %s", (update_id,))

    # --- push subscriptions -------------------------------------------------------------------

    def add_subscription(self, case_id: str, endpoint: str, sealed_keys: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO push_subscriptions (case_id, endpoint, sealed) VALUES (%s, %s, %s) "
                         "ON CONFLICT (case_id, endpoint) DO UPDATE SET sealed = EXCLUDED.sealed",
                         (case_id, endpoint, sealed_keys))

    def subscriptions(self, case_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM push_subscriptions WHERE case_id = %s ORDER BY id",
                                (case_id,)).fetchall()

    def remove_subscription(self, case_id: str, endpoint: str) -> bool:
        with self.pool.connection() as conn:
            return bool(conn.execute("DELETE FROM push_subscriptions WHERE case_id = %s AND endpoint = %s",
                                     (case_id, endpoint)).rowcount)

    # --- for the summary ----------------------------------------------------------------------

    def snapshot(self, case_id: str) -> dict[str, Any]:
        return {"delegation": self.delegation(case_id), "apps": self.apps(case_id),
                "unread": self.unread(case_id)}


def iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else v
