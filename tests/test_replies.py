"""Reply templates: same keys and placeholders in kn / hi / en; formatting helpers."""

import re

import pytest

from agent import replies
from agent.graph import FIELD_ORDER
from agent.replies import money, strings, t
from agent.rules import Clause

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def keys(lang):
    return {k for k in strings(lang) if not k.startswith("_")}


@pytest.mark.parametrize("lang", ["kn", "hi"])
def test_same_keys_as_english(lang):
    assert keys(lang) == keys("en")


@pytest.mark.parametrize("lang", ["kn", "hi"])
def test_same_placeholders_as_english(lang):
    for k in keys("en"):
        assert set(PLACEHOLDER.findall(strings(lang)[k])) == set(PLACEHOLDER.findall(strings("en")[k])), k


@pytest.mark.parametrize("lang", ["kn", "hi"])
def test_kannada_and_hindi_marked_for_review(lang):
    assert "NEEDS NATIVE-SPEAKER REVIEW" in strings(lang)["_review"]


def test_every_rule_field_has_a_question_and_label():
    for f in FIELD_ORDER:
        assert f"ask_{f}" in keys("en") and f"label_{f}" in keys("en")


def test_reply_scripts_match_language():
    # Voice picks the TTS language from the reply's script (voice/lang.py).
    assert any(0x0C80 <= ord(c) < 0x0D00 for c in t("ask_annual_income", "kn"))
    assert any(0x0900 <= ord(c) < 0x0980 for c in t("ask_annual_income", "hi"))
    assert t("ask_annual_income", "fr") == t("ask_annual_income", "en")  # unknown -> English


@pytest.mark.parametrize("amount, text", [
    (0, "₹0"), (800, "₹800"), (1000, "₹1,000"), (120000, "₹1,20,000"),
    (300000, "₹3,00,000"), (12345678, "₹1,23,45,678"),
])
def test_indian_money_format(amount, text):
    assert money(amount) == text


def test_dates():
    assert replies.day("2026-04-01", "en") == "1 April 2026"
    assert replies.day("2026-04-01", "kn") == "1 ಏಪ್ರಿಲ್ 2026"
    assert replies.day("2026-04-01", "hi") == "1 अप्रैल 2026"


def test_reasons():
    cs = [Clause("age", ">=", 60, 62, True), Clause("annual_income", "<=", 300000, 120000, True)]
    assert replies.reasons(cs, "en") == "age 62, at least 60 needed and annual income ₹1,20,000, limit ₹3,00,000"
    assert replies.reasons(cs, "kn") == "ವಯಸ್ಸು 62, ಕನಿಷ್ಠ 60 ಬೇಕು ಮತ್ತು ವಾರ್ಷಿಕ ಆದಾಯ ₹1,20,000, ಮಿತಿ ₹3,00,000"
    assert replies.reason(Clause("gender", "==", "female", "male", False), "en") == "gender: man, must be woman"
    assert replies.reason(Clause("district", "in", [], "Chennai", False), "en") == \
        "district: Chennai, not covered by this scheme"
    assert replies.facts_phrase({"age": 62, "annual_income": 120000}, "kn") == "ವಯಸ್ಸು 62 ಮತ್ತು ವಾರ್ಷಿಕ ಆದಾಯ ₹1,20,000"


def test_no_scheme_text_in_templates():
    # scheme titles and document names are the portal's wording (rules/*.json)
    assert not any(k.startswith(("doc_", "category_")) for k in keys("en"))
