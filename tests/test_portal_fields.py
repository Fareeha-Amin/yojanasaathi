"""Deterministic parts of Phase 4: form-answer parsers, sealing, the drift check, the /turn
edge (OTP + sensitive answers kept out of the graph) and read-back expansion."""

from datetime import date

import pytest

from agent import rules, sealed
from agent.portal import drift, edge, fields

SCHEMES = rules.load_schemes()
P1 = SCHEMES["pension-001"]
TODAY = date(2026, 10, 9)


@pytest.fixture(autouse=True)
def key():
    sealed.set_key(bytes(range(32)))


# --- dates ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, want", [
    ("5 March 1962", date(1962, 3, 5)),
    ("March 5th 1962", date(1962, 3, 5)),
    ("05/03/1962", date(1962, 3, 5)),
    ("5-3-62", date(1962, 3, 5)),
    ("1962-03-05", date(1962, 3, 5)),
    ("my date of birth is 21 sept 1960", date(1960, 9, 21)),
    ("twenty first september nineteen sixty", date(1960, 9, 21)),
    ("five March nineteen sixty two", date(1962, 3, 5)),
    ("ಮಾರ್ಚ್ 5 1962", date(1962, 3, 5)),
    ("5 ಮಾರ್ಚ್ 1962", date(1962, 3, 5)),
    ("ಐದು ಮಾರ್ಚ್ ಸಾವಿರದ ಒಂಬೈನೂರ ಅರವತ್ತೆರಡು", date(1962, 3, 5)),
    ("5 मार्च 1962", date(1962, 3, 5)),
    ("पांच मार्च उन्नीस सौ बासठ", date(1962, 3, 5)),
])
def test_dates(text, want):
    assert fields.parse_date(text, TODAY) == want


@pytest.mark.parametrize("text", ["sometime in spring", "31 February 1960", "5 March 2030", "March 1962",
                                  "1 2 3 4", "5 March 1880"])
def test_dates_rejected(text):
    assert fields.parse_date(text, TODAY) is None


# --- digits: mobile, account, IFSC, OTP -----------------------------------------------------

@pytest.mark.parametrize("text, want", [
    ("98765 43210", "9876543210"),
    ("+91 98765 43210", "9876543210"),
    ("09876543210", "9876543210"),
    ("nine eight seven six five four three two one zero", "9876543210"),
    ("double nine eight seven six five four three two one", "9987654321"),
    ("ಒಂಬತ್ತು ಎಂಟು ಏಳು ಆರು ಐದು ನಾಲ್ಕು ಮೂರು ಎರಡು ಒಂದು ಸೊನ್ನೆ", "9876543210"),
    ("नौ आठ सात छह पांच चार तीन दो एक शून्य", "9876543210"),
    ("೯೮೭೬೫೪೩೨೧೦", "9876543210"),
])
def test_mobile(text, want):
    assert fields.parse_mobile(text) == want


@pytest.mark.parametrize("text", ["12345", "1234567890", "98765 4321", "my number"])
def test_mobile_rejected(text):
    assert fields.parse_mobile(text) is None  # 10 digits starting 6-9 only


def test_account_and_otp():
    assert fields.parse_account("my account is 3456 7890 123") == "34567890123"
    assert fields.parse_account("12345") is None
    assert fields.parse_otp("the code is 4 5 6 7 8 9") == "456789"
    assert fields.parse_otp("12345") is None


@pytest.mark.parametrize("text, want", [
    ("SBIN0001234", "SBIN0001234"),
    ("sbin 000 1234", "SBIN0001234"),
    ("S B I N zero zero zero one two three four", "SBIN0001234"),
    ("S B I N O 0 0 1 2 3 4", "SBIN0001234"),
    ("my IFSC code is HDFC0ABC123", "HDFC0ABC123"),
    ("ಎಸ್ ಬಿ ಐ ಎನ್ 0 0 0 1 2 3 4", "SBIN0001234"),
    ("एस बी आई एन 0001234", "SBIN0001234"),
])
def test_ifsc(text, want):
    assert fields.parse_ifsc(text) == want


@pytest.mark.parametrize("text", ["SBIN1001234", "SBI0001234", "1234"])
def test_ifsc_rejected(text):
    assert fields.parse_ifsc(text) is None


# --- choices: the portal's exact English option ----------------------------------------------

@pytest.mark.parametrize("field, text, want", [
    ("gender", "male", "Male"), ("gender", "ಮಹಿಳೆ", "Female"), ("gender", "पुरुष", "Male"),
    ("marital_status", "I am married", "Married"), ("marital_status", "unmarried", "Single / Unmarried"),
    ("marital_status", "ವಿಧವೆ", "Widowed"), ("marital_status", "तलाकशुदा", "Divorced"),
    ("disbursement_mode", "bank transfer", "Direct Bank Transfer (DBT)"),
    ("disbursement_mode", "ಅಂಚೆ ಕಚೇರಿ", "Post Office Savings Bank (POSB)"),
    ("disbursement_mode", "the second one", "Post Office Savings Bank (POSB)"),
    ("disbursement_mode", "2", "Post Office Savings Bank (POSB)"),
])
def test_choices(field, text, want):
    assert fields.parse(field, text, P1).value == want


def test_choice_in_other_schemes():
    assert fields.parse("assistance_category", "disability", SCHEMES["pension-002"]).value == \
        "Disability Welfare Assistance"
    assert fields.parse("ration_card_category", "ಬಿಪಿಎಲ್", SCHEMES["health-002"]).value == "BPL (Below Poverty Line)"
    assert fields.parse("coverage_preference", "hospital admission", SCHEMES["health-002"]).value == \
        "In-Patient Hospitalization"


@pytest.mark.parametrize("text", ["maybe", "married or widowed"])
def test_choice_unclear(text):
    assert fields.parse("marital_status", text, P1) is None


def test_options_read_aloud_in_kannada():
    assert fields.options_text(P1, "marital_status", "kn") == "ವಿವಾಹಿತ, ವಿಧವೆ/ವಿಧುರ, ಅವಿವಾಹಿತ ಅಥವಾ ವಿಚ್ಛೇದಿತ"
    assert fields.choice_text(P1, "gender", "Female", "hi") == "महिला"


# --- names, text, the declaration --------------------------------------------------------------

@pytest.mark.parametrize("text, want", [
    ("my name is ramesh kumar", "Ramesh Kumar"),
    ("Ramesh Kumar.", "Ramesh Kumar"),
    ("ನನ್ನ ಹೆಸರು ರಮೇಶ್ ಕುಮಾರ್", "ರಮೇಶ್ ಕುಮಾರ್"),
    ("मेरा नाम रमेश कुमार है", "रमेश कुमार"),
])
def test_names(text, want):
    assert fields.parse_name(text) == want


@pytest.mark.parametrize("text", ["12", "a", "my name is 42 street", " ".join(["word"] * 9)])
def test_names_rejected(text):
    assert fields.parse_name(text) is None


def test_declaration_and_text():
    assert fields.parse("declaration_consent", "ಹೌದು", P1).value is True
    assert fields.parse("declaration_consent", "no", P1).value is False
    assert fields.parse("declaration_consent", "hmm", P1) is None
    assert fields.parse("address", "Tumkur", P1) is None  # too short for an address
    assert fields.parse("address", "12 Temple Road, Tumakuru", P1).value == "12 Temple Road, Tumakuru"


def test_every_portal_field_has_a_kind():
    for s in SCHEMES.values():
        for f in s["application_fields"]:
            assert f["name"] in fields.FIELD_KINDS, (s["scheme_id"], f["name"])


# --- spoken, masked -------------------------------------------------------------------------------

def test_spoken_and_masked():
    assert fields.spoken("bank_account_number", "34567890123", "en") == "3 4 5 6, 7 8 9 0, 1 2 3"
    assert fields.spoken("bank_ifsc", "SBIN0001234", "en") == "S B I N 0 0 0 1 2 3 4"
    assert fields.spoken("dob", "1962-03-05", "kn") == "5 ಮಾರ್ಚ್ 1962"
    assert fields.masked("bank_account_number", "34567890123") == "XXXXXX0123"
    assert fields.masked("mobile", "9876543210") == "XXXXXX3210"
    assert fields.masked("bank_ifsc", "SBIN0001234") == "SBIN0******"
    assert fields.masked("dob", "1962-03-05") == "**/**/1962"


@pytest.mark.parametrize("text, want", [
    ("no, the account number is wrong", "bank_account_number"),
    ("change the IFSC", "bank_ifsc"),
    ("my name is spelled wrong", "full_name"),
    ("the nominee name", "nominee_name"),
    ("ಹುಟ್ಟಿದ ದಿನಾಂಕ ತಪ್ಪು", "dob"),
    ("ಖಾತೆ ಸಂಖ್ಯೆ ಬದಲಿಸಿ", "bank_account_number"),
    ("पता गलत है", "address"),
    ("yes", None),
])
def test_field_mentions(text, want):
    assert fields.mentioned(text, [*fields.form_fields(P1), "mobile"]) == want


# --- sealing ----------------------------------------------------------------------------------------

def test_sealed_values_are_bound_to_case_and_field():
    s = sealed.seal("case-1", "bank_account_number", "34567890123")
    assert sealed.is_sealed(s) and "34567890123" not in s
    assert sealed.open_("case-1", "bank_account_number", s) == "34567890123"
    with pytest.raises(sealed.SealedError):
        sealed.open_("case-2", "bank_account_number", s)
    with pytest.raises(sealed.SealedError):
        sealed.open_("case-1", "mobile", s)
    assert sealed.seal("c", "f", "same") != sealed.seal("c", "f", "same")  # random nonce


# --- drift --------------------------------------------------------------------------------------

def _reqs(scheme: dict) -> dict:
    return {"application_fields": [{k: v for k, v in f.items() if k != "label"} | {"label": f["label"]["en"]}
                                   for f in scheme["application_fields"]],
            "required_documents": [d["label"]["en"] for d in scheme["documents"]]}


def test_no_drift_for_the_seeded_schemes():
    for s in SCHEMES.values():
        assert drift.compare(_reqs(s), s) == [], s["scheme_id"]


def test_drift_found():
    r = _reqs(P1)
    r["application_fields"][3]["options"] = ["Married", "Single"]
    r["application_fields"].append({"name": "caste", "type": "text", "required": True, "label": "Caste"})
    r["required_documents"] = r["required_documents"][:-1] + ["Ration Card"]
    diffs = drift.compare(r, P1)
    assert "field marital_status: options changed" in diffs
    assert "new field caste" in diffs and "field caste is not in the agent's field map" in diffs
    assert "new document Ration Card" in diffs and "document Bank Account Details removed" in diffs
    assert drift.compare({}, P1) == ["no application_fields in the portal's answer"]


def test_drift_label_and_order():
    r = _reqs(P1)
    r["application_fields"][0]["label"] = "Name"
    assert drift.compare(r, P1) == ["field full_name: label changed"]
    r = _reqs(P1)
    r["application_fields"][0], r["application_fields"][1] = r["application_fields"][1], r["application_fields"][0]
    assert drift.compare(r, P1) == ["fields in a different order"]


# --- the /turn edge ------------------------------------------------------------------------------

def test_otp_goes_to_the_inbox_not_the_graph():
    text, extra = edge.prepare("case-otp", {}, {"type": "otp"}, "the code is 4 5 6 7 8 9")
    assert text == "[code given]" and extra == {}
    assert edge.take_otp("case-otp") == "456789" and edge.take_otp("case-otp") is None
    text, _ = edge.prepare("case-otp", {}, {"type": "otp"}, "12 34 5")  # not 6 digits
    assert "12" not in text or text == "12 34 5"
    assert edge.prepare("case-otp", {}, {"type": "otp"}, "send again")[0] == "send again"


def test_sensitive_answer_is_sealed_at_the_edge():
    state = {"selected": "pension-001", "asking": "form:bank_account_number", "lang": "en"}
    text, extra = edge.prepare("case-e", state, None, "it is 3456 7890 123")
    assert text == "[bank_account_number given]"
    fi = extra["form_input"]
    assert sealed.open_("case-e", "bank_account_number", fi["sealed"]) == "34567890123"
    assert fi["shown"] == "XXXXXX0123"


def test_unreadable_sensitive_answer_hides_digits():
    state = {"selected": "pension-001", "asking": "form:mobile", "lang": "en"}
    text, extra = edge.prepare("case-e", state, None, "it's 12345 678")
    assert extra == {} and "12345" not in text


def test_correction_at_confirm_is_sealed():
    state = {"selected": "pension-001", "lang": "en"}
    text, extra = edge.prepare("case-e", state, {"type": "confirm"}, "no, the IFSC is HDFC0ABC123")
    assert text == "[bank_ifsc given]" and extra["form_input"]["shown"] == "HDFC0******"


def test_income_edit_at_confirm_is_untouched():
    state = {"selected": "pension-001", "lang": "en"}
    assert edge.prepare("case-e", state, {"type": "confirm"}, "no, my income is 200000") == \
        ("no, my income is 200000", {})


def test_expand_spoken_and_masked():
    state = {"form": {"dob": sealed.seal("case-x", "dob", "1962-03-05"),
                      "bank_account_number": sealed.seal("case-x", "bank_account_number", "34567890123")}}
    text = "Born ⟦dob⟧, account ⟦bank_account_number⟧."
    assert edge.expand("case-x", state, text, "en") == "Born 5 March 1962, account 3 4 5 6, 7 8 9 0, 1 2 3."
    assert edge.expand("case-x", state, text, "en", masked=True) == "Born **/**/1962, account XXXXXX0123."
    assert edge.expand("case-x", state, "no markers", "en") == "no markers"
