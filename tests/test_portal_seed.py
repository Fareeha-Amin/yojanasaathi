"""rules/*.json must agree with the mock portal's scheme seed (the source of truth).

Checked against the snapshot tests/fixtures/portal_seed.json (portal commit 6e6e24d), and
also against a live checkout when MOCK_PORTAL_REPO points to one:
    $env:MOCK_PORTAL_REPO="C:\\path\\to\\mock"; .\\.venv\\Scripts\\python.exe -m pytest tests/test_portal_seed.py -q
Refresh the snapshot after a portal seed change: python -m agent.portal_seed <seed path>.
"""

import json
import os
from pathlib import Path

import pytest

from agent import portal_seed, rules

FIXTURE = Path(__file__).parent / "fixtures" / "portal_seed.json"


def _seeds() -> list[tuple[str, list[dict]]]:
    out = [("snapshot 6e6e24d", json.loads(FIXTURE.read_text(encoding="utf-8"))["schemes"])]
    repo = os.getenv("MOCK_PORTAL_REPO")
    if repo:
        out.append(("live " + repo, portal_seed.trim(portal_seed.parse(Path(repo) / portal_seed.SEED_PATH))))
    return out


SEEDS = _seeds()
SCHEMES = rules.load_schemes()


def expected_rule(er: dict) -> dict:
    conds = []
    if "min_age" in er:
        conds.append({">=": [{"var": "age"}, er["min_age"]]})
    if "max_income" in er:
        conds.append({"<=": [{"var": "annual_income"}, er["max_income"]]})
    assert set(er) <= {"min_age", "max_income"}, f"new portal rule key: {er}"
    return {"and": conds} if len(conds) > 1 else conds[0]


@pytest.mark.parametrize("name, seed", SEEDS, ids=[n for n, _ in SEEDS])
def test_same_scheme_ids(name, seed):
    assert sorted(SCHEMES) == sorted(s["id"] for s in seed)


@pytest.mark.parametrize("name, seed", SEEDS, ids=[n for n, _ in SEEDS])
def test_each_rules_file_matches_the_seed(name, seed):
    for s in seed:
        ours = SCHEMES[s["id"]]
        assert ours["portal_rules"] == s["eligibility_rules"], s["id"]
        assert ours["rule"] == expected_rule(s["eligibility_rules"]), s["id"]
        assert ours["titles"] == s["title"], s["id"]
        assert ours["title"] == s["title"]["en"], s["id"]
        for lang in ("en", "kn", "hi"):
            assert [d["label"][lang] for d in ours["documents"]] == s["documents"][lang], (s["id"], lang)
        assert ours["application_fields"] == s["application_fields"], s["id"]


def test_all_demo_with_portal_source_url():
    for sid, s in SCHEMES.items():
        assert s["verification"].startswith("DEMO"), sid
        assert s["source_url"].endswith(f"/schemes/{sid}"), sid
        assert s["effective_date"]


def test_snapshot_records_its_source():
    assert "6e6e24d" in json.loads(FIXTURE.read_text(encoding="utf-8"))["_source"]
