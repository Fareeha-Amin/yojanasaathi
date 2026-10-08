"""Human gate: turn the citizen's reply at a confirm pause into yes / no / unclear.

Deterministic on purpose (design rule 2): the LLM never decides whether we submit.
Default-deny: anything that is not a clear yes is "no" or "unclear", and only "yes"
lets the graph move past the confirm node. A reply that mixes yes and no is unclear.
"""

import unicodedata
from typing import Literal

Decision = Literal["yes", "no", "unclear"]

YES_WORDS = {
    # en
    "yes", "y", "yeah", "yep", "confirm", "submit", "approve",
    # kn (script + romanised)
    "ಹೌದು", "ಹೌದ್", "ಸರಿ", "ಸಲ್ಲಿಸಿ", "haudu", "houdu", "howdu", "sari",
    # hi (script + romanised)
    "हाँ", "हां", "हा", "haan", "haa", "han",
}
YES_PHRASES = {"theek hai", "ठीक है", "ji haan", "जी हाँ", "जी हां"}

NO_WORDS = {
    # en
    "no", "n", "nope", "dont", "cancel", "stop", "wait",
    # kn
    "ಇಲ್ಲ", "ಬೇಡ", "illa", "beda",
    # hi
    "नहीं", "नही", "मत", "रुको", "nahi", "nahin", "mat", "ruko",
}
NO_PHRASES = {"not now", "do not", "not yet"}


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text).lower().replace("'", "").replace("’", "")
    # Replace punctuation (incl. the Devanagari danda) with spaces; keep combining marks,
    # which Kannada and Devanagari vowel signs depend on. Drop invisible format chars
    # (BOM, ZWJ/ZWNJ) that keyboards and STT engines sometimes insert.
    cleaned = "".join(
        " " if (cat := unicodedata.category(ch)).startswith("P") else "" if cat == "Cf" else ch
        for ch in text
    )
    return cleaned.split()


def parse_decision(text: str) -> Decision:
    toks = _tokens(text)
    joined = f" {' '.join(toks)} "
    has_yes = any(t in YES_WORDS for t in toks) or any(f" {p} " in joined for p in YES_PHRASES)
    has_no = any(t in NO_WORDS for t in toks) or any(f" {p} " in joined for p in NO_PHRASES)
    if has_yes and not has_no:
        return "yes"
    if has_no and not has_yes:
        return "no"
    return "unclear"
