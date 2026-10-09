"""What /turn does around the graph for tracking (agent/main.py calls before() and after()).

before()  under the case lock, before the graph runs:
          - cases submitted before Phase 6 (or with a delegation still in the case memory) are
            registered with the poller
          - the poller's latest statuses go into the graph state (`applications[*].status`), so
            every answer and the summary agree with the portal
          - `corrections`: per application the portal asked to correct, which documents the
            citizen has uploaded since (ready = all of them)
          - the unread updates, to be spoken first
after()   once the turn succeeded: the updates are put in front of the reply, in the case's
          language (the one function agent/tracking/followup.py compose), and marked delivered:
          spoken exactly once. A turn that fails leaves them unread.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent import rules
from agent.tracking import followup
from agent.tracking.store import TrackStore


@dataclass
class Prep:
    update: dict[str, Any] = field(default_factory=dict)  # graph state update for this turn
    unread: list[dict[str, Any]] = field(default_factory=list)


def corrections(apps: list[dict[str, Any]], docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{scheme_id: {app_id, docs (to replace), waiting (not uploaded yet), ready}} for every
    application at CORRECTION_REQUIRED. A document counts as corrected when it was uploaded
    AFTER the portal asked (the vault keeps the upload time)."""
    out: dict[str, dict[str, Any]] = {}
    for a in apps:
        c = a["correction"]
        if a["status"] != "CORRECTION_REQUIRED" or c is None:
            continue
        try:
            flagged = datetime.fromisoformat(c["flagged_at"])
        except (KeyError, ValueError, TypeError):
            continue
        uploaded = {d["doc_type"] for d in docs if d["created_at"] > flagged}
        targets = [d["doc"] for d in c.get("docs") or [] if d.get("doc")]
        if targets:
            have = [x for x in targets if x in uploaded]
            ready = len(have) == len(targets)
            waiting = [x for x in targets if x not in uploaded]
        else:  # the portal did not name a document we know: any new upload for this scheme
            scheme_docs = {d["doc"] for d in rules.load_schemes()[a["scheme_id"]]["documents"]}
            have = sorted(uploaded & scheme_docs)
            ready, waiting = bool(have), []
        out[a["scheme_id"]] = {"app_id": a["app_id"], "docs": have, "waiting": waiting, "ready": ready}
    return out


def before(ts: TrackStore, case_id: str, values: dict[str, Any], docs: list[dict[str, Any]]) -> Prep:
    prep = Prep()
    applications = values.get("applications") or {}
    if applications:
        ts.register_missing(case_id, applications)
    old = values.get("delegation") or {}
    if old.get("sealed") and ts.delegation(case_id) is None:  # Phase 4 kept it in the case memory
        ts.save_delegation(case_id, old["sealed"], old.get("expires_at"), old.get("scopes") or [])
        prep.update["delegation"] = {"expires_at": old.get("expires_at"), "scopes": old.get("scopes") or []}
    apps = ts.apps(case_id)
    merged = {sid: {**a, "status": next((r["status"] for r in apps if r["scheme_id"] == sid), a.get("status"))}
              for sid, a in applications.items()}
    if merged != applications:
        prep.update["applications"] = merged
    prep.update["corrections"] = corrections(apps, docs)
    prep.unread = ts.unread(case_id)
    return prep


def spoken(updates: list[dict[str, Any]], lang: str) -> tuple[str, str]:
    """(text, English text) for updates, oldest first."""
    pairs = [followup.compose(u, lang) for u in updates]
    return " ".join(p[0] for p in pairs), " ".join(p[1] for p in pairs)


def after(ts: TrackStore, prep: Prep, lang: str, reply: str, subtitle: str | None) -> tuple[str, str | None]:
    if not prep.unread:
        return reply, subtitle
    text, text_en = spoken(prep.unread, lang)
    ts.mark_delivered([u["id"] for u in prep.unread])
    reply = f"{text} {reply}".strip()
    if lang != "en":
        subtitle = f"{text_en} {subtitle or ''}".strip()
    return reply, subtitle
