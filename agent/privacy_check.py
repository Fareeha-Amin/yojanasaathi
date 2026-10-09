"""Scan the database and the vault for anything that must not be stored.

    python -m agent.privacy_check

Checks:
1. every value in our tables AND the checkpointer's tables (case memory, incl. its binary
   blobs) for a full Aadhaar-like number (12 digits; masked "XXXX XXXX 1234" is fine);
2. Phase 4: the same values for what the application form and the portal must never leave
   in clear text: a 10-digit mobile number, an IFSC code, a portal delegation or agent key
   (yjs_del_... / yjs_ag_...). Form answers are sealed (agent/sealed.py, base64 ciphertext,
   which these patterns cannot match inside) and shown masked;
3. our tables have no binary (bytea) column, i.e. no place for document contents;
4. every file in the vault (documents AND browser screenshots) is an encrypted envelope.
UUIDs, hex digests and LangGraph channel versions are removed before scanning (they can
contain digit runs that are not personal numbers).
Exit code 0 = clean, 1 = findings (printed with table and column, never the value).
"""

import re
import sys
from pathlib import Path

from agent import config
from agent.db import Store, open_store
from agent.privacy import AADHAAR_RE
from agent.vault import MAGIC

OUR_TABLES = ["citizens", "profiles", "cases", "case_events", "documents", "screenshots", "audit_log"]
CHECKPOINT_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]
# Removed before scanning: UUIDs (checkpoint / task IDs), LangGraph channel versions,
# f"{n:032}.{random.random():016}" (PostgresSaver.get_next_version), whose random part
# sometimes has exactly 12 digits, and hex digests (sha256 of a document, fingerprints)
# that contain at least one letter (so a plain digit run is never treated as noise).
# Version first: 32 digits also look like a dash-less UUID, which would leave the float behind.
NOISE_RE = re.compile(r"\d{32}\.[\d.e+\-]+"  # the float part has its own "." (and maybe e-05)
                      r"|[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
                      r"|\b(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b")
# Outside a word (base64 ciphertext is one long word of letters, digits, "-" and "_").
_EDGE_L, _EDGE_R = r"(?<![A-Za-z0-9_.\-])", r"(?![A-Za-z0-9_.\-])"  # "." too: random decimals in checkpoints
PORTAL_PATTERNS = {
    "mobile number": re.compile(_EDGE_L + r"(?:\+?91[ \-]?)?[6-9]\d{4}[ \-]?\d{5}" + _EDGE_R),
    "IFSC code": re.compile(_EDGE_L + r"[A-Z]{4}0[A-Z0-9]{6}" + _EDGE_R),
    "portal token": re.compile(r"yjs_(?:del|ag)_[A-Za-z0-9]{6,}"),
}


def has_full_aadhaar(text: str) -> bool:
    return AADHAAR_RE.search(NOISE_RE.sub(" ", text)) is not None


def portal_leaks(text: str) -> list[str]:
    """Which Phase 4 secrets appear in clear text (names only)."""
    clean = NOISE_RE.sub(" ", text)
    return [name for name, rx in PORTAL_PATTERNS.items() if rx.search(clean)]


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        # "replace", not "ignore": msgpack length bytes become U+FFFD, a separator, so two
        # adjacent strings in a checkpoint blob are never glued into one word
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def scan_database(store: Store) -> list[str]:
    findings = []
    with store.pool.connection() as conn:
        for table in OUR_TABLES + CHECKPOINT_TABLES:
            for row in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 (fixed names)
                for col, value in row.items():
                    if value is None:
                        continue
                    text = _text(value)
                    if has_full_aadhaar(text):
                        findings.append(f"{table}.{col}: full Aadhaar-like number")
                    findings += [f"{table}.{col}: {what} in clear text" for what in portal_leaks(text)]
        binary = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type = 'bytea' AND table_name = ANY(%s)",
            (OUR_TABLES,)).fetchall()
        findings += [f"{r['table_name']}.{r['column_name']}: binary column in our tables" for r in binary]
    return findings


def scan_vault(root: Path) -> list[str]:
    if not root.exists():
        return []
    return [f"vault/{f.name}: not an encrypted vault file" for f in root.iterdir()
            if f.is_file() and not f.read_bytes()[:len(MAGIC)] == MAGIC]


def main() -> int:
    store = open_store(config.DATABASE_URL, max_size=1)
    try:
        findings = scan_database(store) + scan_vault(config.VAULT_DIR)
    finally:
        store.close()
    for f in findings:
        print("FOUND", f)
    print("clean: no full Aadhaar numbers, mobiles, IFSC codes or portal tokens; no document contents"
          if not findings else f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
