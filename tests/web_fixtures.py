"""Summaries for the web app's component tests (web/src/test/fixtures/*.json), produced by
the real agent so the screens are tested against the real shape.

Regenerate (needs Postgres; uses the test database like pytest does):
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_web_fixtures.py -q --regen-web-fixtures
tests/test_web_fixtures.py fails when the committed fixtures and GET /summary drift apart.
"""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "web" / "src" / "test" / "fixtures"

KN_AGE = "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"
KN_INCOME = "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ"
KN_PENSION = "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ"

# stage name -> messages sent on a fresh case (lang kn), then GET /summary?lang=kn.
# Phase 4: from the scheme choice on, the case has its documents uploaded; "<FORM>"
# answers the application-form questions (tests/helpers.answer_form), "<OTP>" says the code.
STAGES = {
    "interview": [KN_AGE],
    "eligible": [KN_AGE, KN_INCOME],
    "otp": [KN_AGE, KN_INCOME, KN_PENSION, "<FORM>"],
    "review": [KN_AGE, KN_INCOME, KN_PENSION, "<FORM>", "<OTP>"],
    "submitted": [KN_AGE, KN_INCOME, KN_PENSION, "<FORM>", "<OTP>", "ಹೌದು"],
}


def build(client, case_prefix: str = "fixture") -> dict[str, dict]:
    from tests import helpers

    out = {}
    for stage, msgs in STAGES.items():
        case_id = f"{case_prefix}-{stage}"
        client.delete(f"/cases/{case_id}/data")
        if "<FORM>" in msgs:
            helpers.upload_documents(client, case_id, "pension-001")

        def turn(m: str) -> dict:
            r = client.post(f"/turn/{case_id}", json={"text": m, "lang": "kn"})
            assert r.status_code == 200
            return r.json()

        last: dict = {}
        for m in msgs:
            last = helpers.answer_form(turn, last) if m == "<FORM>" else turn(helpers.OTP if m == "<OTP>" else m)
        summary = client.get(f"/cases/{case_id}/summary", params={"lang": "kn"}).json()
        summary["case_id"] = f"web-{stage}"
        out[stage] = summary
    return out


def shape(obj):
    """Keys and types, recursively (lists: shape of the first item)."""
    if isinstance(obj, dict):
        return {k: shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [shape(obj[0])] if obj else []
    return type(obj).__name__
