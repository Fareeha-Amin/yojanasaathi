"""Live checks against the configured LLM (Ollama qwen3:8b). Opt-in, needs the server:
    $env:RUN_LIVE_LLM="1"; .\\.venv\\Scripts\\python.exe -m pytest tests/test_llm_live.py -q -s
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from agent import config
from agent import llm as llm_mod

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1")


@pytest.fixture(scope="module")
def live():
    if config.LLM_PROVIDER == "none":
        pytest.skip("LLM_PROVIDER=none")
    real = llm_mod.ChatLLM(*llm_mod.make_chat_model())
    real.warmup()
    assert real.state == "ready"
    return real


@pytest.mark.parametrize("msg, asking, expect", [
    ("ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", None, {"age": 62, "topic": "pension"}),
    ("मैं 62 साल की हूँ, क्या मुझे पेंशन मिल सकती है?", None, {"age": 62, "topic": "pension"}),
    ("I need help paying for my hospital treatment", None, {"topic": "health"}),
    ("सामाजिक सुरक्षा वाली पेंशन चाहिए", "choose", {"scheme": "pension-002"}),
    ("I live in Tumkur", None, {"district": "Tumakuru"}),
    ("why do you need my income?", "annual_income", {"intent": "question"}),
])
def test_extraction(live, msg, asking, expect):
    t0 = time.perf_counter()
    out = live.extract(msg, asking)
    took = time.perf_counter() - t0
    print(f"\n{took:.2f}s {msg!r} -> {out}")
    assert out is not None
    got = out.model_dump()
    if "district" in expect:
        from agent.districts import normalize_district

        got["district"] = normalize_district(got["district"])
    for k, v in expect.items():
        assert got[k] == v, (k, got)
    assert took < 5, "warm extraction should take ~1 s"


def test_kannada_golden_path_with_live_llm(live, fake_llm):
    llm_mod.set_llm(live)
    from agent.main import app
    from agent.replies import t

    c = TestClient(app)
    case = f"live-{uuid.uuid4().hex[:6]}"
    t0 = time.perf_counter()
    r1 = c.post(f"/turn/{case}", json={"text": "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "lang": "kn"}).json()
    t1 = time.perf_counter()
    r2 = c.post(f"/turn/{case}", json={"text": "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ"}).json()
    t2 = time.perf_counter()
    print(f"\nturn1 {t1 - t0:.2f}s: {r1['reply']}\nturn2 {t2 - t1:.2f}s: {r2['reply']}")
    assert r1["reply"] == t("ask_annual_income", "kn")
    assert "4 ಯೋಜನೆಗಳಿಗೆ ಅರ್ಹರು" in r2["reply"] and "₹1,20,000" in r2["reply"]
