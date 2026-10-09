"""Read the mock portal's scheme seed (github.com/ayush81233/mock,
backend/schemes/management/commands/seed_schemes.py) without running it.

The seed is the source of truth for our rules files: scheme IDs, eligibility thresholds,
document lists and kn/hi wording. tests/test_portal_seed.py checks every rules/*.json
against a snapshot of it (tests/fixtures/portal_seed.json), and against a live checkout
when MOCK_PORTAL_REPO points to one.

    python -m agent.portal_seed <path to seed_schemes.py>   # print the trimmed snapshot
"""

import ast
import json
import sys
from pathlib import Path
from typing import Any

SEED_PATH = "backend/schemes/management/commands/seed_schemes.py"


def parse(path: Path) -> list[dict[str, Any]]:
    """The literal `schemes = [...]` list from seed_schemes.py (ast, never executed)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "schemes" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"no `schemes = [...]` literal in {path}")


def trim(schemes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only what our rules files mirror."""
    out = []
    for s in schemes:
        tr = s.get("translations", {})
        out.append({
            "id": s["id"],
            "title": {"en": s["title"], **{k: tr[k]["title"] for k in ("kn", "hi")}},
            "eligibility_rules": s["eligibility_rules"],
            "documents": {"en": s["documents"], **{k: tr[k]["documents"] for k in ("kn", "hi")}},
            "application_fields": [
                {
                    "name": f["name"],
                    "type": f["type"],
                    "required": f["required"],
                    **({"options": f["options"]} if "options" in f else {}),
                    "label": {"en": f["label"], **{
                        k: next(x["label"] for x in tr[k]["application_fields"] if x["name"] == f["name"])
                        for k in ("kn", "hi")}},
                }
                for f in s["application_fields"]
            ],
        })
    return sorted(out, key=lambda s: s["id"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(trim(parse(Path(sys.argv[1]))), ensure_ascii=False, indent=2))
