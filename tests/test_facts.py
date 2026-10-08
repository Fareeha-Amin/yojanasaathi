"""Deterministic fact extraction: which number is the age, which the income, etc."""

import pytest

from agent.districts import KARNATAKA_DISTRICTS, find_district, local_name
from agent.facts import answer_yes_no, extract


@pytest.mark.parametrize("text, age, income", [
    ("ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", 62, None),
    ("ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ", 62, None),
    ("ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ", 62, 120000),
    ("मैं 62 साल का हूँ", 62, None),
    ("मेरी उम्र बासठ साल है और सालाना आमदनी एक लाख बीस हज़ार है", 62, 120000),
    ("Main 62 saal ki hoon. Kya mujhe pension mil sakti hai?", 62, None),
    ("I'm 62, can I get a pension?", 62, None),
    ("I'm 55 with income 1 lakh, pension?", 55, 100000),
    ("I am 62 years old and earn 90000 a year", 62, 90000),
    ("our income is ₹1,20,000", None, 120000),
    ("we earn 800 rupees", None, 800),
])
def test_age_and_income(text, age, income):
    f = extract(text)
    assert f.values.get("age") == age
    assert f.values.get("annual_income") == income


def test_digits_and_words_are_not_flagged():
    f = extract("ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ")
    assert f.sources == {"age": "digits", "annual_income": "number_words"}
    assert f.readback == set()


def test_monthly_income_is_annualised_and_flagged():
    f = extract("I earn 10 thousand per month")
    assert f.values["annual_income"] == 120000
    assert "annual_income" in f.readback


@pytest.mark.parametrize("asking, text, field, value", [
    ("annual_income", "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ", "annual_income", 120000),
    ("annual_income", "एक लाख बीस हज़ार", "annual_income", 120000),
    ("annual_income", "80000", "annual_income", 80000),
    ("age", "62", "age", 62),
    ("age", "ಅರವತ್ತೆರಡು", "age", 62),
    ("owns_farmland", "ಹೌದು, ಇದೆ", "owns_farmland", True),
    ("owns_farmland", "ಇಲ್ಲ", "owns_farmland", False),
    ("pays_income_tax", "नहीं", "pays_income_tax", False),
    ("is_student", "yes", "is_student", True),
    ("gender", "ಮಹಿಳೆ", "gender", "female"),
    ("gender", "पुरुष", "gender", "male"),
    ("category", "SC", "category", "SC"),
    ("category", "ಪರಿಶಿಷ್ಟ ಜಾತಿ", "category", "SC"),
    ("category", "अनुसूचित जनजाति", "category", "ST"),
    ("district", "ತುಮಕೂರು", "district", "Tumakuru"),
])
def test_answer_to_pending_question(asking, text, field, value):
    f = extract(text, asking)
    assert f.values[field] == value
    assert f.answered


def test_age_answer_out_of_range_is_not_taken():
    f = extract("5", "age")
    assert "age" not in f.values and not f.answered


def test_short_codes_only_as_answers():
    assert "category" not in extract("I study B.Sc in college").values  # "sc" token
    assert "category" not in extract("I came 1st in class").values  # "st" token
    assert extract("SC").values["category"] == "SC"  # a bare reply


def test_first_person_gender_is_flagged():
    f = extract("ನಾನು ತುಮಕೂರಿನ ಮಹಿಳೆ")
    assert f.values["gender"] == "female" and "gender" in f.readback
    assert f.values["district"] == "Tumakuru"
    assert "gender" not in extract("scheme for women").values


@pytest.mark.parametrize("text, topics", [
    ("ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", ["pension"]),
    ("क्या मुझे पेंशन मिलेगी?", ["pension"]),
    ("scholarship for my son", ["scholarship"]),
    ("ಗೃಹಲಕ್ಷ್ಮಿ ಯೋಜನೆ", ["women"]),
    ("मैं किसान हूँ", ["farmer"]),
    ("hello", []),
])
def test_topics(text, topics):
    assert extract(text).topics == topics


def test_proceed_and_decline():
    assert extract("ಹೌದು", "proceed").proceed is True
    assert extract("नहीं", "proceed").proceed is False
    assert extract("please apply").proceed is True
    # a document statement with "no" in it is not a refusal to apply
    f = extract("I have aadhaar and photo but no caste certificate", "proceed")
    assert f.proceed is None
    assert f.docs_have == ["aadhaar", "photo"] and f.docs_missing == ["caste_certificate"]


def test_documents_kannada():
    f = extract("ನನ್ನ ಹತ್ತಿರ ಆಧಾರ್ ಇದೆ, ಆದಾಯ ಪ್ರಮಾಣಪತ್ರ ಇಲ್ಲ")
    assert f.docs_have == ["aadhaar"] and f.docs_missing == ["income_certificate"]


def test_yes_no_answers():
    assert answer_yes_no("ಹೌದು") is True
    assert answer_yes_no("ಇದೆ") is True
    assert answer_yes_no("नहीं है") is False
    assert answer_yes_no("maybe") is None


@pytest.mark.parametrize("text, district", [
    ("I live near Tumkur", "Tumakuru"),
    ("ತುಮಕೂರಿನಲ್ಲಿ ಇದ್ದೇನೆ", "Tumakuru"),
    ("मैं तुमकुर में रहती हूँ", "Tumakuru"),
    ("Bangalore rural", "Bengaluru Rural"),
    ("ಬೆಂಗಳೂರು", "Bengaluru Urban"),
    ("Mysore", "Mysuru"),
    ("ಮೈಸೂರಿನವರು", "Mysuru"),
    ("Hubli", "Dharwad"),
    ("dakshina kannada", "Dakshina Kannada"),
    ("I speak Kannada", None),  # the language is not a district
    ("Chennai", None),
])
def test_district_lookup(text, district):
    assert find_district(text) == district


def test_31_districts_with_local_names():
    assert len(KARNATAKA_DISTRICTS) == 31
    assert local_name("Tumakuru", "kn") == "ತುಮಕೂರು"
    assert local_name("Tumakuru", "hi") == "तुमकुरु"
    assert local_name("Tumakuru", "en") == "Tumakuru"
