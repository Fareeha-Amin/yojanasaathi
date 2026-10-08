"""Rules engine (three-valued JSON Logic) and the scheme files in rules/."""

import pytest

from agent import rules
from agent.checklist import build
from agent.districts import KARNATAKA_DISTRICTS
from agent.rules import UNKNOWN, clauses, evaluate

PENSION = {"and": [{">=": [{"var": "age"}, 60]}, {"<=": [{"var": "annual_income"}, 300000]}]}


@pytest.mark.parametrize("data, result", [
    ({"age": 62, "annual_income": 120000}, True),
    ({"age": 59, "annual_income": 120000}, False),
    ({"age": 62, "annual_income": 300001}, False),
    ({"age": 60, "annual_income": 300000}, True),  # both limits inclusive
    ({"age": 62}, UNKNOWN),  # still possible: ask income
    ({"age": 55}, False),  # decided without income: don't ask it
    ({}, UNKNOWN),
])
def test_kleene_and(data, result):
    assert evaluate(PENSION, data) is result


def test_kleene_or_and_not():
    rule = {"or": [{"==": [{"var": "a"}, 1]}, {"==": [{"var": "b"}, 1]}]}
    assert evaluate(rule, {"a": 1}) is True
    assert evaluate(rule, {"a": 0}) is UNKNOWN
    assert evaluate(rule, {"a": 0, "b": 0}) is False
    assert evaluate({"!": {"var": "x"}}, {}) is UNKNOWN
    assert evaluate({"!": {"var": "x"}}, {"x": False}) is True


def test_operators():
    assert evaluate({"in": [{"var": "d"}, ["Tumakuru", "Mysuru"]]}, {"d": "Mysuru"}) is True
    assert evaluate({"<=": [18, {"var": "age"}, 35]}, {"age": 20}) is True  # between
    assert evaluate({"<": [18, {"var": "age"}, 35]}, {"age": 35}) is False
    assert evaluate({"if": [{">": [{"var": "x"}, 1]}, "big", "small"]}, {"x": 2}) == "big"
    assert evaluate({"var": ["missing", 7]}, {}) == 7
    assert evaluate({"==": [{"var": "flag"}, True]}, {"flag": 1}) is False  # True is not 1
    assert evaluate({">=": [{"var": "age"}, 60]}, {"age": "sixty"}) is False


def test_unsupported_operator_rejected():
    with pytest.raises(ValueError):
        evaluate({"map": [[1], {"var": ""}]}, {})
    with pytest.raises(ValueError):
        rules.validate({"and": [{"merge": [1, 2]}]})


def test_clauses_explain_the_decision():
    cs = clauses(PENSION, {"age": 62, "annual_income": 120000})
    assert [(c.field, c.op, c.limit, c.value, c.result) for c in cs] == [
        ("age", ">=", 60, 62, True), ("annual_income", "<=", 300000, 120000, True)]
    assert clauses(PENSION, {"age": 62})[1].result is None


# --- scheme files -----------------------------------------------------------------------

SCHEMES = rules.load_schemes()


def test_three_to_five_schemes_with_required_metadata():
    assert 3 <= len(SCHEMES) <= 5
    assert "pension-001" in SCHEMES
    for s in SCHEMES.values():
        for key in ("rule", "required_fields", "documents", "source_url", "effective_date",
                    "verification", "topic", "titles", "source_name"):
            assert s.get(key), (s["scheme_id"], key)
        assert set(s["titles"]) == set(s["source_name"]) == {"en", "kn", "hi"}
        # verified rules cite an official URL; unverified ones say DEMO
        if not s["verification"].startswith("VERIFIED"):
            assert s["verification"].startswith("DEMO") and "DEMO" in s["source_url"]
        else:
            assert s["source_url"].startswith("https://")


def test_required_fields_are_exactly_the_rule_variables():
    for s in SCHEMES.values():
        assert {c.field for c in clauses(s["rule"], {})} == set(s["required_fields"]), s["scheme_id"]


def test_different_rule_shapes():
    ops = {s["scheme_id"]: {c.op for c in clauses(s["rule"], {})} for s in SCHEMES.values()}
    assert ops["pension-001"] == {">=", "<="}
    assert ops["pm-kisan"] == {"=="}
    assert ops["post-matric-sc"] == {"==", "<="}
    assert ops["gruha-lakshmi"] == {"==", "in"}


def test_pension_001_is_the_spec():
    s = SCHEMES["pension-001"]
    assert rules.status(s, {"age": 62, "annual_income": 120000}) == "eligible"
    assert rules.status(s, {"age": 62, "annual_income": 400000}) == "not_eligible"
    assert rules.status(s, {"age": 62}) == "unknown"
    assert rules.missing_fields(s, {"age": 62}) == ["annual_income"]


@pytest.mark.parametrize("profile, status", [
    ({"owns_farmland": True, "pays_income_tax": False, "govt_job_or_big_pension": False}, "eligible"),
    ({"owns_farmland": False}, "not_eligible"),  # tenant farmer: land must be in own name
    ({"owns_farmland": True, "pays_income_tax": True}, "not_eligible"),
    ({"owns_farmland": True, "pays_income_tax": False}, "unknown"),
])
def test_pm_kisan(profile, status):
    assert rules.status(SCHEMES["pm-kisan"], profile) == status


@pytest.mark.parametrize("profile, status", [
    ({"is_student": True, "category": "SC", "annual_income": 250000}, "eligible"),
    ({"is_student": True, "category": "SC", "annual_income": 250001}, "not_eligible"),
    ({"is_student": True, "category": "OBC", "annual_income": 100000}, "not_eligible"),
    ({"is_student": False}, "not_eligible"),
])
def test_post_matric_sc(profile, status):
    assert rules.status(SCHEMES["post-matric-sc"], profile) == status


@pytest.mark.parametrize("profile, status", [
    ({"gender": "female", "is_family_head": True, "district": "Tumakuru", "pays_income_tax": False}, "eligible"),
    ({"gender": "female", "is_family_head": True, "district": "Chennai", "pays_income_tax": False}, "not_eligible"),
    ({"gender": "male"}, "not_eligible"),
    ({"gender": "female", "district": "Mysuru"}, "unknown"),
])
def test_gruha_lakshmi(profile, status):
    assert rules.status(SCHEMES["gruha-lakshmi"], profile) == status


def test_gruha_lakshmi_district_list_matches_lookup_table():
    rule_list = next(c.limit for c in clauses(SCHEMES["gruha-lakshmi"]["rule"], {}) if c.op == "in")
    assert sorted(rule_list) == KARNATAKA_DISTRICTS


# --- document checklist -------------------------------------------------------------------


def test_checklist_marks_have_missing_needed():
    items = build(SCHEMES["pension-001"], {}, have=["aadhaar", "photo"], missing=["income_certificate"])
    assert {i["doc"]: i["status"] for i in items} == {
        "aadhaar": "have", "income_certificate": "missing", "residence_proof": "needed",
        "bank_passbook": "needed", "photo": "have"}


def test_checklist_when_condition():
    scheme = {"documents": [{"doc": "aadhaar", "when": None},
                            {"doc": "caste_certificate", "when": {"in": [{"var": "category"}, ["SC", "ST"]]}}]}
    assert [i["doc"] for i in build(scheme, {"category": "General"})] == ["aadhaar"]
    assert [i["doc"] for i in build(scheme, {"category": "SC"})] == ["aadhaar", "caste_certificate"]
    assert [i["doc"] for i in build(scheme, {})] == ["aadhaar", "caste_certificate"]  # unknown: keep
