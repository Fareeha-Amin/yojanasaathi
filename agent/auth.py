"""Session tokens for the web app: JWT (HS256), standard library only.

The web app has no login (the portal's OTP login is the citizen's, not ours). It asks
POST /session for a token: without one it gets a NEW random case ID ("web-<32 hex>") and a
token whose `sub` is that case; with a valid token it gets a fresh token for the same case.
Every /cases/{case_id}/... endpoint requires `Authorization: Bearer <token>` for that case.
/turn stays keyed by case ID (voice bot, agent.cli and old clients keep working); the
random case IDs are what keeps other cases out of reach there.

The signing key is derived from MASTER_KEY (HMAC with a fixed label), so there is no extra
secret to manage, and the document-wrapping key itself never signs anything.
Never log a token.

Dev / demo: `python -m agent.auth <case_id>` prints a token for an existing case (e.g. the
demo reset) and a web-app link that opens that case (`?session=<token>`).
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

from agent import config

ALG = "HS256"
ISSUER = "yojanasaathi"
AUDIENCE = "yojanasaathi-web"
_LABEL = b"yojanasaathi/session-jwt/v1"
_HEADER = {"alg": ALG, "typ": "JWT"}

_key: bytes | None = None


class AuthError(Exception):
    """Missing, malformed, wrongly signed or expired token."""


def set_key(master_key: bytes) -> None:
    global _key
    _key = hmac.new(master_key, _LABEL, hashlib.sha256).digest()


def _signing_key() -> bytes:
    if _key is None:
        raise RuntimeError("auth.set_key() was not called (agent/main.py does it at startup)")
    return _key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: bytes) -> bytes:
    return hmac.new(_signing_key(), signing_input, hashlib.sha256).digest()


def new_case_id() -> str:
    return f"web-{uuid.uuid4().hex}"


def issue(case_id: str, ttl_seconds: float | None = None, now: float | None = None) -> tuple[str, int]:
    """(token, expiry as unix seconds) for this case."""
    iat = int(now if now is not None else time.time())
    exp = iat + int(ttl_seconds if ttl_seconds is not None else config.SESSION_TTL_HOURS * 3600)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": case_id, "iat": iat, "exp": exp}
    head = _b64(json.dumps(_HEADER, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{_b64(_sign(signing_input))}", exp


def verify(token: str, now: float | None = None) -> str:
    """The case ID the token is for. Raises AuthError."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise AuthError("malformed token")
    head_b64, body_b64, sig_b64 = parts
    try:
        header = json.loads(_unb64(head_b64))
        claims = json.loads(_unb64(body_b64))
        sig = _unb64(sig_b64)
    except (ValueError, TypeError):
        raise AuthError("malformed token") from None
    # Only HS256: never "none", never an algorithm the token chooses for itself.
    if not isinstance(header, dict) or header.get("alg") != ALG:
        raise AuthError("unsupported token algorithm")
    if not hmac.compare_digest(sig, _sign(f"{head_b64}.{body_b64}".encode("ascii"))):
        raise AuthError("bad signature")
    if not isinstance(claims, dict) or claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
        raise AuthError("token not issued for this app")
    exp = claims.get("exp")
    if not isinstance(exp, int) or (now if now is not None else time.time()) >= exp:
        raise AuthError("token expired")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("token has no case")
    return sub


def bearer(authorization: str | None) -> str:
    """The token from an Authorization header. Raises AuthError."""
    if not authorization:
        raise AuthError("missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("expected 'Authorization: Bearer <token>'")
    return token.strip()


def main() -> None:
    import sys

    from agent.vault import load_master_key

    if len(sys.argv) != 2:
        print("usage: python -m agent.auth <case_id>")
        raise SystemExit(2)
    set_key(load_master_key(config.MASTER_KEY))
    token, exp = issue(sys.argv[1])
    print(f"case:    {sys.argv[1]}")
    print(f"expires: {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}")
    print(f"token:   {token}")
    print(f"web app: http://localhost:5173/?session={token}")


if __name__ == "__main__":
    main()
