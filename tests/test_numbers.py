"""Number words in kn / hi / en -> integers, deterministically (the LLM never converts)."""

import pytest

from agent.numbers import find_numbers, parse_number, tokenize


@pytest.mark.parametrize("text, value", [
    # the two required cases
    ("ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ", 120000),
    ("एक लाख बीस हज़ार", 120000),
    # the LLM's mistake from testing (read as 102000)
    ("ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ ರೂಪಾಯಿ", 120000),
    # Kannada
    ("ಅರವತ್ತೆರಡು", 62),
    ("ಅರವತ್ತೆರಡು ವರ್ಷ", 62),
    ("ಅರವತ್ತು ಎರಡು", 62),
    ("ಇಪ್ಪತ್ಮೂರು", 23),
    ("ಇಪ್ಪತ್ತಮೂರು", 23),
    ("ಎಪ್ಪತ್ನಾಲ್ಕು", 74),
    ("ತೊಂಬತ್ತೊಂಬತ್ತು", 99),
    ("ಹದಿನೆಂಟು", 18),
    ("ಐವತ್ತು ಸಾವಿರ", 50000),
    ("ಒಂದು ಲಕ್ಷದ ಇಪ್ಪತ್ತು ಸಾವಿರ", 120000),
    ("ಎರಡು ಲಕ್ಷ", 200000),
    ("ಒಂದೂವರೆ ಲಕ್ಷ", 150000),
    ("ಎರಡೂವರೆ ಲಕ್ಷ", 250000),
    ("ಇನ್ನೂರ ಐವತ್ತು", 250),
    ("ಐನೂರು", 500),
    ("ಸಾವಿರ", 1000),
    ("ಮೂರು ಲಕ್ಷ", 300000),
    ("1 ಲಕ್ಷ 20 ಸಾವಿರ", 120000),
    ("೬೨", 62),
    ("ondu laksha ippattu saavira", 120000),
    # Hindi
    ("बासठ", 62),
    ("बासठ साल", 62),
    ("निन्यानबे", 99),
    ("निन्यानवे", 99),
    ("पचास हजार", 50000),
    ("डेढ़ लाख", 150000),
    ("ढाई लाख", 250000),
    ("साढ़े तीन लाख", 350000),
    ("सवा लाख", 125000),
    ("दो लाख पचास हज़ार", 250000),
    ("पाँच सौ", 500),
    ("पांच सौ", 500),
    ("तीन लाख", 300000),
    ("एक करोड़", 10000000),
    ("६२", 62),
    ("ek lakh bees hazaar", 120000),
    ("do lakh", 200000),
    # English
    ("sixty-two", 62),
    ("sixty two", 62),
    ("one lakh twenty thousand", 120000),
    ("1 lakh 20 thousand", 120000),
    ("a lakh", 100000),
    ("two hundred and fifty", 250),
    ("one thousand two hundred", 1200),
    ("three lakhs", 300000),
    ("2.5 lakh", 250000),
    ("1.5 lac", 150000),
    ("50k", 50000),
    ("2.5L", 250000),
    ("1,20,000", 120000),
    ("₹1,20,000", 120000),
    ("Rs. 50,000", 50000),
    ("120000", 120000),
    ("62", 62),
])
def test_single_number(text, value):
    assert parse_number(text) == value


def test_two_numbers_in_one_sentence():
    spans = find_numbers(tokenize("I'm 62 and our income is 1 lakh 20 thousand"))
    assert [s.value for s in spans] == [62, 120000]
    assert spans[1].scaled and spans[1].words and not spans[0].words


def test_kannada_sentence():
    spans = find_numbers(tokenize("ನನಗೆ 62 ವರ್ಷ. ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ"))
    assert [s.value for s in spans] == [62, 120000]


def test_sentence_period_is_not_a_decimal_point():
    assert [s.value for s in find_numbers(tokenize("I am 62. Can I get a pension?"))] == [62]


def test_adjacent_numbers_do_not_merge():
    assert [s.value for s in find_numbers(tokenize("62 120000"))] == [62, 120000]
    assert [s.value for s in find_numbers(tokenize("twenty twenty"))] == [20, 20]


@pytest.mark.parametrize("text", [
    "do I get a pension?",  # "do" = 2 in romanised Hindi: weak word alone is ignored
    "I am a teen",
    "a farmer",
    "hello",
    "",
])
def test_no_false_numbers(text):
    assert find_numbers(tokenize(text)) == []


def test_invisible_chars_and_bom_are_ignored():
    assert parse_number("﻿ಅರವತ್ತೆರಡು") == 62
    assert parse_number("हज़ार") == 1000  # decomposed nukta
