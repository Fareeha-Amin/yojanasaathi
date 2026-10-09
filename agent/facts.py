"""Deterministic fact extraction from one citizen message (kn / hi / en). Runs before the
LLM; what it finds wins over the LLM.

- Numbers come from agent.numbers (digits or number words) and are attributed to a field
  by cue words around them (ವರ್ಷ / साल / years -> age; ಆದಾಯ / आय / income / ₹ / lakh ->
  annual_income; per month -> x12, flagged for read-back) or, for a short reply, by the
  question we just asked (`asking`).
- District: lookup table (agent.districts), canonical English name.
- Gender: only "I am a woman"-style statements, flagged (free text otherwise goes to the LLM).
- Topic (pension / health), scheme choice (by name, "first" / "second", "next"),
  "proceed" / "status" requests, documents the citizen says they have or don't have.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from agent.districts import find_district
from agent.gate import parse_decision
from agent.numbers import NumberSpan, find_numbers, normalize, tokenize

NUMERIC_FIELDS = ("age", "annual_income")
FIELDS = (*NUMERIC_FIELDS, "gender", "district")

AGE_MIN, AGE_MAX = 10, 120

# Questions that a plain yes / no answers (`asking` values that are not profile fields).
YES_NO_QUESTIONS = ("proceed", "others", "choose")


def _phrases(*items: str) -> list[tuple[str, ...]]:
    return [tuple(tokenize(p)) for p in items]


def _is_indic(tok: str) -> bool:
    return any(0x0900 <= ord(c) < 0x0D00 for c in tok)


def _tok_eq(want: str, tok: str) -> bool:
    # Kannada/Hindi words take suffixes (ಪಿಂಚಣಿಗೆ, ಆದಾಯವು): prefix match for longer words.
    if want == tok:
        return True
    return _is_indic(want) and len(want) >= 3 and tok.startswith(want)


def _find(tokens: list[str], phrases: list[tuple[str, ...]]) -> int | None:
    """Index of the first token where any phrase starts, else None."""
    for i in range(len(tokens)):
        for p in phrases:
            if p and len(p) <= len(tokens) - i and all(_tok_eq(w, t) for w, t in zip(p, tokens[i:])):
                return i
    return None


def _has(tokens: list[str], phrases: list[tuple[str, ...]]) -> bool:
    return _find(tokens, phrases) is not None


AGE_CUES = _phrases(
    "age", "aged", "old", "year", "years", "yrs", "yr",
    "ವರ್ಷ", "ವಯಸ್ಸು", "ವಯಸ್ಸಿನ", "ವಯಸ್ಸಾಗಿದೆ",
    "साल", "वर्ष", "उम्र", "आयु", "बरस",
    "saal", "sal", "umar", "umr", "varsha", "vayassu",
)
MONEY_CUES = _phrases(
    "income", "earn", "earns", "earning", "earnings", "salary", "rupees", "rupee", "rs", "inr",
    "₹", "annual", "yearly", "per annum",
    "ಆದಾಯ", "ರೂಪಾಯಿ", "ರೂ", "ಸಂಬಳ", "ಗಳಿಕೆ", "ಗಳಿಸ",
    "आय", "आमदनी", "कमाई", "कमात", "रुपये", "रुपए", "रुपया", "रूपये", "वेतन", "तनख्वाह", "सालाना",
    "aay", "aamdani", "kamai", "rupaye", "rupay", "aadaya", "sambala",
)
MONTH_CUES = _phrases(
    "month", "monthly", "per month", "a month", "ತಿಂಗಳ", "ತಿಂಗಳಿಗೆ", "ತಿಂಗಳು",
    "महीना", "महीने", "मासिक", "mahina", "mahine",
)
CURRENCY_AFTER = _phrases("rupees", "rupee", "rs", "ರೂಪಾಯಿ", "ರೂ", "रुपये", "रुपए", "रुपया", "rupaye")

TOPICS: dict[str, list[tuple[str, ...]]] = {
    "pension": _phrases("pension", "old age", "ಪಿಂಚಣಿ", "ವೃದ್ಧಾಪ್ಯ", "पेंशन", "पेन्शन",
                        "वृद्धावस्था", "pinchani"),
    "health": _phrases("health", "healthcare", "hospital", "medical", "treatment", "ಆರೋಗ್ಯ",
                       "ಆಸ್ಪತ್ರೆ", "ಚಿಕಿತ್ಸೆ", "ವೈದ್ಯಕೀಯ", "स्वास्थ्य", "अस्पताल", "इलाज",
                       "चिकित्सा"),
}

PROCEED = _phrases(
    "proceed", "apply", "continue", "go ahead", "fill the form", "fill form",
    "ಮುಂದುವರಿಸಿ", "ಮುಂದುವರೆಸಿ", "ಅರ್ಜಿ ಹಾಕಿ", "ಅರ್ಜಿ ತುಂಬಿ", "ಶುರು ಮಾಡಿ",
    "आगे बढ़ें", "आगे बढ़ो", "आवेदन करें", "आवेदन करो", "फॉर्म भरो", "फॉर्म भरें", "शुरू करो",
    "apply karo", "aage badho",
)
STATUS = _phrases(
    "status", "application status", "my application", "ಸ್ಥಿತಿ", "ಅರ್ಜಿಯ ಸ್ಥಿತಿ",
    "स्थिति", "स्टेटस", "आवेदन की स्थिति",
)

# Choosing among the offered schemes: position in the list, or "next".
ORDINALS: list[list[tuple[str, ...]]] = [
    _phrases("first", "1st", "ಮೊದಲ", "ಮೊದಲನೆ", "ಮೊದಲನೇ", "ಒಂದನೇ", "पहला", "पहली", "pehla", "pehli"),
    _phrases("second", "2nd", "ಎರಡನೇ", "ಎರಡನೆ", "दूसरा", "दूसरी", "doosra", "doosri"),
    _phrases("third", "3rd", "ಮೂರನೇ", "ಮೂರನೆ", "तीसरा", "तीसरी"),
    _phrases("fourth", "4th", "ನಾಲ್ಕನೇ", "ನಾಲ್ಕನೆ", "चौथा", "चौथी"),
]
NEXT = _phrases("next", "next one", "another", "other one", "ಮುಂದಿನ", "ಇನ್ನೊಂದು", "ಬೇರೆ ಯೋಜನೆ",
                "अगला", "अगली", "दूसरी योजना")

# Free text: only "I am a woman"-style statements (first person + a singular word), and
# flagged for read-back because "मैं महिला योजना के बारे में..." would also match.
FIRST_PERSON = _phrases("i", "ನಾನು", "मैं", "main")
GENDER_SAID: dict[str, set[str]] = {
    g: {normalize(w) for w in words} for g, words in {
        "female": {"woman", "lady", "ಮಹಿಳೆ", "ಹೆಂಗಸು", "महिला", "औरत"},
        "male": {"man", "ಪುರುಷ", "ಗಂಡಸು", "पुरुष", "आदमी"},
    }.items()
}

# Document ids as in rules/*.json (the portal's document list).
DOCS: dict[str, list[tuple[str, ...]]] = {
    "identity_proof": _phrases("aadhaar", "aadhar", "adhar", "voter id", "identity", "id proof",
                               "ಆಧಾರ್", "ಆಧಾರ", "ಗುರುತಿನ", "आधार", "पहचान"),
    "age_proof": _phrases("age proof", "birth certificate", "ವಯಸ್ಸಿನ ಪುರಾವೆ", "ಜನನ ಪ್ರಮಾಣ",
                          "आयु प्रमाण", "जन्म प्रमाण"),
    "residence_proof": _phrases("residence", "address proof", "domicile", "ವಾಸಸ್ಥಳ", "ನಿವಾಸ",
                                "निवास"),
    "income_certificate": _phrases("income certificate", "ಆದಾಯ ಪ್ರಮಾಣ", "आय प्रमाण"),
    "bank_account_details": _phrases("passbook", "bank account", "bank details", "bank book",
                                     "ಪಾಸ್ ಬುಕ್", "ಪಾಸ್‌ಬುಕ್", "ಬ್ಯಾಂಕ್ ಖಾತೆ", "पासबुक",
                                     "बैंक खाता"),
    "medical_documents": _phrases("medical documents", "medical records", "prescription",
                                  "ವೈದ್ಯಕೀಯ ದಾಖಲೆ", "चिकित्सा दस्तावेज"),
    "family_details": _phrases("family details", "ration card", "ಕುಟುಂಬದ ವಿವರ", "ರೇಷನ್",
                               "ಪಡಿತರ", "पारिवारिक विवरण", "राशन"),
}
HAVE_CUES = _phrases("have", "got", "ve", "ಇದೆ", "ಇವೆ", "ಇದ್ದಾವೆ", "ಹತ್ತಿರ", "है", "हैं", "पास",
                     "hai", "paas")
NEGATION = _phrases("no", "not", "don", "dont", "without", "missing", "lost", "haven",
                    "ಇಲ್ಲ", "ಇಲ್ಲಾ", "ಇಲ್ಲದ", "नहीं", "नही", "nahi", "nahin", "illa")
_CLAUSE_SPLIT = re.compile(r"[,.;:!?।]|\b(?:but|and|also)\b|ಆದರೆ|ಮತ್ತು|लेकिन|और|lekin|aur")

# Extra yes/no words for answers to a question ("ಇದೆ" = there is). The submit gate does
# not use these; it keeps its own stricter list.
ANSWER_YES = _phrases("ಇದೆ", "ಇದ್ದೇನೆ", "ಹೌದು", "है", "हूं", "हूँ", "i am", "i do", "yes i")
ANSWER_NO = _phrases("ಇಲ್ಲ", "ಇಲ್ಲಾ", "नहीं", "नही", "no", "not", "never")


@lru_cache(maxsize=1)
def _scheme_aliases() -> dict[str, list[tuple[str, ...]]]:
    """Phrases that name each scheme: its `aliases` plus its title in kn / hi / en."""
    from agent.rules import load_schemes

    return {sid: _phrases(*s.get("aliases", []), *s["titles"].values())
            for sid, s in load_schemes().items()}


@dataclass
class Facts:
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # field -> how we know it
    readback: set[str] = field(default_factory=set)  # fields to flag at review
    numbers: list[NumberSpan] = field(default_factory=list)
    unattributed: list[NumberSpan] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    chosen: str | None = None  # scheme named in the message
    ordinal: int | None = None  # "the second one" -> 1
    next_one: bool = False  # "the next one", "another scheme"
    proceed: bool | None = None  # True: wants to apply / yes to our yes-no question; False: no
    status: bool = False
    docs_have: list[str] = field(default_factory=list)
    docs_missing: list[str] = field(default_factory=list)
    answered: bool = False  # the pending question was answered deterministically

    def set(self, name: str, value: Any, source: str, flag: bool = False) -> None:
        self.values[name] = value
        self.sources[name] = source
        if flag:
            self.readback.add(name)

    @property
    def picks_scheme(self) -> bool:
        return self.chosen is not None or self.ordinal is not None or self.next_one


def _window(tokens: list[str], span: NumberSpan, before: int = 3, after: int = 3) -> list[str]:
    return tokens[max(0, span.start - before) : span.end + after]


def _age_prefix(tokens: list[str], span: NumberSpan) -> bool:
    """"I'm 62", "I am 62", "aged 62": an age even without a "years" cue."""
    before = tokens[max(0, span.start - 2) : span.start]
    return before[-1:] in (["im"], ["aged"]) or before == ["i", "m"] or before == ["i", "am"]


def _attribute_numbers(tokens: list[str], facts: Facts) -> None:
    """Strong money signals (a multiplier, >= 1000, ₹ / rupees right next to it) beat an
    age cue; an age cue beats a money word somewhere nearby ("I'm 55 with income 1 lakh":
    55 is the age, 1 lakh the income). A money word alone never makes a number below 100
    an income ("income is one-twenty" -> left to the LLM, flagged)."""
    for span in facts.numbers:
        win = _window(tokens, span)
        strong_money = span.scaled or span.value >= 1000 or (
            span.start > 0 and tokens[span.start - 1] == "₹") or _find(
            tokens[span.end : span.end + 1], CURRENCY_AFTER) == 0
        age_cue = (_has(win, AGE_CUES) or _age_prefix(tokens, span)) and \
            AGE_MIN <= span.value <= AGE_MAX
        source = "number_words" if span.words else "digits"
        weak_money = not age_cue and span.value >= 100 and _has(win, MONEY_CUES)
        if strong_money or weak_money:
            if "annual_income" in facts.values:
                facts.unattributed.append(span)
            elif _has(win, MONTH_CUES):
                facts.set("annual_income", span.value * 12, source + "_monthly_x12", flag=True)
            else:
                facts.set("annual_income", span.value, source)
        elif age_cue and "age" not in facts.values:
            facts.set("age", span.value, source)
        else:
            facts.unattributed.append(span)


def answer_yes_no(text: str) -> bool | None:
    d = parse_decision(text)
    if d != "unclear":
        return d == "yes"
    toks = tokenize(text)
    yes, no = _has(toks, ANSWER_YES), _has(toks, ANSWER_NO)
    if yes != no:
        return yes
    return None


def _documents(text: str, facts: Facts) -> None:
    for clause in _CLAUSE_SPLIT.split(normalize(text)):
        toks = tokenize(clause or "")
        docs = [d for d, phrases in DOCS.items() if _has(toks, phrases)]
        if not docs:
            continue
        if _has(toks, NEGATION):
            facts.docs_missing += [d for d in docs if d not in facts.docs_missing]
        elif _has(toks, HAVE_CUES) or _has(tokenize(text), HAVE_CUES):
            facts.docs_have += [d for d in docs if d not in facts.docs_have]


def _choice(tokens: list[str], facts: Facts) -> None:
    named = [sid for sid, phrases in _scheme_aliases().items() if _has(tokens, phrases)]
    if len(named) == 1:  # "pension" alone names two schemes: not a choice
        facts.chosen = named[0]
    hits = [i for i, phrases in enumerate(ORDINALS) if _has(tokens, phrases)]
    if len(hits) == 1:
        facts.ordinal = hits[0]
    facts.next_one = _has(tokens, NEXT)


def _answer(text: str, asking: str, facts: Facts) -> None:
    """The message as a reply to the question we asked (`asking` = a field, or a yes/no
    question: "proceed" = start this application?, "others" = check other schemes?,
    "choose" = which scheme?)."""
    if asking in YES_NO_QUESTIONS:
        if facts.values or facts.docs_have or facts.docs_missing or facts.picks_scheme:
            return  # "I have Aadhaar but no income certificate" is not a "no" to applying
        yn = answer_yes_no(text)
        if yn is not None:
            facts.proceed = yn
            facts.answered = True
        return
    if asking in facts.values:
        facts.answered = True
        return
    if asking in NUMERIC_FIELDS and len(facts.unattributed) == 1:
        span = facts.unattributed.pop()
        if asking != "age" or AGE_MIN <= span.value <= AGE_MAX:
            facts.set(asking, span.value, "answer_" + ("number_words" if span.words else "digits"))
            facts.answered = True
        else:
            facts.unattributed.append(span)


def extract(text: str, asking: str | None = None) -> Facts:
    tokens = tokenize(text)
    facts = Facts(numbers=find_numbers(tokens))
    _attribute_numbers(tokens, facts)

    district = find_district(text)
    if district:
        facts.set("district", district, "lookup")
    if _has(tokens, FIRST_PERSON):
        said = [g for g, words in GENDER_SAID.items() if any(t in words for t in tokens)]
        if len(said) == 1:
            facts.set("gender", said[0], "said", flag=True)

    facts.topics = [t for t, phrases in TOPICS.items() if _has(tokens, phrases)]
    facts.status = _has(tokens, STATUS)
    if _has(tokens, PROCEED):
        facts.proceed = True
    _choice(tokens, facts)
    _documents(text, facts)

    if asking:
        _answer(text, asking, facts)
    return facts
