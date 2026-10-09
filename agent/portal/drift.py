"""Before every pre-fill: does the live portal still ask for what rules/ and our field map
say? Any difference -> safe-stop (the agent never fills a form it does not know).

The portal's GET /agent/v1/schemes/{id}/requirements/ gives application_fields (English
labels) and required_documents (English names). They must equal the scheme's rules file
(which mirrors the portal seed; tests/test_portal_seed.py) field for field: name, type,
required, options and English label, in the same order; documents by name, in order; and
every field must have a kind in agent/portal/fields.FIELD_KINDS.
Returns short descriptions with field / document NAMES only (they are logged).
"""

from typing import Any

from agent.portal.fields import FIELD_KINDS

COMPARED = ("type", "required", "options")


def compare(requirements: dict[str, Any], scheme: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    live = requirements.get("application_fields")
    if not isinstance(live, list):
        return ["no application_fields in the portal's answer"]
    ours = scheme.get("application_fields", [])
    live_names = [f.get("name") for f in live if isinstance(f, dict)]
    our_names = [f["name"] for f in ours]
    for name in live_names:
        if name not in our_names:
            diffs.append(f"new field {name}")
        if name not in FIELD_KINDS:
            diffs.append(f"field {name} is not in the agent's field map")
    for name in our_names:
        if name not in live_names:
            diffs.append(f"field {name} removed")
    if not diffs and live_names != our_names:
        diffs.append("fields in a different order")
    by_name = {f.get("name"): f for f in live if isinstance(f, dict)}
    for f in ours:
        lf = by_name.get(f["name"])
        if lf is None:
            continue
        for key in COMPARED:
            if lf.get(key) != f.get(key):
                diffs.append(f"field {f['name']}: {key} changed")
        label = lf.get("label")
        if isinstance(label, str) and label != f["label"]["en"]:
            diffs.append(f"field {f['name']}: label changed")
    docs = requirements.get("required_documents")
    our_docs = [d["label"]["en"] for d in scheme.get("documents", [])]
    if not isinstance(docs, list):
        diffs.append("no required_documents in the portal's answer")
    elif docs != our_docs:
        for d in docs:
            if d not in our_docs:
                diffs.append(f"new document {d}")
        for d in our_docs:
            if d not in docs:
                diffs.append(f"document {d} removed")
        if set(docs) == set(our_docs):
            diffs.append("documents in a different order")
    return diffs
