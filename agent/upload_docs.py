"""Upload a scheme's documents for a case from the command line (until the web app's
Documents screen is ready). The agent must be running.

    python -m agent.upload_docs demo-case-1 pension-001            # dummy PDFs, all 5
    python -m agent.upload_docs demo-case-1 pension-001 C:\\docs   # your own files
    python -m agent.upload_docs demo-case-1 pension-001 --list     # what is stored now

Your own files: a folder holding one file per document, named after the document id
(identity_proof.pdf, age_proof.jpg, residence_proof.png, income_certificate.pdf,
bank_account_details.pdf). PDF / JPG / PNG, each under 5 MB (the portal's limit).

It gives document consent for the case (that is what the Documents screen's switch does),
signs a case token with MASTER_KEY from .env, and PUTs each file to
/cases/<case>/documents/<doc>. Files are encrypted in the vault; nothing here prints them.
The dummy PDFs say "DEMO DOCUMENT" and hold no personal data.
"""

import os
import sys
from pathlib import Path

import httpx

from agent import auth, config, rules
from agent.vault import load_master_key

TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
MAX_BYTES = 5 * 1024 * 1024


def dummy_pdf(doc: str) -> bytes:
    """A tiny valid one-page PDF (no personal data)."""
    text = f"DEMO DOCUMENT - {doc.replace('_', ' ')}"
    stream = f"BT /F1 18 Tf 60 740 Td ({text}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def find_file(folder: Path, doc: str) -> Path | None:
    for ext in TYPES:
        p = folder / f"{doc}{ext}"
        if p.is_file():
            return p
    return None


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    case_id, scheme_id = args[0], args[1]
    folder = Path(args[2]) if len(args) > 2 else None
    schemes = rules.load_schemes()
    if scheme_id not in schemes:
        print(f"unknown scheme {scheme_id}; one of {sorted(schemes)}")
        return 2
    base = os.getenv("AGENT_URL") or "http://127.0.0.1:8000"
    token = auth.issue(case_id)[0] if _key_ready() else None
    if token is None:
        return 1
    http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    try:
        if "--list" in argv:
            for d in http.get(f"/cases/{case_id}/documents").raise_for_status().json():
                print(f"  {d['doc_type']:<24} {d['content_type']:<16} {d['size_bytes']} bytes")
            return 0
        http.put(f"/cases/{case_id}/consent", json={"documents": True}).raise_for_status()
        failed = 0
        for d in schemes[scheme_id]["documents"]:
            doc = d["doc"]
            if folder is None:
                data, ctype, name = dummy_pdf(doc), "application/pdf", "dummy PDF"
            else:
                path = find_file(folder, doc)
                if path is None:
                    print(f"  MISSING  {doc}: no {doc}.pdf / .jpg / .png in {folder}")
                    failed += 1
                    continue
                data, ctype, name = path.read_bytes(), TYPES[path.suffix.lower()], path.name
                if len(data) > MAX_BYTES:
                    print(f"  TOO BIG  {doc}: {name} is over 5 MB (the portal's limit)")
                    failed += 1
                    continue
            r = http.put(f"/cases/{case_id}/documents/{doc}", content=data, headers={"Content-Type": ctype})
            if r.status_code == 200:
                print(f"  stored   {doc} ({name})")
            else:
                print(f"  FAILED   {doc}: {r.status_code} {r.text[:120]}")
                failed += 1
        print("done: all documents stored" if not failed else f"{failed} document(s) not stored")
        return 1 if failed else 0
    except httpx.ConnectError:
        print(f"cannot reach the agent at {base}: start it first (uvicorn agent.main:app)")
        return 1
    finally:
        http.close()


def _key_ready() -> bool:
    try:
        auth.set_key(load_master_key(config.MASTER_KEY))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"MASTER_KEY problem in .env: {e}")
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
