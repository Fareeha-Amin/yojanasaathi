"""The web app's component-test fixtures must have the shape GET /summary really returns."""

import json

import pytest

from agent import llm as llm_mod
from agent import portal
from agent.main import app
from tests.fake_driver import FakeDriver
from tests.helpers import CaseClient, FakeLLM
from tests.web_fixtures import FIXTURES, STAGES, build, shape

client = CaseClient(app)


@pytest.fixture(scope="module")
def fresh() -> dict[str, dict]:
    # Module scope runs before the per-test FakeLLM / FakeDriver: install them here, never
    # the real LLM or browser.
    previous = portal._driver
    llm_mod.set_llm(FakeLLM())
    portal.set_driver(FakeDriver())
    try:
        return build(client)
    finally:
        llm_mod.set_llm(None)
        portal.set_driver(previous)


def test_fixtures_match_the_summary_endpoint(fresh, request):
    if request.config.getoption("--regen-web-fixtures"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for stage, summary in fresh.items():
            (FIXTURES / f"{stage}.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for stage in STAGES:
        path = FIXTURES / f"{stage}.json"
        assert path.exists(), f"missing {path}; run with --regen-web-fixtures"
        committed = json.loads(path.read_text(encoding="utf-8"))
        live = json.loads(json.dumps(fresh[stage], default=str))
        assert shape(committed) == shape(live), f"{stage}.json is stale; run with --regen-web-fixtures"


def test_stages_cover_the_golden_path(fresh):
    assert fresh["interview"]["asking"]["field"] == "annual_income"
    assert [s["status"] for s in fresh["eligible"]["schemes"]] == ["eligible"] * 4
    assert fresh["otp"]["pause"]["type"] == "otp" and fresh["otp"]["progress"]["steps"][0]["key"] == "login"
    assert fresh["review"]["review"]["scheme_id"] == "pension-001"
    assert fresh["review"]["review"]["screenshots"]
    assert fresh["submitted"]["applications"][0]["app_id"].startswith("YJS-")
