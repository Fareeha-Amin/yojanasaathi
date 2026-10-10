"""The status poller: APScheduler inside FastAPI, one portal call per citizen.

tick()        every STATUS_POLL_SECONDS / 6 (max 10 s): the cases that are due -> check_case().
              Runs on the scheduler's own thread: /turn never waits for it.
check_case()  one list call (GET /agent/v1/citizen/applications/) with the case's sealed,
              read-only delegation token. A status that differs from what we stored is applied in
              one transaction (agent/tracking/store.py apply_change): status row, update for the
              citizen, timeline event, audit `status_changed`; then one web push. Details are
              fetched only on a change: documents + history for CORRECTION_REQUIRED / REJECTED
              (the portal's remark and the flagged document), history for the timeline.
Errors        timeout / 5xx / 401 -> back off 2x per failure (capped); 429 -> the same, honouring
              Retry-After; 403 DELEGATION_TOKEN_INVALID -> polling for the case pauses and the
              citizen is told ONCE (an update, spoken at the next turn + pushed) that renewing
              needs a new OTP login. All state is in Postgres: a restart loses nothing.
Never logged: tokens, portal remarks, numbers (scheme IDs and status codes only).
"""

import hashlib
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent import audit, config, rules, sealed
from agent.tracking import Throttled, TokenExpired, TrackUnavailable, push
from agent.tracking.api import CitizenAPI
from agent.tracking.store import FINAL, TrackStore

log = logging.getLogger("yojanasaathi.poller")

PROBLEM_DOC = ("CORRECTION_REQUIRED", "REJECTED")  # a document's verification_status we must tell about
NEEDS_DETAILS = ("CORRECTION_REQUIRED", "REJECTED")  # the remark / document is part of the update
DOC_WATCH = ("SUBMITTED", "UNDER_REVIEW", "CORRECTION_REQUIRED")  # documents can be flagged meanwhile
TEXT_MAX = 300


@dataclass
class CheckResult:
    state: str  # ok | no_tracking | paused | expired | throttled | unavailable
    apps: list[dict[str, Any]] = field(default_factory=list)  # tracked_apps rows after the check
    changes: list[dict[str, Any]] = field(default_factory=list)  # new update rows


def _doc_id(scheme_id: str, portal_name: str) -> str | None:
    """Our document id for the portal's document type name (the seed's English label)."""
    scheme = rules.load_schemes().get(scheme_id)
    for d in (scheme or {}).get("documents", []):
        if d["label"]["en"].strip().lower() == (portal_name or "").strip().lower():
            return d["doc"]
    return None


def _clip(v: Any) -> str:
    return " ".join(str(v or "").split())[:TEXT_MAX]


def _problem_docs(scheme_id: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": d.get("document_type"), "doc": _doc_id(scheme_id, d.get("document_type") or ""),
             "status": d.get("verification_status"), "remark": _clip(d.get("remarks"))}
            for d in docs if d.get("verification_status") in PROBLEM_DOC]


def _events(history: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for e in (history or {}).get("lifecycle_events") or []:
        if isinstance(e, dict):
            out.append({"at": e.get("created_at"), "type": e.get("type"), "title": _clip(e.get("title")),
                        "message": _clip(e.get("message"))})
    return out


def _sig(docs: list[dict[str, Any]]) -> str:
    return hashlib.sha1(json.dumps(sorted([d["type"], d["status"], d["remark"]] for d in docs)).encode()).hexdigest()[:12]


class Poller:
    def __init__(self, ts: TrackStore, api: CitizenAPI | None = None):
        self.ts = ts
        self.api = api or CitizenAPI()
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._scheduler = None

    # --- scheduler --------------------------------------------------------------------------

    def start(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        interval = max(1.0, min(config.STATUS_POLL_SECONDS / 6, 10.0))  # a case is due at most one tick late
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._scheduler.add_job(self.tick, "interval", seconds=interval, id="status-poll", max_instances=1,
                                coalesce=True, next_run_time=datetime.now(timezone.utc))
        self._scheduler.start()
        log.info("status polling every %.0f s per citizen (checked every %.0f s)", config.STATUS_POLL_SECONDS,
                 interval)

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def tick(self) -> int:
        """Poll every case that is due; one failing case never stops the others."""
        n = 0
        try:
            cases = self.ts.due_cases()
        except Exception as e:  # noqa: BLE001 (database hiccup: the next tick retries)
            log.warning("poll: cannot list due cases (%s)", type(e).__name__)
            return 0
        for case_id in cases:
            try:
                self.check_case(case_id)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("poll: a case failed (%s)", type(e).__name__)
        return n

    # --- one case ---------------------------------------------------------------------------

    def check_case(self, case_id: str, *, now: bool = False) -> CheckResult:
        """Look at the portal for this case. `now`: the citizen asked ("what's my status?"):
        short timeout, and what changed counts as already told (the answer says it)."""
        with self._locks[case_id]:
            return self._check(case_id, now)

    def _check(self, case_id: str, now: bool) -> CheckResult:
        ts = self.ts
        d = ts.delegation(case_id)
        rows = ts.apps(case_id)
        if d is None:
            return CheckResult("no_tracking", rows)
        if d["paused_reason"]:
            return CheckResult("paused", rows)
        try:
            token = sealed.open_(case_id, "delegation", d["sealed"])
        except sealed.SealedError:
            self._expired(case_id, d, now)
            return CheckResult("expired", ts.apps(case_id))
        timeout = config.TRACK_NOW_TIMEOUT if now else None
        try:
            listing = self.api.applications(token, timeout)
            by_no = {a.get("application_number"): a for a in listing}
            changes: list[dict[str, Any]] = []
            for row in rows:
                if row["final"] or row["app_id"] not in by_no:
                    continue
                new = self._apply(case_id, token, row, by_no[row["app_id"]], timeout, now)
                if new is not None:
                    changes.append(new)
        except TokenExpired:
            self._expired(case_id, d, now)
            return CheckResult("expired", ts.apps(case_id))
        except Throttled as e:
            delay = ts.schedule_failure(case_id, e.retry_after)
            log.warning("poll: portal rate limit, next try in %.0f s", delay)
            return CheckResult("throttled", ts.apps(case_id))
        except TrackUnavailable as e:
            delay = ts.schedule_failure(case_id)
            log.warning("poll: portal unavailable (%s), next try in %.0f s", str(e).split(":")[0], delay)
            return CheckResult("unavailable", ts.apps(case_id))
        ts.schedule_ok(case_id)
        self._push(case_id, changes)
        return CheckResult("ok", ts.apps(case_id), changes)

    def _apply(self, case_id: str, token: str, row: dict[str, Any], item: dict[str, Any],
               timeout: float | None, now: bool) -> dict[str, Any] | None:
        """Compare one application with the portal; record a change. Raises the API errors
        (the whole check then backs off and nothing half-recorded is lost: it is retried)."""
        sid, app_no = row["scheme_id"], row["app_id"]
        status = item.get("status") or row["status"]
        stamp = item.get("updated_at")
        changed = status != row["status"]
        # documents are looked at only when something happened: a new status, or the portal's own
        # updated_at moved (first poll: compared with the submission time, so a quiet application
        # costs exactly one call per citizen)
        baseline = row["portal_updated_at"] or item.get("submitted_at")
        want_docs = (changed and status in NEEDS_DETAILS) or (
            not changed and status in DOC_WATCH and stamp and stamp != baseline)
        docs: list[dict[str, Any]] | None = None
        history = None
        if want_docs:
            try:
                docs = self.api.documents(token, app_no, timeout)
            except TrackUnavailable:
                if changed and status in NEEDS_DETAILS:
                    raise  # the remark is the point of this update: retry rather than lose it
        if changed:
            try:
                history = self.api.history(token, app_no, timeout)
            except TrackUnavailable:
                if status in NEEDS_DETAILS:
                    raise
        events = _events(history) if history is not None else None
        problems = _problem_docs(sid, docs) if docs is not None else []
        old_corr = row["correction"] or {}

        if changed:
            data: dict[str, Any] = {"status": status, "prev": row["status"]}
            correction = None
            if status in NEEDS_DETAILS:
                reason = next((e["message"] for e in reversed(events or []) if e["message"]), None)
                correction = {"flagged_at": datetime.now(timezone.utc).isoformat(), "docs": problems,
                              "reason": reason}
                data.update(docs=problems, reason=reason)
            update = {"kind": "status_changed", "data": data,
                      "dedupe_key": f"status:{app_no}:{row['status']}>{status}:{stamp or ''}"}
            return self.ts.apply_change(case_id, sid, status=status, portal_updated_at=stamp,
                                        correction=correction, history=events, update=update,
                                        mark_delivered=now, expect_old=row["status"])
        known = {_sig([d]) for d in old_corr.get("docs") or []}
        fresh = [p for p in problems if _sig([p]) not in known]
        if fresh:  # a document flagged while the status stayed the same
            correction = {"flagged_at": old_corr.get("flagged_at") or datetime.now(timezone.utc).isoformat(),
                          "docs": [*(old_corr.get("docs") or []), *fresh], "reason": old_corr.get("reason")}
            update = {"kind": "document_problem", "data": {"status": status, "docs": fresh},
                      "dedupe_key": f"doc:{app_no}:{_sig(fresh)}"}
            return self.ts.apply_change(case_id, sid, status=status, portal_updated_at=stamp,
                                        correction=correction, history=None, update=update,
                                        mark_delivered=now, expect_old=status)
        if stamp != row["portal_updated_at"]:
            self.ts.apply_change(case_id, sid, status=status, portal_updated_at=stamp,
                                 correction=row["correction"], history=None, update=None, expect_old=status)
        return None

    def _expired(self, case_id: str, d: dict[str, Any], now: bool) -> None:
        """403 DELEGATION_TOKEN_INVALID: stop polling this case, tell the citizen once."""
        self.ts.pause(case_id, "expired")
        new = self.ts.add_update(case_id, "delegation_expired", f"expired:{d['created_at'].isoformat()}", {},
                                 delivered=now)
        if new is not None:
            audit.log_event("system", "delegation_expired", case_id=case_id, detail={"polling": "paused"})
            self._push(case_id, [new])

    def _push(self, case_id: str, updates: list[dict[str, Any]]) -> None:
        if not updates or not push.configured():
            return
        lang = self.ts.case_lang(case_id)
        for u in updates:
            try:
                push.notify(self.ts, case_id, u, lang)
            except Exception as e:  # noqa: BLE001 (never fails polling)
                log.warning("push: %s", type(e).__name__)

    # --- after the citizen's own actions --------------------------------------------------------

    def final(self, status: str) -> bool:
        return status in FINAL
