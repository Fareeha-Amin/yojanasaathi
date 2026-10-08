"""Deterministic number reading for kn / hi / en. The LLM never converts number words.

Order of trust for numbers (see agent/facts.py): digits from STT (Saaras writes numerals),
then this parser for spoken number words, and an LLM value only as a flagged fallback.

Covered:
- digits in any script ("62", "೬೨", "६२"), Indian grouping "1,20,000", decimals "1.5",
  suffixes right after digits: k (thousand), l / lac / lakh, cr
- English: zero..nineteen, twenty..ninety (also "sixty-two"), hundred, thousand, lakh /
  lakhs / lac / lacs, million, crore; "and" between number words; "a lakh"
- Hindi (Devanagari): the full 0-99 table, सौ, हज़ार, लाख, करोड़; डेढ़ (1.5), ढाई (2.5),
  आधा (0.5); साढ़े (+0.5), सवा (+0.25), पौने (-0.25) before a number. Nukta and
  chandrabindu are normalised away (हज़ार = हजार, पाँच = पांच).
- Kannada: units, 10-19, tens, every fused compound 21-99 (ಅರವತ್ತೆರಡು, ಇಪ್ಪತ್ಮೂರು),
  hundreds (ಇನ್ನೂರು .. ಒಂಬೈನೂರು), ನೂರು, ಸಾವಿರ, ಲಕ್ಷ, ಕೋಟಿ and their genitive forms
  (ಸಾವಿರದ, ಲಕ್ಷದ, ನೂರ); ಒಂದೂವರೆ (1.5) .. ನಾಲ್ಕೂವರೆ, ಅರ್ಧ (0.5)
- Romanised Hindi / Kannada (ek, do, bees, hazaar, ondu, ippattu, saavira, laksha, ...):
  "weak" words, counted only when the span also has a multiplier or another number word,
  because many collide with English ("do", "teen", "nau").
- Mixed digits and words: "1 lakh 20 thousand", "1 ಲಕ್ಷ 20 ಸಾವಿರ".
"""

import re
import unicodedata
from dataclasses import dataclass

_NUKTA = "़"
_CHANDRABINDU, _ANUSVARA = "ँ", "ं"


def normalize(text: str) -> str:
    """NFC, lower case, no nukta, chandrabindu -> anusvara, no invisible format chars."""
    text = unicodedata.normalize("NFC", text).lower()
    text = text.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


_DIGITS = re.compile(r"(\d+(?:,\d+)*(?:\.\d+)?)")


def tokenize(text: str) -> list[str]:
    """Words and digit groups. Punctuation splits words; "₹" is kept as its own token.
    (Python's \\w does not match Kannada/Devanagari vowel signs, so no \\w here.)"""
    out: list[str] = []
    for i, piece in enumerate(_DIGITS.split(normalize(text))):
        if i % 2:
            out.append(piece)
            continue
        cleaned = "".join(
            " ₹ " if ch == "₹" else " " if unicodedata.category(ch)[0] in "PS" else ch
            for ch in piece
        )
        out.extend(cleaned.split())
    return out


# --- word tables -------------------------------------------------------------------------

_EN_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
]
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
            "seventy": 70, "eighty": 80, "ninety": 90}

_HI_0_99 = (
    "शून्य एक दो तीन चार पांच छह सात आठ नौ दस "
    "ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस "
    "इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस "
    "इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
    "इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ "
    "इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर "
    "इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे "
    "इक्यानबे बानबे तिरानबे चौरानबे पंचानबे छियानबे सत्तानबे अट्ठानबे निन्यानबे"
).split()
_HI_VARIANTS = {
    "पाँच": 5, "छः": 6, "छे": 6, "पन्द्रह": 15, "उन्तीस": 29, "इकत्तीस": 31, "चौवालीस": 44,
    "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94, "पंचानवे": 95,
    "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99, "अठत्तर": 78,
}

_KN_UNITS = {"ಸೊನ್ನೆ": 0, "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5,
             "ಆರು": 6, "ಏಳು": 7, "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9, "ಒಂಭತ್ತು": 9}
_KN_TEENS = {"ಹತ್ತು": 10, "ಹನ್ನೊಂದು": 11, "ಹನ್ನೆರಡು": 12, "ಹದಿಮೂರು": 13, "ಹದಿನಾಲ್ಕು": 14,
             "ಹದಿನೈದು": 15, "ಹದಿನಾರು": 16, "ಹದಿನೇಳು": 17, "ಹದಿನೆಂಟು": 18,
             "ಹತ್ತೊಂಬತ್ತು": 19, "ಹತ್ತೊಂಭತ್ತು": 19}
_KN_TENS = {"ಇಪ್ಪತ್ತು": 20, "ಮೂವತ್ತು": 30, "ನಲವತ್ತು": 40, "ನಲ್ವತ್ತು": 40, "ಐವತ್ತು": 50,
            "ಅರವತ್ತು": 60, "ಎಪ್ಪತ್ತು": 70, "ಎಂಬತ್ತು": 80, "ಎಂಭತ್ತು": 80, "ತೊಂಬತ್ತು": 90}
# Vowel-initial units fuse onto the tens stem as a vowel sign: ಅರವತ್ತ + ೆರಡು = ಅರವತ್ತೆರಡು
_KN_FUSED_VOWEL = {1: ["ೊಂದು"], 2: ["ೆರಡು"], 5: ["ೈದು"], 6: ["ಾರು"], 7: ["ೇಳು"],
                   8: ["ೆಂಟು"], 9: ["ೊಂಬತ್ತು", "ೊಂಭತ್ತು"]}
# Consonant-initial units: ಅರವ + ತ್ + ಮೂರು = ಅರವತ್ಮೂರು (also heard as ಅರವತ್ತಮೂರು)
_KN_FUSED_CONS = {3: "ಮೂರು", 4: "ನಾಲ್ಕು"}
_KN_HUNDREDS = {"ಇನ್ನೂರು": 200, "ಮುನ್ನೂರು": 300, "ನಾನೂರು": 400, "ನಾನ್ನೂರು": 400,
                "ಐನೂರು": 500, "ಆರುನೂರು": 600, "ಆರ್ನೂರು": 600, "ಏಳುನೂರು": 700,
                "ಏಳ್ನೂರು": 700, "ಎಂಟುನೂರು": 800, "ಎಂಟ್ನೂರು": 800, "ಒಂಬೈನೂರು": 900,
                "ಒಂಬತ್ತುನೂರು": 900}

_ROMAN_WEAK = {
    # hi
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "bees": 20, "tees": 30,
    "chalis": 40, "chaalis": 40, "pachas": 50, "pachaas": 50, "saath": 60, "sattar": 70,
    "assi": 80, "nabbe": 90, "dedh": 1.5, "dhai": 2.5,
    # kn
    "ondu": 1, "eradu": 2, "mooru": 3, "muru": 3, "naalku": 4, "nalku": 4, "aidu": 5,
    "aaru": 6, "elu": 7, "yelu": 7, "entu": 8, "yentu": 8, "ombattu": 9, "hattu": 10,
    "ippattu": 20, "moovattu": 30, "nalavattu": 40, "aivattu": 50, "aravattu": 60,
    "eppattu": 70, "embattu": 80, "tombattu": 90, "ondoovare": 1.5,
}


def _kn_compounds() -> dict[str, int]:
    out: dict[str, int] = {}
    for word, tens in _KN_TENS.items():
        stem = word[:-1]  # drop final "ು": ಅರವತ್ತು -> ಅರವತ್ತ
        for unit, signs in _KN_FUSED_VOWEL.items():
            for sign in signs:
                out[stem + sign] = tens + unit
        for unit, unit_word in _KN_FUSED_CONS.items():
            out[word[:-4] + "ತ್" + unit_word] = tens + unit  # ಅರವ + ತ್ + ಮೂರು
            out[stem + unit_word] = tens + unit  # ಅರವತ್ತ + ಮೂರು
    return out


_UNITS: dict[str, float] = {}
_UNITS.update({w: i for i, w in enumerate(_EN_UNITS)})
_UNITS.update(_EN_TENS)
_UNITS.update({w: i for i, w in enumerate(_HI_0_99)})
_UNITS.update(_HI_VARIANTS)
_UNITS.update({"डेढ़": 1.5, "ढाई": 2.5, "अढ़ाई": 2.5, "आधा": 0.5})
_UNITS.update(_KN_UNITS)
_UNITS.update(_KN_TEENS)
_UNITS.update(_KN_TENS)
_UNITS.update(_kn_compounds())
_UNITS.update(_KN_HUNDREDS)
_UNITS.update({w[:-1]: v for w, v in _KN_HUNDREDS.items()})  # genitive: ಇನ್ನೂರ ಐವತ್ತು
_UNITS.update({"ಒಂದೂವರೆ": 1.5, "ಒಂದುವರೆ": 1.5, "ಎರಡೂವರೆ": 2.5, "ಮೂರೂವರೆ": 3.5,
               "ನಾಲ್ಕೂವರೆ": 4.5, "ಅರ್ಧ": 0.5})

_HUNDRED = {"hundred", "सौ", "ನೂರು", "ನೂರ", "sau", "nooru", "nuru"}
_BIG: dict[str, int] = {
    "thousand": 1000, "हज़ार": 1000, "हजार": 1000, "ಸಾವಿರ": 1000, "ಸಾವಿರದ": 1000,
    "ಸಾವಿರಾ": 1000, "hazaar": 1000, "hazar": 1000, "hajar": 1000, "saavira": 1000,
    "savira": 1000,
    "lakh": 100000, "lakhs": 100000, "lac": 100000, "lacs": 100000,
    "लाख": 100000, "ಲಕ್ಷ": 100000, "ಲಕ್ಷದ": 100000, "ಲಕ್ಷಾ": 100000, "laksha": 100000,
    "lakha": 100000,
    "million": 1000000,
    "crore": 10000000, "crores": 10000000, "करोड़": 10000000, "ಕೋಟಿ": 10000000,
    "ಕೋಟಿಯ": 10000000, "karod": 10000000,
}
# Only directly after digits: "50k", "2.5l", "3cr"
_DIGIT_SUFFIX = {"k": 1000, "l": 100000, "cr": 10000000}
_MODIFIERS = {"साढ़े": 0.5, "सवा": 0.25, "पौने": -0.25, "sadhe": 0.5, "saade": 0.5, "sava": 0.25}
_CONNECTORS = {"and", "ಮತ್ತು", "और", "aur"}


def _norm_keys(d: dict) -> dict:
    return {normalize(k): v for k, v in d.items()}


_UNITS = _norm_keys(_UNITS)
_ROMAN_WEAK = _norm_keys(_ROMAN_WEAK)
_HUNDRED = {normalize(w) for w in _HUNDRED}
_BIG = _norm_keys(_BIG)
_MODIFIERS = _norm_keys(_MODIFIERS)
_CONNECTORS = {normalize(w) for w in _CONNECTORS}


# --- parser ------------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberSpan:
    value: int
    start: int  # token index
    end: int  # exclusive
    words: bool  # number words were involved (not digits only)
    scaled: bool  # a hundred / thousand / lakh / crore multiplier was involved


def _digit_value(tok: str) -> float | None:
    if not tok[0].isdigit():
        return None
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def _fits(current: float, value: float) -> bool:
    """Can `value` extend `current` additively (60 + 2, 200 + 50, 100 + 20)?"""
    if current == 0:
        return True
    if value < 10:
        return current % 10 == 0
    if value < 100:
        return current % 100 == 0
    return current % 1000 == 0


class _Span:
    def __init__(self, start: int):
        self.start = start
        self.total = 0.0
        self.current = 0.0
        self.words = False
        self.scaled = False
        self.strong = False  # has a non-weak number token
        self.number_tokens = 0


def find_numbers(tokens: list[str]) -> list[NumberSpan]:
    spans: list[NumberSpan] = []
    span: _Span | None = None
    modifier = 0.0
    prev_digit = False

    def close(end: int) -> None:
        nonlocal span
        if span is not None and (span.strong or span.scaled or span.number_tokens > 1):
            value = span.total + span.current
            spans.append(NumberSpan(int(round(value)), span.start, end, span.words, span.scaled))
        span = None

    def add_unit(i: int, value: float, *, word: bool, weak: bool = False) -> None:
        nonlocal span, modifier
        value += modifier
        modifier = 0.0
        if span is None or not _fits(span.current, value) or (not word and span.current):
            close(i)
            span = _Span(i)
        span.current += value
        span.words |= word
        span.strong |= not weak
        span.number_tokens += 1

    def multiply(i: int, factor: int) -> None:
        nonlocal span
        if modifier:  # "सवा लाख" = 1.25 lakh: the modifier applies to an implied one
            add_unit(i, 1, word=True)
        if span is None:
            span = _Span(i)
        span.words = True
        span.scaled = True
        span.number_tokens += 1
        if factor == 100:
            span.current = (span.current or 1) * 100
        else:
            span.total += (span.current or 1) * factor
            span.current = 0.0

    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        digit = _digit_value(tok)
        if digit is not None:
            add_unit(i, digit, word=False)
            span.strong = True
            prev_digit = True
            continue
        was_digit, prev_digit = prev_digit, False
        if was_digit and tok in _DIGIT_SUFFIX:
            multiply(i, _DIGIT_SUFFIX[tok])
        elif tok in _BIG:
            multiply(i, _BIG[tok])
            span.strong = True
        elif tok in _HUNDRED:
            multiply(i, 100)
            span.strong = True
        elif tok in _UNITS:
            add_unit(i, _UNITS[tok], word=True)
        elif tok in _ROMAN_WEAK:
            add_unit(i, _ROMAN_WEAK[tok], word=True, weak=True)
        elif tok == "a" and (nxt in _BIG or nxt in _HUNDRED):
            add_unit(i, 1, word=True, weak=True)
        elif tok in _MODIFIERS and _is_number_token(nxt):
            close(i)
            modifier = _MODIFIERS[tok]
        elif tok in _CONNECTORS and span is not None and _is_number_token(nxt):
            continue
        else:
            close(i)
    close(len(tokens))
    return spans


def _is_number_token(tok: str) -> bool:
    return bool(tok) and (
        _digit_value(tok) is not None or tok in _UNITS or tok in _ROMAN_WEAK
        or tok in _BIG or tok in _HUNDRED
    )


def parse_number(text: str) -> int | None:
    """The single number in `text`, or None if there is none or more than one."""
    spans = find_numbers(tokenize(text))
    return spans[0].value if len(spans) == 1 else None
