"""Scan the database and the vault for anything that must not be stored.

    python -m agent.privacy_check

Checks:
1. every value in our tables AND the checkpointer's tables (case memory, incl. its binary
   blobs) for a full Aadhaar-like number (12 digits; masked "XXXX XXXX 1234" is fine);
2. our tables have no binary (bytea) column, i.e. no place for document contents;
3. every file in the vault is an encrypted vault envelope (no plaintext document).
UUIDs and LangGraph channel versions are removed before scanning (both can contain a
12-digit run that is not an Aadhaar number).
Exit code 0 = clean, 1 = findings (printed with table and column, never the value).
"""

import re
import sys
from pathlib import Path

from agent import config
from agent.db import Store, open_store
from agent.privacy import AADHAAR_RE
from agent.vault import MAGIC

OUR_TABLES = ["citizens", "profiles", "cases", "case_events", "documents", "audit_log"]
CHECKPOINT_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]
# Removed before scanning: UUIDs (checkpoint / task IDs) and LangGraph channel versions,
# f"{n:032}.{random.random():016}" (PostgresSaver.get_next_version), whose random part
# sometimes has exactly 12 digits.
# Version first: 32 digits also look like a dash-less UUID, which would leave the float behind.
NOISE_RE = re.compile(r"\d{32}\.[\d.e+\-]+"  # the float part has its own "." (and maybe e-05)
                      r"|[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")


def has_full_aadhaar(text: str) -> bool:
    return AADHAAR_RE.search(NOISE_RE.sub(" ", text)) is not None


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="ignore")
    return str(value)


def scan_database(store: Store) -> list[str]:
    findings = []
    with store.pool.connection() as conn:
        for table in OUR_TABLES + CHECKPOINT_TABLES:
            for row in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 (fixed names)
                for col, value in row.items():
                    if value is not None and has_full_aadhaar(_text(value)):
                        findings.append(f"{table}.{col}: full Aadhaar-like number")
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
    print("clean: no full Aadhaar numbers, no document contents" if not findings
          else f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
