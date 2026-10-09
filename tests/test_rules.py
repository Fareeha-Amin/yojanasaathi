"""Rules engine (three-valued JSON Logic) and the scheme files in rules/."""

import pytest

from agent import rules
from agent.checklist import build
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


# --- scheme files (the 4 mock-portal schemes; seed sync is tests/test_portal_seed.py) ------

SCHEMES = rules.load_schemes()


def test_four_portal_schemes_in_priority_order():
    assert list(SCHEMES) == ["pension-001", "pension-002", "health-001", "health-002"]


def test_required_fields_are_exactly_the_rule_variables():
    for s in SCHEMES.values():
        assert {c.field for c in clauses(s["rule"], {})} == set(s["required_fields"]), s["scheme_id"]


def test_golden_path_citizen_qualifies_for_all_four():
    profile = {"age": 62, "annual_income": 120000}
    assert {sid: rules.status(s, profile) for sid, s in SCHEMES.items()} == dict.fromkeys(SCHEMES, "eligible")


@pytest.mark.parametrize("sid, profile, status", [
    ("pension-001", {"age": 62, "annual_income": 300000}, "eligible"),
    ("pension-001", {"age": 62, "annual_income": 300001}, "not_eligible"),
    ("pension-001", {"age": 59}, "not_eligible"),
    ("pension-001", {"age": 62}, "unknown"),
    ("pension-002", {"age": 60}, "eligible"),
    ("pension-002", {"age": 59, "annual_income": 10}, "not_eligible"),
    ("health-001", {"annual_income": 500000}, "eligible"),
    ("health-001", {"annual_income": 500001}, "not_eligible"),
    ("health-001", {"age": 30}, "unknown"),
    ("health-002", {"annual_income": 400000}, "eligible"),
    ("health-002", {"annual_income": 450000}, "not_eligible"),
])
def test_portal_thresholds(sid, profile, status):
    assert rules.status(SCHEMES[sid], profile) == status


def test_document_labels_in_three_languages():
    assert rules.doc_label(SCHEMES["pension-001"], "age_proof", "kn") == "ವಯಸ್ಸಿನ ಪುರಾವೆ"
    assert rules.doc_label(SCHEMES["pension-002"], "age_proof", "en") == "Age Proof, where applicable"
    assert rules.doc_label(SCHEMES["health-001"], "medical_documents", "hi") == "चिकित्सा दस्तावेज (जहां लागू हो)"


# --- document checklist -------------------------------------------------------------------


def test_checklist_marks_have_missing_needed():
    items = build(SCHEMES["pension-001"], {}, have=["identity_proof"], missing=["income_certificate"])
    assert {i["doc"]: i["status"] for i in items} == {
        "identity_proof": "have", "age_proof": "needed", "residence_proof": "needed",
        "income_certificate": "missing", "bank_account_details": "needed"}


def test_checklist_when_condition():
    scheme = {"documents": [{"doc": "identity_proof", "when": None},
                            {"doc": "medical_documents", "when": {"==": [{"var": "needs_treatment"}, True]}}]}
    assert [i["doc"] for i in build(scheme, {"needs_treatment": False})] == ["identity_proof"]
    assert [i["doc"] for i in build(scheme, {"needs_treatment": True})] == ["identity_proof", "medical_documents"]
    assert [i["doc"] for i in build(scheme, {})] == ["identity_proof", "medical_documents"]  # unknown: keep
