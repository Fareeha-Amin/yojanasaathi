"""Voice language plumbing: STT code -> /turn lang, reply script -> Bulbul language."""

import pytest

from voice.lang import AGENT_UNREACHABLE, GREETING, tts_language, turn_lang, turn_lang_for


@pytest.mark.parametrize(
    "code, expected",
    [
        ("kn-IN", "kn"),
        ("hi-IN", "hi"),
        ("en-IN", "en"),
        ("en-US", "en"),
        ("kn", "kn"),
        ("HI-in", "hi"),
        ("ta-IN", None),  # other Indian languages: don't override the case's lang
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_turn_lang(code, expected):
    assert turn_lang(code) == expected


@pytest.mark.parametrize(
    "text, code, expected",
    [
        ("ನನಗೆ ಪಿಂಚಣಿ ಬೇಕು", "kn-IN", "kn"),
        ("मुझे पेंशन चाहिए", "hi-IN", "hi"),
        ("Yeah.", "en-IN", None),  # Saaras' reading of a lone "हाँ": must not flip a Hindi case
        ("ಹೌದು.", "kn-IN", None),
        ("ಹೌದು ಸಲ್ಲಿಸಿ", "kn-IN", None),
        ("vanakkam ungalukku eppadi", "ta-IN", None),
    ],
)
def test_turn_lang_for_ignores_short_turns(text, code, expected):
    assert turn_lang_for(text, code) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ನಿಮಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತದೆ.", "kn-IN"),
        ("आपको पेंशन मिल सकती है।", "hi-IN"),
        ("You qualify for the pension.", "en-IN"),
        ("ಅರ್ಜಿ ID DEMO-0001 ಆಗಿದೆ.", "kn-IN"),  # Kannada with an English ID
        ("Application ID DEMO-0001 है।", "hi-IN"),
        ("", "en-IN"),
    ],
)
def test_tts_language(text, expected):
    assert tts_language(text) == expected


def test_fixed_phrases_match_their_language():
    assert tts_language(GREETING) == "kn-IN"
    assert {lang: tts_language(t) for lang, t in AGENT_UNREACHABLE.items()} == {
        "kn": "kn-IN", "hi": "hi-IN", "en": "en-IN",
    }
