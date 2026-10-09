"""Secrets, environments and local data must never be committed."""

import shutil
import subprocess

import pytest

from agent.config import ROOT

MUST_IGNORE = [
    ".env",
    ".env.local",
    ".venv/Lib/site.py",
    "web/node_modules/react/index.js",
    "agent/__pycache__/main.cpython-312.pyc",
    "screenshots/step-1.png",
    "data/vault/file.bin",
    "data/vault/0123abcd.ysv",
]
MUST_TRACK = [".env.example", "agent/main.py", "agent/schema.sql", "rules/pension-001.json"]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git repo"
)


def _ignored(path: str) -> bool:
    # --no-index: check the rules even for paths that are already tracked
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    return r.returncode == 0


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_ignored(path):
    assert _ignored(path)


@pytest.mark.parametrize("path", MUST_TRACK)
def test_not_ignored(path):
    assert not _ignored(path)
