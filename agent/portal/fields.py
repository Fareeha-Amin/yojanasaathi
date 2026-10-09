"""The application form: which portal field the agent asks for, how an answer is read,
and how a value is spoken, shown and masked. Deterministic only: the LLM never reads a
date, a phone or account number, an IFSC code or a choice (design rule 1).

FIELD_KINDS is "our field map": every application field of the 4 mock-portal schemes
(rules/*.json, from the portal seed) has a kind. A field the portal starts asking for that
is not in this map is drift (agent/portal/drift.py): the agent safe-stops instead of guessing.

Sensitive values (SENSITIVE) never reach the graph as text: agent/portal/edge.py reads them
from the raw message, seals them (agent/sealed.py) and passes only the ciphertext on. They
are shown and logged masked; the spoken read-back of the digits is put into the /turn
reply at the very end (edge.expand), so the checkpoint never holds them.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from agent.facts import _find, _phrases, answer_yes_no, extract
from agent.numbers import find_numbers, normalize, parse_number, tokenize
from agent.replies import day, money, strings, t

# kind per portal field name (all 4 schemes). "mobile" is ours: the number registered on
# the portal, where its login OTP goes.
FIELD_KINDS: dict[str, str] = {
    "full_name": "name", "applicant_name": "name", "nominee_name": "name",
    "dob": "date", "annual_income": "income", "family_members_count": "count",
    "gender": "choice", "marital_status": "choice", "assistance_category": "choice",
    "ration_card_category": "choice", "coverage_preference": "choice", "disbursement_mode": "choice",
    "bank_account_number": "account", "bank_ifsc": "ifsc",
    "address": "text", "hospital_name": "text", "medical_condition": "text", "disability_details": "text",
    "declaration_consent": "declaration",
    "mobile": "mobile",
}
SENSITIVE = ("dob", "mobile", "bank_account_number", "bank_ifsc")
# Step 1 of the portal's form (applicant details) reuses these from our answers.
NAME_FIELDS = ("full_name", "applicant_name")
TEXT_MAX = 300

# --- reading digits -------------------------------------------------------------------

_DIGIT_WORDS = {normalize(w): d for d, words in enumerate([
    ["zero", "oh", "ಸೊನ್ನೆ", "शून्य", "sunna", "sonne", "shunya"],
    ["one", "ಒಂದು", "एक", "ek", "ondu"],
    ["two", "ಎರಡು", "दो", "do", "eradu"],
    ["three", "ಮೂರು", "तीन", "teen", "mooru", "muru"],
    ["four", "ನಾಲ್ಕು", "चार", "char", "chaar", "naalku", "nalku"],
    ["five", "ಐದು", "पांच", "पाँच", "paanch", "panch", "aidu"],
    ["six", "ಆರು", "छह", "छः", "छे", "chhe", "aaru"],
    ["seven", "ಏಳು", "सात", "saat", "elu", "yelu"],
    ["eight", "ಎಂಟು", "आठ", "aath", "entu", "yentu"],
    ["nine", "ಒಂಬತ್ತು", "ಒಂಭತ್ತು", "नौ", "nau", "ombattu"],
]) for w in words}
_REPEAT = {normalize(w): n for w, n in [("double", 2), ("triple", 3), ("ಡಬಲ್", 2), ("डबल", 2),
                                         ("ಟ್ರಿಪಲ್", 3), ("ट्रिपल", 3)]}


def _digits_of(tok: str) -> str | None:
    """ASCII / Kannada / Devanagari digits of a numeric token ("1,20,000" -> "120000")."""
    if not any(c.isdigit() for c in tok) or not all(c.isdigit() or c in ",." for c in tok):
        return None
    return "".join(str(unicodedata.digit(c)) for c in tok if c.isdigit())


def digit_string(text: str) -> str:
    """Every digit in the message, in order: digit groups and single digit words ("nine
    eight seven", "ಒಂಬತ್ತು ಎಂಟು", "double five"). Other words are skipped."""
    out: list[str] = []
    repeat = 1
    for tok in tokenize(text):
        d = _digits_of(tok)
        if d is None and tok in _DIGIT_WORDS:
            d = str(_DIGIT_WORDS[tok])
        if d is not None:
            out.append(d[0] * repeat + d[1:] if repeat > 1 else d)
            repeat = 1
        elif tok in _REPEAT:
            repeat = _REPEAT[tok]
    return "".join(out)


def parse_mobile(text: str) -> str | None:
    d = digit_string(text)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if re.fullmatch(r"[6-9]\d{9}", d) else None


def parse_account(text: str) -> str | None:
    d = digit_string(text)
    return d if 9 <= len(d) <= 18 else None


def parse_otp(text: str) -> str | None:
    d = digit_string(text)
    return d if len(d) == 6 else None


# --- IFSC: 4 letters, a zero, 6 letters or digits (SBIN0001234) -----------------------

_LETTER_NAMES = {
    # Kannada and Hindi names of the Latin letters, as STT may write them
    **{normalize(w): c for c, w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [
        "ಎ", "ಬಿ", "ಸಿ", "ಡಿ", "ಇ", "ಎಫ್", "ಜಿ", "ಎಚ್", "ಐ", "ಜೆ", "ಕೆ", "ಎಲ್", "ಎಂ", "ಎನ್", "ಒ",
        "ಪಿ", "ಕ್ಯೂ", "ಆರ್", "ಎಸ್", "ಟಿ", "ಯು", "ವಿ", "ಡಬ್ಲ್ಯು", "ಎಕ್ಸ್", "ವೈ", "ಜೆಡ್"])},
    **{normalize(w): c for c, w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [
        "ए", "बी", "सी", "डी", "ई", "एफ", "जी", "एच", "आई", "जे", "के", "एल", "एम", "एन", "ओ",
        "पी", "क्यू", "आर", "एस", "टी", "यू", "वी", "डब्ल्यू", "एक्स", "वाई", "जेड"])},
}
IFSC_RE = re.compile(r"[A-Z]{4}0[A-Z0-9]{6}")


def parse_ifsc(text: str) -> str | None:
    """Letters (also their Kannada / Hindi names), digits and digit words, joined over
    consecutive words into exactly 11 characters ("no, the IFSC is S B I N 0 0 0 1 2 3 4",
    "sbin 000 1234"). The letter O said in the fifth place is the zero."""
    chars: list[str] = []
    repeat = 1
    for tok in tokenize(text):
        if tok in _REPEAT:
            repeat = _REPEAT[tok]
            continue
        d = _digits_of(tok)
        if d is not None:
            piece = d
        elif tok in _DIGIT_WORDS and tok != "oh":
            piece = str(_DIGIT_WORDS[tok])
        elif tok in _LETTER_NAMES:
            piece = _LETTER_NAMES[tok]
        elif re.fullmatch(r"[a-z0-9]+", tok):
            piece = tok.upper()
        else:
            continue
        chars.append(piece[0] * repeat + piece[1:])
        repeat = 1
    for i in range(len(chars)):
        code = ""
        for piece in chars[i:]:
            code += piece
            if len(code) >= 11:
                break
        if len(code) == 11:
            code = code[:4] + ("0" if code[4] == "O" else code[4]) + code[5:]
            if IFSC_RE.fullmatch(code):
                return code
    return None


# --- dates of birth ------------------------------------------------------------------

_EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]
_MONTH_VARIANTS = {  # spellings STT also produces
    "ಫೆಬ್ರುವರಿ": 2, "ಸೆಪ್ಟಂಬರ್": 9, "ಅಕ್ಟೊಬರ್": 10, "फरवरी": 2, "सितम्बर": 9, "अक्तूबर": 10,
    "नवम्बर": 11, "दिसम्बर": 12, "sept": 9,
}
_ORDINALS = {w: i for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split())}
_ORDINALS["thirtieth"] = 30
_TENS = {"twenty": 20, "thirty": 30}


def _months() -> dict[str, int]:
    out = {m: i + 1 for i, m in enumerate(_EN_MONTHS)}
    out.update({m[:3]: i + 1 for i, m in enumerate(_EN_MONTHS)})
    for lang in ("kn", "hi"):
        out.update({normalize(strings(lang)[f"month_{i}"]): i for i in range(1, 13)})
    out.update({normalize(k): v for k, v in _MONTH_VARIANTS.items()})
    return out


_MONTHS = _months()


def _month_of(tok: str) -> int | None:
    if tok in _MONTHS:
        return _MONTHS[tok]
    if any(0x0900 <= ord(c) < 0x0D00 for c in tok):  # ಮಾರ್ಚ್‌ನಲ್ಲಿ: suffixed Kannada / Hindi
        for name, m in _MONTHS.items():
            if len(name) >= 3 and tok.startswith(name):
                return m
    return None


def _age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def parse_date(text: str, today: date | None = None) -> date | None:
    """A date of birth: "5 March 1962", "March 5th 1962", "05/03/1962" (day first, as in
    India), "1962-03-05", "ಮಾರ್ಚ್ 5 1962", "5 मार्च 1962", or number words ("five March
    nineteen sixty two", "ಐದು ಮಾರ್ಚ್ ಸಾವಿರದ ಒಂಬೈನೂರ ಅರವತ್ತೆರಡು"). None if unsure."""
    today = today or date.today()
    raw = tokenize(text)
    toks: list[str] = []
    month = None
    for tok in raw:
        m = _month_of(tok)
        if m and month is None:
            month = m
            toks.append("|")  # keeps the day and the year apart
        elif tok in _ORDINALS:
            if toks and toks[-1] in _TENS:
                toks[-1] = str(_TENS[toks[-1]] + _ORDINALS[tok])
            else:
                toks.append(str(_ORDINALS[tok]))
        else:
            toks.append(tok)
    nums = [s.value for s in find_numbers(toks)]
    # "nineteen sixty two" / "उन्नीस बासठ" -> 19, 62
    merged: list[int] = []
    for n in nums:
        if merged and 18 <= merged[-1] <= 20 and 0 <= n <= 99 and \
                (month is not None or len(merged) >= 3):
            merged[-1] = merged[-1] * 100 + n
        else:
            merged.append(n)
    nums = merged
    if month is not None:
        years = [n for n in nums if 1900 <= n <= today.year]
        days = [n for n in nums if 1 <= n <= 31]
        if not years:  # "ಐದು ಸಾವಿರದ ಒಂಬೈನೂರ..." reads as 5962: day 5, year 1962
            for n in nums:
                if 2000 <= n <= 31999 and n % 1000 >= 900:
                    days, years = [n // 1000], [1000 + n % 1000]
                    break
        if len(years) != 1:
            return None
        days = [d for d in days if d != years[0]]
        if len(days) != 1:
            return None
        y, d = years[0], days[0]
    else:
        if len(nums) != 3:
            return None
        a, b, c = nums
        if a >= 1900:  # 1962-03-05
            y, month, d = a, b, c
        else:  # 05/03/1962, day first
            d, month, y = a, b, c
            if y < 100:  # 05/03/62: 1962, unless that would be over 120 years ago
                y += 1900 if today.year - (1900 + y) <= 120 else 2000
    try:
        born = date(y, month, d)
    except ValueError:
        return None
    if born > today or _age_on(born, today) > 120:
        return None
    return born


def age_from(iso: str, today: date | None = None) -> int:
    return _age_on(date.fromisoformat(iso), today or date.today())


# --- choices: the portal's exact English option -----------------------------------------

# Words people say for an option, besides the option text itself (en + the portal's own
# kn / hi option labels from rules/*.json "option_labels").
_SYNONYMS: dict[str, list[str]] = {
    "Male": ["man", "male", "gents", "ಗಂಡು", "ಗಂಡಸು", "पुरुष", "आदमी", "mard"],
    "Female": ["woman", "female", "lady", "ಹೆಣ್ಣು", "ಹೆಂಗಸು", "ಮಹಿಳೆ", "महिला", "औरत", "स्त्री"],
    "Other": ["other", "ಇತರ", "अन्य"],
    "Married": ["married", "ಮದುವೆ", "ಮದುವೆಯಾಗಿದೆ", "ವಿವಾಹಿತ", "शादीशुदा", "विवाहित"],
    "Widowed": ["widow", "widowed", "widower", "ವಿಧವೆ", "ವಿಧುರ", "विधवा", "विधुर"],
    "Single / Unmarried": ["single", "unmarried", "ಅವಿವಾಹಿತ", "ಮದುವೆಯಾಗಿಲ್ಲ", "अविवाहित", "कुंवारा"],
    "Divorced": ["divorced", "divorce", "ವಿಚ್ಛೇದಿತ", "ವಿಚ್ಛೇದನ", "तलाकशुदा", "तलाक"],
    "Direct Bank Transfer (DBT)": ["bank", "dbt", "direct", "ಬ್ಯಾಂಕ್", "बैंक"],
    "Post Office Savings Bank (POSB)": ["post", "posb", "ಅಂಚೆ", "ಪೋಸ್ಟ್", "डाकघर", "डाक", "पोस्ट"],
    "Old Age Assistance": ["old", "age", "ವೃದ್ಧಾಪ್ಯ", "ವಯಸ್ಸಾದ", "वृद्धावस्था", "बुढ़ापा"],
    "Disability Welfare Assistance": ["disability", "disabled", "handicapped", "ಅಂಗವಿಕಲ", "दिव्यांग", "विकलांग"],
    "Destitute / Widow Assistance": ["destitute", "widow", "ನಿರಾಶ್ರಿತ", "ವಿಧವಾ", "ವಿಧವೆ", "निराश्रित", "विधवा"],
    "Special Care Support": ["special", "care", "ವಿಶೇಷ", "विशेष"],
    "BPL (Below Poverty Line)": ["bpl", "below", "ಬಿಪಿಎಲ್", "ಬಡತನ", "बीपीएल", "गरीबी"],
    "AAY (Antyodaya Anna)": ["aay", "antyodaya", "ಅಂತ್ಯೋದಯ", "अंत्योदय"],
    "APL (State Ration Card)": ["apl", "ಎಪಿಎಲ್", "एपीएल"],
    "In-Patient Hospitalization": ["inpatient", "in-patient", "admission", "admitted", "hospitalization",
                                   "ಒಳರೋಗಿ", "ದಾಖಲಾತಿ", "भर्ती"],
    "Outpatient Chronic Disease Care": ["outpatient", "opd", "chronic", "ಹೊರರೋಗಿ", "ओपीडी", "पुरानी"],
    "Diagnostic Support": ["diagnostic", "diagnosis", "test", "tests", "scan", "ರೋಗನಿರ್ಣಯ", "ಪರೀಕ್ಷೆ", "नैदानिक", "जांच"],
}
_ORDINAL_PHRASES = [
    _phrases("first", "1st", "ಮೊದಲ", "ಮೊದಲನೆ", "ಮೊದಲನೇ", "पहला", "पहली", "pehla"),
    _phrases("second", "2nd", "ಎರಡನೇ", "ಎರಡನೆ", "दूसरा", "दूसरी", "doosra"),
    _phrases("third", "3rd", "ಮೂರನೇ", "ಮೂರನೆ", "तीसरा", "तीसरी"),
    _phrases("fourth", "4th", "ನಾಲ್ಕನೇ", "ನಾಲ್ಕನೆ", "चौथा", "चौथी"),
]


def _option_phrases(option: str, local: list[str]) -> list[tuple[str, ...]]:
    words = [option, *local, *_SYNONYMS.get(option, [])]
    return _phrases(*words)


def parse_choice(text: str, options: list[str], labels: dict[str, list[str]] | None = None) -> str | None:
    """The portal option the citizen named (exact English option), by its words in kn / hi /
    en, or its position ("the second one", "2"). None when unclear or ambiguous."""
    toks = tokenize(text)
    labels = labels or {}
    hits = []
    for i, opt in enumerate(options):
        local = [ls[i] for ls in labels.values() if i < len(ls)]
        if _find(toks, _option_phrases(opt, local)) is not None:
            hits.append(opt)
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    pos = [i for i, ph in enumerate(_ORDINAL_PHRASES[:len(options)]) if _find(toks, ph) is not None]
    if len(pos) == 1:
        return options[pos[0]]
    n = parse_number(text)
    if n is not None and 1 <= n <= len(options) and len(toks) <= 3:
        return options[n - 1]
    return None


# --- names and free text -----------------------------------------------------------

_NAME_PREFIX = re.compile(
    r"^(?:(?:my|his|her|the|nominee'?s?|applicant'?s?)\s+)?(?:full\s+)?(?:name\s+is|name's|is|i\s+am|i'm|this\s+is|it\s+is|it's)\s+",
    re.I)
_LOCAL_PREFIX = ("ನನ್ನ ಹೆಸರು", "ಅವರ ಹೆಸರು", "ಹೆಸರು", "मेरा नाम", "उनका नाम", "नाम")
_LOCAL_SUFFIX = ("ಆಗಿದೆ", "ಅಂತ", "ಎಂದು", "है", "हैं", "हूं", "हूँ")


def parse_name(text: str) -> str | None:
    s = unicodedata.normalize("NFC", text).strip().strip(".,!?।\"' ")
    s = _NAME_PREFIX.sub("", s)
    for p in _LOCAL_PREFIX:
        if s.startswith(p):
            s = s[len(p):].strip()
            break
    for p in _LOCAL_SUFFIX:
        if s.endswith(p):
            s = s[: -len(p)].strip()
    s = re.sub(r"\s+", " ", s).strip(".,!?।\"' ")
    letters = sum(c.isalpha() or unicodedata.category(c).startswith("M") for c in s)
    if letters < 2 or any(c.isdigit() for c in s) or len(s) > 80 or len(s.split()) > 6:
        return None
    if s.isascii():
        s = " ".join(w[:1].upper() + w[1:] for w in s.split())
    return s


def parse_text(text: str, min_len: int = 3) -> str | None:
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    return s if min_len <= len(s) <= TEXT_MAX else None


# --- one entry point --------------------------------------------------------------

@dataclass
class Answer:
    value: Any  # str for the form (the portal takes text); bool for the declaration


def parse(field: str, text: str, scheme: dict[str, Any] | None = None) -> Answer | None:
    """The answer to the question about `field`, or None if it can't be read for sure."""
    kind = FIELD_KINDS.get(field)
    if kind == "name":
        v = parse_name(text)
    elif kind == "date":
        d = parse_date(text)
        v = d.isoformat() if d else None
    elif kind == "mobile":
        v = parse_mobile(text)
    elif kind == "account":
        v = parse_account(text)
    elif kind == "ifsc":
        v = parse_ifsc(text)
    elif kind == "count":
        n = parse_number(text)
        v = str(n) if n is not None and 1 <= n <= 30 else None
    elif kind == "income":
        facts = extract(text, "annual_income")
        n = facts.values.get("annual_income")
        v = str(n) if n is not None and 0 <= n <= 10**9 else None
    elif kind == "choice":
        f = spec(scheme, field) if scheme else None
        v = parse_choice(text, f.get("options") or [], option_labels(scheme, field)) if f else None
    elif kind == "declaration":
        yn = answer_yes_no(text)
        return None if yn is None else Answer(yn)
    elif kind == "text":
        v = parse_text(text, 8 if field == "address" else 3)
    else:
        v = None
    return None if v is None else Answer(v)


# --- the scheme's form (rules/*.json mirrors the portal seed) ---------------------------

def spec(scheme: dict[str, Any], field: str) -> dict[str, Any] | None:
    return next((f for f in scheme.get("application_fields", []) if f["name"] == field), None)


def option_labels(scheme: dict[str, Any] | None, field: str) -> dict[str, list[str]]:
    return ((scheme or {}).get("option_labels") or {}).get(field) or {}


def label(scheme: dict[str, Any] | None, field: str, lang: str) -> str:
    if field == "mobile":
        return t("field_mobile", lang)
    f = spec(scheme or {}, field)
    if f is None:
        return field
    return f["label"].get(lang) or f["label"]["en"]


def choice_text(scheme: dict[str, Any] | None, field: str, value: str, lang: str) -> str:
    """A portal option in the citizen's language (the portal's own translation)."""
    f = spec(scheme or {}, field) or {}
    opts = f.get("options") or []
    local = option_labels(scheme, field).get(lang)
    if lang != "en" and local and value in opts and opts.index(value) < len(local):
        return local[opts.index(value)]
    return value


def options_text(scheme: dict[str, Any], field: str, lang: str) -> str:
    f = spec(scheme, field) or {}
    names = [choice_text(scheme, field, o, lang) for o in f.get("options") or []]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + t("join_or", lang) + names[-1]


def form_fields(scheme: dict[str, Any]) -> list[str]:
    """Fields the agent fills, in the portal's order: required ones, plus the declaration."""
    return [f["name"] for f in scheme.get("application_fields", []) if f.get("required")]


# --- spoken, shown, masked --------------------------------------------------------

def _spaced(digits: str, group: int = 4) -> str:
    groups = [digits[i:i + group] for i in range(0, len(digits), group)]
    return ", ".join(" ".join(g) for g in groups)


def spoken(field: str, value: str, lang: str) -> str:
    """How the agent reads a value back (digits one by one, dates in words)."""
    kind = FIELD_KINDS.get(field)
    if kind == "date":
        return day(value, lang)
    if kind in ("account", "mobile"):
        return _spaced(value)
    if kind == "ifsc":
        return " ".join(value)
    return value


def masked(field: str, value: str) -> str:
    """How a sensitive value is shown and logged: never in full."""
    kind = FIELD_KINDS.get(field)
    if kind == "date":
        return f"**/**/{value[:4]}"
    if kind in ("account", "mobile"):
        return "X" * 6 + value[-4:]
    if kind == "ifsc":  # "*", not "X": SBIN0XXXXXX would itself look like an IFSC code
        return value[:4] + "0" + "*" * 6
    return value


def last4(value: str) -> str:
    return value[-4:]


def shown(scheme: dict[str, Any] | None, field: str, value: Any, lang: str) -> str:
    """Display text for the screens: masked when sensitive, the citizen's language for a
    choice, ₹ grouping for income."""
    if field in SENSITIVE:
        return masked(field, str(value))
    kind = FIELD_KINDS.get(field)
    if kind == "choice":
        return choice_text(scheme, field, str(value), lang)
    if kind == "income":
        return money(int(value))
    if kind == "declaration":
        return t("value_yes" if value else "value_no", lang)
    return str(value)


# --- which field a correction is about ("no, the account number is wrong") -------------

_MENTIONS: list[tuple[str, list[tuple[str, ...]]]] = [
    ("nominee_name", _phrases("nominee", "guardian", "ನಾಮಿನಿ", "ನಾಮನಿರ್ದೇಶಿತ", "ಪೋಷಕ", "नॉमिनी", "नामांकित")),
    ("bank_ifsc", _phrases("ifsc", "ಐಎಫ್ಎಸ್ಸಿ", "आईएफएससी", "branch code")),
    ("bank_account_number", _phrases("account", "ಖಾತೆ", "खाता", "khata")),
    ("mobile", _phrases("mobile", "phone", "ಮೊಬೈಲ್", "ಫೋನ್", "मोबाइल", "फोन")),
    ("dob", _phrases("birth", "dob", "born", "ಹುಟ್ಟಿದ", "ಜನ್ಮ", "जन्म")),
    ("hospital_name", _phrases("hospital", "ಆಸ್ಪತ್ರೆ", "अस्पताल")),
    ("medical_condition", _phrases("condition", "diagnosis", "illness", "disease", "ಕಾಯಿಲೆ", "ರೋಗ", "बीमारी")),
    ("address", _phrases("address", "ವಿಳಾಸ", "पता")),
    ("marital_status", _phrases("marital", "married", "ವೈವಾಹಿಕ", "वैवाहिक")),
    ("disbursement_mode", _phrases("disbursement", "post office", "payment mode", "ವಿತರಣ", "भुगतान")),
    ("assistance_category", _phrases("assistance", "category", "ನೆರವು", "ವರ್ಗ", "सहायता", "श्रेणी")),
    ("ration_card_category", _phrases("ration", "ರೇಷನ್", "ಪಡಿತರ", "राशन")),
    ("coverage_preference", _phrases("coverage", "ವ್ಯಾಪ್ತಿ", "कवरेज")),
    ("family_members_count", _phrases("members", "family size", "ಸದಸ್ಯ", "सदस्य")),
    ("gender", _phrases("gender", "ಲಿಂಗ", "लिंग")),
    ("full_name", _phrases("name", "ಹೆಸರು", "नाम")),
]


def mentioned(text: str, fields: list[str]) -> str | None:
    """The form field a message is about, among `fields` (most specific first). A plain
    "name" means the applicant's name field of this scheme."""
    toks = tokenize(text)
    for field, phrases in _MENTIONS:
        if _find(toks, phrases) is None:
            continue
        if field == "full_name":
            return next((f for f in NAME_FIELDS if f in fields), None)
        if field in fields:
            return field
    return None
