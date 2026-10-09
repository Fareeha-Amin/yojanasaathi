"""Encrypted document vault: AES-256-GCM files on disk, metadata in Postgres.

Each file gets its own random 256-bit key. That key is encrypted ("wrapped") with
MASTER_KEY (.env; KMS in production) and stored in the file's header, so Postgres holds
metadata only (type, owner, hash, storage key, expiry) and cannot decrypt anything.

File layout (<VAULT_DIR>/<storage_key>.ysv):
    b"YSV1" | wrap nonce (12) | wrapped key (32 + 16 tag) | data nonce (12) | ciphertext + tag
Both encryptions take b"YSV1" + storage_key as associated data, so a file renamed to
another document's key fails to decrypt.

Contents never leave this module except through read() (Phase 4: the browser worker
decrypts to memory, uploads to the portal, drops it). Nothing here talks to the LLM, and
no endpoint returns a document's contents. Every store / read / delete is audited.
Documents expire DOC_RETENTION_HOURS after the case's latest submission; purge_expired()
deletes them (agent/main.py runs it every VAULT_PURGE_SECONDS).
"""

import base64
import binascii
import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent import audit
from agent.db import StartupError, Store

log = logging.getLogger("yojanasaathi.vault")

MAGIC = b"YSV1"
NONCE = 12
WRAPPED = 32 + 16
HEADER = len(MAGIC) + NONCE + WRAPPED + NONCE
ORPHAN_GRACE_SECONDS = 600  # a file with no metadata row older than this is removed


class VaultError(Exception):
    pass


def load_master_key(b64: str | None) -> bytes:
    gen = 'python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"'
    if not b64:
        raise StartupError(f"MASTER_KEY is not set. Add a 32-byte base64 key to .env; generate one with:\n  {gen}")
    try:
        key = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise StartupError(f"MASTER_KEY is not valid base64. Generate one with:\n  {gen}") from None
    if len(key) != 32:
        raise StartupError(f"MASTER_KEY must be 32 bytes (AES-256), got {len(key)}. Generate one with:\n  {gen}")
    return key


def seal(master: bytes, storage_key: str, data: bytes) -> bytes:
    aad = MAGIC + storage_key.encode()
    file_key = AESGCM.generate_key(bit_length=256)
    n1, n2 = os.urandom(NONCE), os.urandom(NONCE)
    wrapped = AESGCM(master).encrypt(n1, file_key, aad)
    return MAGIC + n1 + wrapped + n2 + AESGCM(file_key).encrypt(n2, data, aad)


def unseal(master: bytes, storage_key: str, blob: bytes) -> bytes:
    if len(blob) < HEADER + 16 or not blob.startswith(MAGIC):
        raise VaultError("not a vault file")
    aad = MAGIC + storage_key.encode()
    i = len(MAGIC)
    n1, wrapped, n2 = blob[i:i + NONCE], blob[i + NONCE:i + NONCE + WRAPPED], blob[i + NONCE + WRAPPED:HEADER]
    try:
        file_key = AESGCM(master).decrypt(n1, wrapped, aad)
        return AESGCM(file_key).decrypt(n2, blob[HEADER:], aad)
    except InvalidTag:
        raise VaultError("decryption failed: wrong key, wrong file or tampered") from None


def public(row: dict[str, Any]) -> dict[str, Any]:
    """What a client may see about a document: never contents, Aadhaar as last 4 only."""
    last4 = row.get("aadhaar_last4")
    return {
        "id": str(row["id"]), "doc_type": row["doc_type"], "content_type": row["content_type"],
        "size_bytes": row["size_bytes"], "aadhaar": f"XXXX XXXX {last4}" if last4 else None,
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
    }


class Vault:
    def __init__(self, store: Store, root: Path, master_key: bytes):
        self.store = store
        self.root = Path(root)
        self.master = master_key
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        return self.root / f"{storage_key}.ysv"

    def _unlink(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)

    def put(self, case_id: str, doc_type: str, data: bytes, content_type: str,
            aadhaar_last4: str | None = None) -> dict[str, Any]:
        """Encrypt and store; replaces an earlier upload of the same type. Caller checks consent."""
        storage_key = uuid.uuid4().hex
        path = self._path(storage_key)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(seal(self.master, storage_key, data))
        tmp.replace(path)
        try:
            row, replaced = self.store.add_document(case_id, doc_type, content_type, len(data),
                                                    hashlib.sha256(data).hexdigest(), storage_key,
                                                    aadhaar_last4)
        except Exception:
            self._unlink(storage_key)
            raise
        if replaced:
            self._unlink(replaced)
        return row

    def read(self, case_id: str, doc_id: str, actor: str = "agent") -> bytes:
        """Decrypt to memory (Phase 4: upload to the portal, then drop it)."""
        row = self.store.document(case_id, doc_id)
        if row is None:
            raise VaultError("no such document")
        data = unseal(self.master, row["storage_key"], self._path(row["storage_key"]).read_bytes())
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise VaultError("hash mismatch")
        audit.log_event(actor, "document_read", case_id=case_id, detail={"doc_id": doc_id,
                                                                         "doc_type": row["doc_type"]})
        return data

    def delete(self, case_id: str, doc_id: str, actor: str = "citizen",
               action: str = "document_deleted") -> bool:
        row = self.store.document(case_id, doc_id)
        if row is None:
            return False
        self.store.delete_document_row(str(row["id"]), actor, action)
        self._unlink(row["storage_key"])
        return True

    def delete_case(self, case_id: str, actor: str = "citizen") -> int:
        rows = self.store.documents(case_id)
        for row in rows:
            self.store.delete_document_row(str(row["id"]), actor, "document_deleted")
            self._unlink(row["storage_key"])
        return len(rows)

    def purge_expired(self) -> int:
        """Delete documents past their expiry, and files whose metadata row is gone."""
        n = 0
        for row in self.store.expired_documents():
            self.store.delete_document_row(str(row["id"]), "system", "document_auto_deleted")
            self._unlink(row["storage_key"])
            n += 1
        known = self.store.storage_keys()
        now = time.time()
        for f in self.root.glob("*.ysv"):
            if f.stem not in known and now - f.stat().st_mtime > ORPHAN_GRACE_SECONDS:
                f.unlink(missing_ok=True)
                log.info("removed orphan vault file %s", f.name)
        return n
