"""Document mapper: the documents a scheme needs for this citizen, minus what they have.

Deterministic (no LLM). A document's "when" is a JSON Logic condition on the profile; an
unknown condition keeps the document on the list (better to ask for one too many).
Status per document: "have" (citizen said so), "missing" (citizen said they don't have
it), "needed" (not discussed yet). Phase 3 replaces the spoken have/missing with the
encrypted document vault.
"""

from typing import Any

from agent.rules import UNKNOWN, evaluate


def build(scheme: dict[str, Any], profile: dict[str, Any],
          have: list[str] | None = None, missing: list[str] | None = None) -> list[dict[str, str]]:
    have_set, missing_set = set(have or []), set(missing or [])
    out = []
    for d in scheme["documents"]:
        when = d.get("when")
        if when is not None:
            r = evaluate(when, profile)
            if r is not UNKNOWN and not r:
                continue
        doc = d["doc"]
        st = "have" if doc in have_set else "missing" if doc in missing_set else "needed"
        out.append({"doc": doc, "status": st})
    return out
