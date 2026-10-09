"""District names -> one English canonical name (the 31 districts of Karnataka).

The profile always stores the canonical English name, whatever language or old spelling
the citizen used ("ತುಮಕೂರಿನಲ್ಲಿ", "तुमकुर", "Tumkur" -> "Tumakuru"); it feeds the portal's
address field in Phase 4. A city that is not itself a district maps to its district
(Hubballi -> Dharwad, Mangaluru -> Dakshina Kannada).
"""

from agent.numbers import tokenize

# canonical: (kn, hi, other spellings...)
DISTRICTS: dict[str, tuple[str, ...]] = {
    "Bagalkot": ("ಬಾಗಲಕೋಟೆ", "बागलकोट", "bagalkote"),
    "Ballari": ("ಬಳ್ಳಾರಿ", "बल्लारी", "bellary", "बेल्लारी"),
    "Belagavi": ("ಬೆಳಗಾವಿ", "बेलगावी", "belgaum", "बेलगाम"),
    "Bengaluru Rural": ("ಬೆಂಗಳೂರು ಗ್ರಾಮಾಂತರ", "बेंगलुरु ग्रामीण", "bangalore rural"),
    "Bengaluru Urban": ("ಬೆಂಗಳೂರು", "बेंगलुरु", "bengaluru", "bangalore", "बैंगलोर",
                        "bengaluru urban", "bangalore urban", "ಬೆಂಗಳೂರು ನಗರ", "बेंगलुरु शहरी"),
    "Bidar": ("ಬೀದರ್", "बीदर", "ಬೀದರ"),
    "Chamarajanagar": ("ಚಾಮರಾಜನಗರ", "चामराजनगर", "chamarajanagara"),
    "Chikkaballapur": ("ಚಿಕ್ಕಬಳ್ಳಾಪುರ", "चिक्कबल्लापुर", "chikkaballapura", "chikballapur"),
    "Chikkamagaluru": ("ಚಿಕ್ಕಮಗಳೂರು", "चिक्कमगलुरु", "chikmagalur", "chikkamagalur"),
    "Chitradurga": ("ಚಿತ್ರದುರ್ಗ", "चित्रदुर्ग"),
    "Dakshina Kannada": ("ದಕ್ಷಿಣ ಕನ್ನಡ", "दक्षिण कन्नड़", "mangaluru", "mangalore",
                         "ಮಂಗಳೂರು", "मंगलौर", "मंगलुरु", "south canara"),
    "Davanagere": ("ದಾವಣಗೆರೆ", "दावणगेरे", "davangere", "दावणगेरे"),
    "Dharwad": ("ಧಾರವಾಡ", "धारवाड़", "dharwar", "hubballi", "hubli", "ಹುಬ್ಬಳ್ಳಿ", "हुबली"),
    "Gadag": ("ಗದಗ", "गदग"),
    "Hassan": ("ಹಾಸನ", "हासन"),
    "Haveri": ("ಹಾವೇರಿ", "हावेरी"),
    "Kalaburagi": ("ಕಲಬುರಗಿ", "कलबुरगी", "gulbarga", "गुलबर्गा"),
    "Kodagu": ("ಕೊಡಗು", "कोडगु", "coorg", "madikeri", "ಮಡಿಕೇರಿ"),
    "Kolar": ("ಕೋಲಾರ", "कोलार"),
    "Koppal": ("ಕೊಪ್ಪಳ", "कोप्पल"),
    "Mandya": ("ಮಂಡ್ಯ", "मांड्या", "मंड्या"),
    "Mysuru": ("ಮೈಸೂರು", "मैसूरु", "mysore", "मैसूर"),
    "Raichur": ("ರಾಯಚೂರು", "रायचूर"),
    "Ramanagara": ("ರಾಮನಗರ", "रामनगर", "ramanagaram", "bengaluru south"),
    "Shivamogga": ("ಶಿವಮೊಗ್ಗ", "शिवमोग्गा", "shimoga", "शिमोगा"),
    "Tumakuru": ("ತುಮಕೂರು", "तुमकुरु", "tumkur", "तुमकुर"),
    "Udupi": ("ಉಡುಪಿ", "उडुपी"),
    "Uttara Kannada": ("ಉತ್ತರ ಕನ್ನಡ", "उत्तर कन्नड़", "karwar", "ಕಾರವಾರ", "north canara"),
    "Vijayapura": ("ವಿಜಯಪುರ", "विजयपुरा", "bijapur", "बीजापुर"),
    "Vijayanagara": ("ವಿಜಯನಗರ", "विजयनगर", "hosapete", "hospet", "ಹೊಸಪೇಟೆ"),
    "Yadgir": ("ಯಾದಗಿರಿ", "यादगिर", "yadagiri"),
}

KARNATAKA_DISTRICTS: list[str] = sorted(DISTRICTS)


def _is_kannada(word: str) -> bool:
    return any(0x0C80 <= ord(c) < 0x0D00 for c in word)


def _build() -> dict[tuple[str, ...], str]:
    table: dict[tuple[str, ...], str] = {}
    for canonical, names in DISTRICTS.items():
        for name in (canonical, *names):
            table[tuple(tokenize(name))] = canonical
    return table


_TABLE = _build()
_LONGEST = max(len(k) for k in _TABLE)


def _token_matches(name_tok: str, tok: str) -> bool:
    if name_tok == tok:
        return True
    # Kannada inflects the name itself: ತುಮಕೂರು -> ತುಮಕೂರಿನಲ್ಲಿ, ಮೈಸೂರಿನವರು. Match the stem
    # (name without its final vowel sign) as a prefix.
    if _is_kannada(name_tok) and len(name_tok) >= 4:
        stem = name_tok[:-1] if not name_tok[-1].isalpha() else name_tok
        return tok.startswith(stem)
    return False


def find_district(text: str) -> str | None:
    """The first district named in `text` (canonical English), longest name first."""
    toks = tokenize(text)
    for i in range(len(toks)):
        for n in range(min(_LONGEST, len(toks) - i), 0, -1):
            window = toks[i : i + n]
            for key, canonical in _TABLE.items():
                if len(key) == n and all(_token_matches(k, t) for k, t in zip(key, window)):
                    return canonical
    return None


def normalize_district(name: str | None) -> str | None:
    """A district from free text (e.g. the LLM's answer) -> canonical name, or None."""
    if not name:
        return None
    return find_district(name)


def local_name(canonical: str, lang: str) -> str:
    names = DISTRICTS.get(canonical)
    if not names or lang == "en":
        return canonical
    return names[0] if lang == "kn" else names[1]
