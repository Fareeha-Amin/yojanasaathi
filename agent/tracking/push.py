"""Web push (backend only; the browser side comes later): pywebpush + VAPID.

    python -m agent.tracking.push keys     print a VAPID key pair for .env

Subscriptions arrive through the token-guarded POST /cases/{id}/push/subscribe; the keys
(p256dh / auth) are sealed in Postgres. A push is sent once per update (case_updates.pushed_at)
with a short payload: the same 1-2 sentence text the citizen hears, in the case's language.
The push service gets nothing else. A subscription the push service reports gone (404 / 410)
is deleted. Failures never fail polling. Not configured (no VAPID keys): push is skipped.
"""

import base64
import ipaddress
import json
import logging
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from agent import config, sealed
from agent.tracking import followup
from agent.tracking.store import TrackStore

log = logging.getLogger("yojanasaathi.push")

Sender = Callable[[dict[str, Any], str, str, dict[str, Any]], Any]  # (subscription, data, vapid key, claims)
_sender: Sender | None = None


def set_sender(sender: Sender | None) -> None:
    """Tests: capture pushes instead of calling a push service."""
    global _sender
    _sender = sender


def configured() -> bool:
    return bool(config.VAPID_PRIVATE_KEY and config.VAPID_PUBLIC_KEY)


def generate_keys() -> tuple[str, str]:
    """(private, public) as base64url, for VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    private = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(private), b64(public)


def valid_endpoint(url: str) -> bool:
    """https only, and not a private / local address: the server POSTs to this URL, so an
    endpoint pointing inside our network would be a way to make us call it."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or host == "localhost" or host.endswith((".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return ip.is_global


def subscribe(ts: TrackStore, case_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    ts.add_subscription(case_id, endpoint, sealed.seal(case_id, "push", json.dumps({"p256dh": p256dh, "auth": auth})))


def payload(update: dict[str, Any], lang: str) -> dict[str, Any]:
    text, text_en = followup.compose(update, lang)
    return {"title": "YojanaSaathi", "body": text, "body_en": text_en, "lang": lang,
            "tag": update.get("dedupe_key") or str(update.get("id")), "kind": update["kind"],
            "scheme_id": update.get("scheme_id"), "status": (update.get("data") or {}).get("status")}


def _send_one(subscription: dict[str, Any], data: str) -> Any:
    claims = {"sub": config.VAPID_SUBJECT}
    if _sender is not None:
        return _sender(subscription, data, config.VAPID_PRIVATE_KEY or "", claims)
    from pywebpush import webpush

    return webpush(subscription_info=subscription, data=data, vapid_private_key=config.VAPID_PRIVATE_KEY,
                   vapid_claims=claims, ttl=24 * 3600, timeout=10)


def notify(ts: TrackStore, case_id: str, update: dict[str, Any], lang: str) -> int:
    """Push one update to every subscription of the case; returns how many were accepted."""
    if not configured():
        return 0
    subs = ts.subscriptions(case_id)
    if not subs:
        return 0
    data = json.dumps(payload(update, lang), ensure_ascii=False)
    sent = 0
    for s in subs:
        try:
            keys = json.loads(sealed.open_(case_id, "push", s["sealed"]))
            _send_one({"endpoint": s["endpoint"], "keys": keys}, data)
            sent += 1
        except Exception as e:  # noqa: BLE001 (WebPushException, network, sealed): never fails polling
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):  # the browser unsubscribed
                ts.remove_subscription(case_id, s["endpoint"])
            log.warning("push failed (%s%s)", type(e).__name__, f" {status}" if status else "")
    if sent:
        ts.mark_pushed(update["id"])
    return sent


if __name__ == "__main__":
    if sys.argv[1:] == ["keys"]:
        priv, pub = generate_keys()
        print(f"VAPID_PRIVATE_KEY={priv}\nVAPID_PUBLIC_KEY={pub}\nVAPID_SUBJECT=mailto:you@example.org")
    else:
        print("usage: python -m agent.tracking.push keys")
        sys.exit(2)
