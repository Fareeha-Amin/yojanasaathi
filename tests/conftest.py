"""Tests never call a real LLM: every test gets a FakeLLM (deterministic parsers only,
unless a test scripts what the "LLM" returns). Live Ollama checks: tests/test_llm_live.py."""

import pytest

from agent import llm as llm_mod
from agent.llm import Extraction


class FakeLLM:
    state = "ready"

    def __init__(self):
        self.extractions: dict[str, Extraction] = {}  # message -> scripted extraction
        self.answers: dict[str, str] = {}
        self.calls: list[str] = []

    def extract(self, msg, asking=None):
        self.calls.append(msg)
        return self.extractions.get(msg)

    def answer(self, msg, lang, kb, profile):
        return self.answers.get(msg)

    def warmup(self):
        pass


@pytest.fixture(autouse=True)
def fake_llm() -> FakeLLM:
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)
