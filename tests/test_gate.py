import pytest

from agent.gate import parse_decision


@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "YES, submit", "ಹೌದು", "ಹೌದು.", "ಹೌದು, ಸಲ್ಲಿಸಿ", "haudu", "हाँ", "हां।", "haan", "theek hai", "जी हाँ"],
)
def test_yes(text):
    assert parse_decision(text) == "yes"


@pytest.mark.parametrize("text", ["﻿ಹೌದು", "ಹೌ‍ದು", "‌हाँ"])
def test_yes_ignores_invisible_format_chars(text):
    assert parse_decision(text) == "yes"


@pytest.mark.parametrize(
    "text",
    ["no", "No!", "not now", "don't", "cancel", "ಇಲ್ಲ", "ಬೇಡ", "illa", "नहीं", "nahi"],
)
def test_no(text):
    assert parse_decision(text) == "no"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "what documents do I need?",
        "maybe",
        "ok",  # too weak to submit an application on
        "yes, but don't submit",  # mixed -> never submit
        "no, submit it",
        "ಹೌದು ಇಲ್ಲ",
        "yesterday",  # substring of "yes" is not a yes
    ],
)
def test_unclear_never_submits(text):
    assert parse_decision(text) == "unclear"
