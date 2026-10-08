"""Language plumbing between Sarvam and /turn. No Pipecat imports, so the agent's
test suite can cover it.

STT side: Saaras auto-detects the language of each utterance ("kn-IN", "hi-IN", ...);
we pass it to /turn as "kn" | "hi" | "en". Anything else (another Indian language,
nothing detected, or a turn too short to trust) is sent without "lang", so the case
keeps its previous language.

TTS side: Bulbul is told the language of the text it must read, decided by the script
the reply is written in.
"""

from typing import Literal

TurnLang = Literal["kn", "hi", "en"]

_SUPPORTED: tuple[TurnLang, ...] = ("kn", "hi", "en")
_KANNADA = range(0x0C80, 0x0D00)
_DEVANAGARI = range(0x0900, 0x0980)


def turn_lang(code: str | None) -> TurnLang | None:
    """Sarvam/Pipecat language code ("kn-IN", "en-US", "hi") -> /turn lang, or None."""
    if not code:
        return None
    base = str(code).split("-")[0].strip().lower()
    return base if base in _SUPPORTED else None  # type: ignore[return-value]


# Language ID on one or two words is unreliable: Saaras hears a lone "हाँ" as "Yeah."
# (en-IN). Short turns ("ಹೌದು", "हाँ", "no") therefore don't change the case's language.
MIN_WORDS_FOR_LANG = 3


def turn_lang_for(text: str, code: str | None) -> TurnLang | None:
    """The "lang" to send with this turn: the detected language, if the turn is long
    enough to trust it."""
    return turn_lang(code) if len(text.split()) >= MIN_WORDS_FOR_LANG else None


def tts_language(text: str) -> str:
    """Bulbul target language for a reply: Kannada script -> kn-IN, Devanagari -> hi-IN,
    otherwise en-IN. The script, not the citizen's last language, decides how the text
    can be read aloud."""
    kn = sum(ord(c) in _KANNADA for c in text)
    hi = sum(ord(c) in _DEVANAGARI for c in text)
    if kn == 0 and hi == 0:
        return "en-IN"
    return "kn-IN" if kn >= hi else "hi-IN"


# Spoken when the client connects. Kannada first (design rule); the agent takes over
# from the citizen's first reply.
GREETING = "ನಮಸ್ಕಾರ, ನಾನು ಯೋಜನಾಸಾಥಿ. ಹೇಳಿ, ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?"

# Spoken when /turn fails (agent down, timeout). Nothing was decided or submitted.
AGENT_UNREACHABLE: dict[TurnLang, str] = {
    "kn": "ಕ್ಷಮಿಸಿ, ಈಗ ಸೇವೆ ಸಿಗುತ್ತಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    "hi": "माफ़ कीजिए, अभी सेवा उपलब्ध नहीं है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
    "en": "Sorry, the service is not available right now. Please try again in a little while.",
}
