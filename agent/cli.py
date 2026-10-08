"""Text REPL against a running agent: python -m agent.cli [case_id] [kn|hi|en]
(with a lang, every turn sends it, like the voice bot does for long turns)

Talks to /turn exactly like web/voice/phone do. Use it instead of curl for Kannada or
Hindi on Windows: curl.exe receives arguments in the ANSI code page, so non-Latin text
arrives as '????'. Python's console I/O is Unicode-safe.
"""

import os
import sys
import uuid

import httpx

AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8000")


def main() -> None:
    case_id = sys.argv[1] if len(sys.argv) > 1 else f"cli-{uuid.uuid4().hex[:6]}"
    lang = {"lang": sys.argv[2]} if len(sys.argv) > 2 else {}
    print(f"case {case_id} @ {AGENT_URL}  (Ctrl+C to quit)")
    with httpx.Client(base_url=AGENT_URL, timeout=60) as client:
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                continue
            out = client.post(f"/turn/{case_id}", json={"text": text, **lang}).json()
            print(f"agent> {out['reply']}")
            if out["pause"]:
                print(f"       [paused: {out['pause']}]")


if __name__ == "__main__":
    main()
