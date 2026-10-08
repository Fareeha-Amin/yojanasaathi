"""Eligibility engine: JSON Logic rules from rules/*.json, decided by code, never the LLM.

Why our own evaluator instead of a JSON Logic package: the interview needs three answers,
not two. Standard JSON Logic reads a missing variable as null, so "age >= 60" with no age
is simply false and the citizen would be told "not eligible" before we asked anything.
Here a missing variable is UNKNOWN and propagates with Kleene logic:
  and: any false -> false; all true -> true; otherwise unknown
  or:  any true  -> true;  all false -> false; otherwise unknown
A scheme whose rule is unknown is "still possible", and the interview asks for its
missing required_fields.

Supported operators (anything else is rejected when the rules are loaded):
  var (with optional default), and, or, !, !!, ==, !=, <, <=, >, >= (< and <= also as
  "between" with 3 args), in (value in list, or substring in string), if.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Any = _Unknown()

OPERATORS = {"var", "and", "or", "!", "!!", "==", "!=", "<", "<=", ">", ">=", "in", "if"}
COMPARISONS = {"==", "!=", "<", "<=", ">", ">=", "in"}


def _truthy(v: Any) -> bool | Any:
    if v is UNKNOWN:
        return UNKNOWN
    if isinstance(v, (list, dict, str)):
        return len(v) > 0
    return bool(v)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b  # True must not equal 1
    return a == b


def _compare(op: str, args: list[Any]) -> Any:
    if any(a is UNKNOWN for a in args):
        return UNKNOWN
    if op == "==":
        return _eq(args[0], args[1])
    if op == "!=":
        return not _eq(args[0], args[1])
    if op == "in":
        return args[0] in args[1]
    try:
        if op in ("<", "<=") and len(args) == 3:  # between
            lo, x, hi = args
            return lo < x < hi if op == "<" else lo <= x <= hi
        a, b = args[0], args[1]
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    except TypeError:
        return False  # e.g. comparing a string with a number: the condition is not met


def evaluate(logic: Any, data: dict[str, Any]) -> Any:
    """Evaluate a rule; the result is True / False / a value / UNKNOWN."""
    if isinstance(logic, list):
        return [evaluate(x, data) for x in logic]
    if not isinstance(logic, dict) or len(logic) != 1:
        return logic  # literal
    op, raw = next(iter(logic.items()))
    if op not in OPERATORS:
        raise ValueError(f"unsupported JSON Logic operator: {op}")
    args = raw if isinstance(raw, list) else [raw]

    if op == "var":
        name = evaluate(args[0], data)
        if name in data and data[name] is not None:
            return data[name]
        return evaluate(args[1], data) if len(args) > 1 else UNKNOWN
    if op == "and":
        results = [_truthy(evaluate(a, data)) for a in args]
        if any(r is False for r in results):
            return False
        return UNKNOWN if any(r is UNKNOWN for r in results) else True
    if op == "or":
        results = [_truthy(evaluate(a, data)) for a in args]
        if any(r is True for r in results):
            return True
        return UNKNOWN if any(r is UNKNOWN for r in results) else False
    if op in ("!", "!!"):
        t = _truthy(evaluate(args[0], data))
        if t is UNKNOWN:
            return UNKNOWN
        return (not t) if op == "!" else t
    if op == "if":
        # [cond, then, cond2, then2, ..., else]
        for i in range(0, len(args) - 1, 2):
            c = _truthy(evaluate(args[i], data))
            if c is UNKNOWN:
                return UNKNOWN
            if c:
                return evaluate(args[i + 1], data)
        return evaluate(args[-1], data) if len(args) % 2 else None
    return _compare(op, [evaluate(a, data) for a in args])


def validate(logic: Any) -> None:
    """Raise ValueError if the rule uses an operator we don't support."""
    if isinstance(logic, list):
        for x in logic:
            validate(x)
    elif isinstance(logic, dict):
        if len(logic) != 1:
            raise ValueError(f"a JSON Logic node has exactly one operator: {logic}")
        op, raw = next(iter(logic.items()))
        if op not in OPERATORS:
            raise ValueError(f"unsupported JSON Logic operator: {op}")
        validate(raw)


# --- explanations ---------------------------------------------------------------------


@dataclass(frozen=True)
class Clause:
    """One condition of a rule, for the WHY box: field, op, limit vs the citizen's value."""

    field: str
    op: str
    limit: Any
    value: Any  # None if unknown
    result: bool | None  # None if unknown


def clauses(rule: Any, data: dict[str, Any]) -> list[Clause]:
    """The leaf conditions `{op: [{"var": field}, literal]}` of a rule, evaluated."""
    out: list[Clause] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict) or len(node) != 1:
            return
        op, raw = next(iter(node.items()))
        args = raw if isinstance(raw, list) else [raw]
        if op in COMPARISONS and len(args) == 2 and isinstance(args[0], dict) and "var" in args[0]:
            field = args[0]["var"]
            field = field[0] if isinstance(field, list) else field
            r = evaluate(node, data)
            out.append(Clause(field, op, args[1], data.get(field), None if r is UNKNOWN else bool(r)))
            return
        for a in args:
            walk(a)

    walk(rule)
    return out


# --- schemes ----------------------------------------------------------------------------

REQUIRED_KEYS = ("scheme_id", "title", "rule", "required_fields", "documents", "source_url",
                 "effective_date")

Status = Literal["eligible", "not_eligible", "unknown"]


@lru_cache(maxsize=1)
def load_schemes(rules_dir: Path = RULES_DIR) -> dict[str, dict[str, Any]]:
    """All rules/*.json, validated, keyed by scheme_id (file order = name order)."""
    schemes: dict[str, dict[str, Any]] = {}
    for path in sorted(rules_dir.glob("*.json")):
        scheme = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_KEYS if k not in scheme]
        if missing:
            raise ValueError(f"{path.name}: missing keys {missing}")
        validate(scheme["rule"])
        for d in scheme["documents"]:
            if d.get("when") is not None:
                validate(d["when"])
        schemes[scheme["scheme_id"]] = scheme
    return schemes


def status(scheme: dict[str, Any], profile: dict[str, Any]) -> Status:
    r = evaluate(scheme["rule"], profile)
    if r is UNKNOWN:
        return "unknown"
    return "eligible" if _truthy(r) else "not_eligible"


def missing_fields(scheme: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    return [f for f in scheme["required_fields"] if profile.get(f) is None]


def title(scheme: dict[str, Any], lang: str) -> str:
    return scheme.get("titles", {}).get(lang) or scheme["title"]


def source_name(scheme: dict[str, Any], lang: str) -> str:
    names = scheme.get("source_name") or {}
    return names.get(lang) or names.get("en") or scheme["source_url"]
