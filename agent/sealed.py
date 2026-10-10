"""Sealed values: application-form answers (date of birth, mobile, bank account, IFSC, ...)
and the portal delegation token, kept in the case memory as AES-256-GCM ciphertext.

The graph state is checkpointed in Postgres, so anything sensitive in it is sealed here
first. The key is derived from MASTER_KEY (HMAC with its own label, like the session
key), so no new secret. The associated data binds each value to its case and field: a
sealed value copied into another case or field does not open. "Delete my data" removes
the checkpoint, and with it the ciphertext.

    s = seal("case-1", "bank_account_number", "123456789012")   # "ysv1:..."
    open_("case-1", "bank_account_number", s)                    # "123456789012"
"""

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "ysv1:"
_key: bytes | None = None


class SealedError(Exception):
    pass


def set_key(master_key: bytes) -> None:
    global _key
    _key = hmac.new(master_key, b"yojanasaathi sealed form values v1", hashlib.sha256).digest()


def _aes() -> AESGCM:
    if _key is None:
        raise SealedError("sealing key not set (agent.main sets it from MASTER_KEY)")
    return AESGCM(_key)


def _aad(case_id: str, name: str) -> bytes:
    return f"{PREFIX}{case_id}\x00{name}".encode()


def seal(case_id: str, name: str, value: str) -> str:
    nonce = os.urandom(12)
    ct = _aes().encrypt(nonce, value.encode(), _aad(case_id, name))
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def open_(case_id: str, name: str, sealed: str) -> str:
    if not isinstance(sealed, str) or not sealed.startswith(PREFIX):
        raise SealedError("not a sealed value")
    try:
        blob = base64.urlsafe_b64decode(sealed[len(PREFIX):])
        return _aes().decrypt(blob[:12], blob[12:], _aad(case_id, name)).decode()
    except (InvalidTag, ValueError):
        raise SealedError("cannot open: wrong key, case or field") from None


def is_sealed(v: object) -> bool:
    return isinstance(v, str) and v.startswith(PREFIX)
