"""Deterministic fact extraction from one citizen message (kn / hi / en). Runs before the
LLM; what it finds wins over the LLM.

- Numbers come from agent.numbers (digits or number words) and are attributed to a field
  by cue words around them (ವರ್ಷ / साल / years -> age; ಆದಾಯ / आय / income / ₹ / lakh ->
  annual_income; per month -> x12, flagged for read-back) or, for a short reply, by the
  question we just asked (`asking`).
- District: lookup table (agent.districts), canonical English name.
- Category: unambiguous phrases anywhere; short codes ("sc", "st") only as an answer.
- Gender and yes/no fields: only as the answer to our question (free text goes to the LLM).
- Topic (which kind of scheme the citizen asks about), "proceed" / "status" requests,
  documents the citizen says they have or don't have.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from agent.districts import find_district
from agent.gate import parse_decision
from agent.numbers import NumberSpan, find_numbers, normalize, tokenize

NUMERIC_FIELDS = ("age", "annual_income")
BOOL_FIELDS = ("is_student", "owns_farmland", "pays_income_tax", "govt_job_or_big_pension",
               "is_family_head")
CHOICE_FIELDS = ("gender", "category", "district")
FIELDS = (*NUMERIC_FIELDS, *CHOICE_FIELDS, *BOOL_FIELDS)

AGE_MIN, AGE_MAX = 10, 120


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

TOPICS: dict[str, list[tuple[str, ...]]] = {
    "pension": _phrases("pension", "old age", "ಪಿಂಚಣಿ", "ವೃದ್ಧಾಪ್ಯ", "पेंशन", "पेन्शन",
                        "वृद्धावस्था", "pinchani"),
    "farmer": _phrases("farmer", "farmers", "kisan", "farming", "ರೈತ", "ಕೃಷಿ", "ಕಿಸಾನ್",
                       "किसान", "खेती", "raita"),
    "scholarship": _phrases("scholarship", "scholarships", "ವಿದ್ಯಾರ್ಥಿ ವೇತನ", "ವಿದ್ಯಾರ್ಥಿವೇತನ",
                            "ಸ್ಕಾಲರ್‌ಶಿಪ್", "ಸ್ಕಾಲರ್ಶಿಪ್", "छात्रवृत्ति", "स्कॉलरशिप", "वजीफा"),
    "women": _phrases("gruha lakshmi", "gruhalakshmi", "griha lakshmi", "ಗೃಹಲಕ್ಷ್ಮಿ",
                      "ಗೃಹ ಲಕ್ಷ್ಮಿ", "गृह लक्ष्मी", "गृहलक्ष्मी"),
}

PROCEED = _phrases(
    "proceed", "apply", "continue", "go ahead", "fill the form", "fill form", "start",
    "ಮುಂದುವರಿಸಿ", "ಮುಂದುವರೆಸಿ", "ಅರ್ಜಿ ಹಾಕಿ", "ಅರ್ಜಿ ತುಂಬಿ", "ಶುರು ಮಾಡಿ",
    "आगे बढ़ें", "आगे बढ़ो", "आवेदन करें", "आवेदन करो", "फॉर्म भरो", "फॉर्म भरें", "शुरू करो",
    "apply karo", "aage badho",
)
STATUS = _phrases(
    "status", "application status", "my application", "ಸ್ಥಿತಿ", "ಅರ್ಜಿಯ ಸ್ಥಿತಿ",
    "स्थिति", "स्टेटस", "आवेदन की स्थिति",
)

CATEGORY_PHRASES: dict[str, list[tuple[str, ...]]] = {
    "SC": _phrases("scheduled caste", "ಪರಿಶಿಷ್ಟ ಜಾತಿ", "अनुसूचित जाति", "dalit", "ದಲಿತ", "दलित"),
    "ST": _phrases("scheduled tribe", "ಪರಿಶಿಷ್ಟ ಪಂಗಡ", "अनुसूचित जनजाति", "adivasi", "आदिवासी"),
    "OBC": _phrases("obc", "other backward", "ಹಿಂದುಳಿದ ವರ್ಗ", "ಒಬಿಸಿ", "पिछड़ा वर्ग", "ओबीसी"),
    "General": _phrases("general category", "ಸಾಮಾನ್ಯ ವರ್ಗ", "सामान्य वर्ग"),
}
CATEGORY_ANSWERS: dict[str, list[tuple[str, ...]]] = {
    "SC": _phrases("sc", "ಎಸ್ಸಿ", "एससी"),
    "ST": _phrases("st", "ಎಸ್ಟಿ", "एसटी"),
    "OBC": _phrases("obc", "bc", "ಬಿಸಿ"),
    "General": _phrases("general", "gen", "ಸಾಮಾನ್ಯ", "सामान्य", "जनरल", "none", "no caste"),
}
# Free text: only "I am a woman"-style statements (first person + a singular word), and
# flagged for read-back because "मैं महिला योजना के बारे में..." would also match.
FIRST_PERSON = _phrases("i", "ನಾನು", "मैं", "main")
GENDER_SAID: dict[str, set[str]] = {
    g: {normalize(w) for w in words} for g, words in {
        "female": {"woman", "lady", "ಮಹಿಳೆ", "ಹೆಂಗಸು", "महिला", "औरत"},
        "male": {"man", "ಪುರುಷ", "ಗಂಡಸು", "पुरुष", "आदमी"},
    }.items()
}
GENDER_ANSWERS: dict[str, list[tuple[str, ...]]] = {
    "female": _phrases("female", "woman", "women", "lady", "girl", "mahila", "ಮಹಿಳೆ",
                       "ಹೆಣ್ಣು", "ಸ್ತ್ರೀ", "महिला", "औरत", "स्त्री", "लड़की"),
    "male": _phrases("male", "man", "boy", "gents", "purush", "ಪುರುಷ", "ಗಂಡು", "पुरुष",
                     "आदमी", "मर्द", "लड़का"),
}

DOCS: dict[str, list[tuple[str, ...]]] = {
    "aadhaar": _phrases("aadhaar", "aadhar", "adhar", "ಆಧಾರ್", "ಆಧಾರ", "आधार"),
    "income_certificate": _phrases("income certificate", "ಆದಾಯ ಪ್ರಮಾಣ", "आय प्रमाण"),
    "residence_proof": _phrases("residence", "address proof", "domicile", "ವಾಸಸ್ಥಳ",
                                "ನಿವಾಸ", "निवास"),
    "bank_passbook": _phrases("passbook", "bank account", "bank book", "ಪಾಸ್ ಬುಕ್",
                              "ಪಾಸ್‌ಬುಕ್", "ಬ್ಯಾಂಕ್ ಖಾತೆ", "पासबुक", "बैंक खाता"),
    "photo": _phrases("photo", "photograph", "ಫೋಟೋ", "ಭಾವಚಿತ್ರ", "फोटो", "फ़ोटो"),
    "ration_card": _phrases("ration", "ರೇಷನ್", "ಪಡಿತರ", "राशन"),
    "land_records": _phrases("rtc", "pahani", "land record", "land records", "ಪಹಣಿ",
                             "khatauni", "खतौनी", "भूमि रिकॉर्ड"),
    "caste_certificate": _phrases("caste certificate", "ಜಾತಿ ಪ್ರಮಾಣ", "जाति प्रमाण"),
    "marksheet": _phrases("marksheet", "marks card", "mark sheet", "ಅಂಕಪಟ್ಟಿ", "मार्कशीट",
                          "अंकतालिका"),
}
HAVE_CUES = _phrases("have", "got", "ve", "ಇದೆ", "ಇವೆ", "ಇದ್ದಾವೆ", "ಹತ್ತಿರ", "है", "हैं", "पास",
                     "hai", "paas")
NEGATION = _phrases("no", "not", "don", "dont", "without", "missing", "lost", "haven",
                    "ಇಲ್ಲ", "ಇಲ್ಲಾ", "ಇಲ್ಲದ", "नहीं", "नही", "nahi", "nahin", "illa")
_CLAUSE_SPLIT = re.compile(r"[,.;:!?।]|\b(?:but|and|also)\b|ಆದರೆ|ಮತ್ತು|लेकिन|और|पर\b|lekin|aur")

# Extra yes/no words for answers to a question ("ಇದೆ" = there is). The submit gate does
# not use these; it keeps its own stricter list.
ANSWER_YES = _phrases("ಇದೆ", "ಇದ್ದೇನೆ", "ಹೌದು", "है", "हूं", "हूँ", "i am", "i do", "yes i")
ANSWER_NO = _phrases("ಇಲ್ಲ", "ಇಲ್ಲಾ", "नहीं", "नही", "no", "not", "never")


@dataclass
class Facts:
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # field -> how we know it
    readback: set[str] = field(default_factory=set)  # fields to flag at review
    numbers: list[NumberSpan] = field(default_factory=list)
    unattributed: list[NumberSpan] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    proceed: bool | None = None  # True: wants to apply; False: declined
    status: bool = False
    docs_have: list[str] = field(default_factory=list)
    docs_missing: list[str] = field(default_factory=list)
    answered: bool = False  # the pending question was answered deterministically

    def set(self, name: str, value: Any, source: str, flag: bool = False) -> None:
        self.values[name] = value
        self.sources[name] = source
        if flag:
            self.readback.add(name)


def _window(tokens: list[str], span: NumberSpan, before: int = 3, after: int = 3) -> list[str]:
    return tokens[max(0, span.start - before) : span.end + after]


def _age_prefix(tokens: list[str], span: NumberSpan) -> bool:
    """"I'm 62", "I am 62", "aged 62": an age even without a "years" cue."""
    before = tokens[max(0, span.start - 2) : span.start]
    return before[-1:] in (["im"], ["aged"]) or before == ["i", "m"] or before == ["i", "am"]


CURRENCY_AFTER = _phrases("rupees", "rupee", "rs", "ರೂಪಾಯಿ", "ರೂ", "रुपये", "रुपए", "रुपया", "rupaye")


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


def _choice(tokens: list[str], table: dict[str, list[tuple[str, ...]]]) -> str | None:
    hits = [k for k, phrases in table.items() if _has(tokens, phrases)]
    return hits[0] if len(hits) == 1 else None


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
        # "income certificate" also contains the income cue; that's fine, docs are separate.
        if _has(toks, NEGATION):
            facts.docs_missing += [d for d in docs if d not in facts.docs_missing]
        elif _has(toks, HAVE_CUES) or _has(tokenize(text), HAVE_CUES):
            facts.docs_have += [d for d in docs if d not in facts.docs_have]


def _answer(text: str, tokens: list[str], asking: str, facts: Facts) -> None:
    """The message as a reply to the question we asked (`asking` = a field or "proceed")."""
    if asking == "proceed":
        if facts.values or facts.docs_have or facts.docs_missing:
            return  # "I have Aadhaar but no caste certificate" is not a "no" to applying
        yn = answer_yes_no(text)
        if yn is not None:
            facts.proceed = yn
            facts.answered = True
        return
    if asking in facts.values:
        facts.answered = True
        return
    if asking in NUMERIC_FIELDS:
        if len(facts.unattributed) == 1:
            span = facts.unattributed.pop()
            if asking != "age" or AGE_MIN <= span.value <= AGE_MAX:
                facts.set(asking, span.value, "answer_" + ("number_words" if span.words else "digits"))
                facts.answered = True
            else:
                facts.unattributed.append(span)
    elif asking in BOOL_FIELDS:
        yn = answer_yes_no(text)
        if yn is not None:
            facts.set(asking, yn, "answer")
            facts.answered = True
    elif asking == "gender":
        g = _choice(tokens, GENDER_ANSWERS)
        if g:
            facts.set("gender", g, "answer")
            facts.answered = True
    elif asking == "category":
        c = _choice(tokens, CATEGORY_PHRASES) or _choice(tokens, CATEGORY_ANSWERS)
        if c:
            facts.set("category", c, "answer")
            facts.answered = True


def extract(text: str, asking: str | None = None) -> Facts:
    tokens = tokenize(text)
    facts = Facts(numbers=find_numbers(tokens))
    _attribute_numbers(tokens, facts)

    district = find_district(text)
    if district:
        facts.set("district", district, "lookup")
    category = _choice(tokens, CATEGORY_PHRASES)
    if not category and len(tokens) == 1:  # a bare "SC" / "ST" / "OBC" reply
        category = _choice(tokens, CATEGORY_ANSWERS)
    if category:
        facts.set("category", category, "said")
    if _has(tokens, FIRST_PERSON):
        said = [g for g, words in GENDER_SAID.items() if any(t in words for t in tokens)]
        if len(said) == 1:
            facts.set("gender", said[0], "said", flag=True)

    facts.topics = [t for t, phrases in TOPICS.items() if _has(tokens, phrases)]
    facts.status = _has(tokens, STATUS)
    if _has(tokens, PROCEED):
        facts.proceed = True
    _documents(text, facts)

    if asking:
        _answer(text, tokens, asking, facts)
    return facts
