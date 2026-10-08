"""The LLM understands and speaks; code decides and acts.

Two jobs only:
1. extract(): message -> `Extraction` (JSON-schema structured output, temperature 0,
   few-shot kn/hi/en). Used for facts the deterministic parsers (agent/facts.py) did not
   find and for intent. Numbers from here are a flagged fallback (see agent/graph.py).
2. answer(): a short free-form reply to a general question. Never eligibility, never the
   key replies (those are templates, agent/replies.py).

Provider is a config change (.env LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL),
see make_chat_model(). Every call can fail or time out; callers then carry on with the
deterministic parsers, so the agent never depends on the LLM being up.
"""

import json
import logging
import re
import threading
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from agent import config

log = logging.getLogger("yojanasaathi.llm")

LANG_NAMES = {"kn": "Kannada", "hi": "Hindi", "en": "English"}


class Extraction(BaseModel):
    """Facts the citizen clearly stated in this one message. null = not stated."""

    intent: Literal["info", "question", "proceed", "status", "other"] = Field(
        description="info: gives facts or asks if eligible; question: asks something else; "
        "proceed: wants to apply or fill the form; status: asks about an application; other")
    age: int | None = Field(None, description="age in years")
    annual_income: int | None = Field(None, description="family income per year in rupees")
    gender: Literal["female", "male"] | None = None
    district: str | None = Field(None, description="district name in English")
    category: Literal["SC", "ST", "OBC", "General"] | None = None
    is_student: bool | None = Field(None, description="studying in class 11 or above now")
    owns_farmland: bool | None = Field(None, description="farm land in their own name")
    pays_income_tax: bool | None = Field(None, description="they or their spouse pay income tax")
    govt_job_or_big_pension: bool | None = Field(
        None, description="they or their spouse have a government job or a pension of Rs 10,000/month or more")
    is_family_head: bool | None = Field(None, description="head of the family on the ration card")
    topic: Literal["pension", "farmer", "scholarship", "women"] | None = Field(
        None, description="kind of scheme they ask about")


SYSTEM_EXTRACT = """You extract facts from a citizen's message for an Indian welfare-scheme assistant.
Messages are in Kannada, Hindi, English or a mix. Return only facts the citizen clearly stated
in THIS message; use null for everything else. Never guess and never decide eligibility.
Numbers are integers: 1 lakh = ಲಕ್ಷ = लाख = 100000; thousand = ಸಾವಿರ = हज़ार = 1000.
annual_income is the family's income per year (a monthly income times 12).
district is the district name in English. A yes/no field is true or false only if the
citizen answered it. If "Asked:" is given, a short reply answers that question.
intent: info (gives facts or asks if eligible), question (asks something else, such as what a
scheme is or why a detail is needed), proceed (wants to apply, fill the form or continue),
status (asks about an application's status), other."""

ASKED = {
    "age": "How old are you?",
    "annual_income": "What is your family's total income in one year?",
    "gender": "Are you a woman or a man?",
    "category": "Which category: SC, ST, OBC or general?",
    "district": "Which district do you live in?",
    "is_student": "Are you studying in class 11 or above?",
    "owns_farmland": "Is there farm land in your own name?",
    "pays_income_tax": "Do you or your spouse pay income tax?",
    "govt_job_or_big_pension": "Do you or your spouse have a government job or a pension of Rs 10,000 a month or more?",
    "is_family_head": "Are you the head of the family on your ration card?",
    "proceed": "Shall I start filling the application form?",
}


def _user(msg: str, asking: str | None = None) -> str:
    asked = f"Asked: {ASKED[asking]}\n" if asking in ASKED else ""
    return f"{asked}Citizen: {msg}"


def _ex(**kw: Any) -> str:
    return Extraction(**kw).model_dump_json()


FEW_SHOT: list[tuple[str, str]] = [
    (_user("I'm 62 and my family earns about 1 lakh 20 thousand a year. Can I get a pension?"),
     _ex(intent="info", age=62, annual_income=120000, topic="pension")),
    (_user("ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"),
     _ex(intent="info", age=62, topic="pension")),
    (_user("ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ ರೂಪಾಯಿ", "annual_income"),
     _ex(intent="info", annual_income=120000)),
    (_user("मैं किसान हूँ, मेरे नाम पर दो एकड़ ज़मीन है। कोई योजना है?"),
     _ex(intent="info", owns_farmland=True, topic="farmer")),
    (_user("नहीं, हम टैक्स नहीं भरते", "pays_income_tax"),
     _ex(intent="info", pays_income_tax=False)),
    (_user("ನಾನು ತುಮಕೂರಿನ ಮಹಿಳೆ, ಮನೆಯ ಯಜಮಾನಿ ನಾನೇ"),
     _ex(intent="info", gender="female", district="Tumakuru", is_family_head=True)),
    (_user("Why do you need my income?", "annual_income"),
     _ex(intent="question")),
    (_user("हाँ, फ़ॉर्म भर दीजिए", "proceed"),
     _ex(intent="proceed")),
    (_user("ನನ್ನ ಅರ್ಜಿ ಏನಾಯಿತು?"),
     _ex(intent="status")),
]

SYSTEM_ANSWER = """You are YojanaSaathi, a voice assistant that helps Indian citizens with welfare schemes.
Reply in {language} only, in at most two short sentences of plain text that will be spoken
aloud: no lists, no markdown, no emojis. Never say whether the citizen is or is not eligible:
eligibility is decided by official rules, so say you will check it with their details.
Never ask for OTPs, passwords or a full Aadhaar number. Use only the facts below; if you do
not know, say so.
Schemes:
{kb}
Known about the citizen: {profile}"""

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLM(Protocol):
    state: str  # off | cold | warming | ready | error

    def extract(self, msg: str, asking: str | None = None) -> Extraction | None: ...

    def answer(self, msg: str, lang: str, kb: str, profile: dict[str, Any]) -> str | None: ...

    def warmup(self) -> None: ...


class NullLLM:
    """LLM_PROVIDER=none, or a provider that failed to load: deterministic parsers only."""

    def __init__(self, state: str = "off"):
        self.state = state

    def extract(self, msg: str, asking: str | None = None) -> Extraction | None:
        return None

    def answer(self, msg: str, lang: str, kb: str, profile: dict[str, Any]) -> str | None:
        return None

    def warmup(self) -> None:
        pass


def make_chat_model():
    """(LangChain chat model, structured-output method) for the configured provider.

    Switching provider is a .env change. Only langchain-ollama is installed (team decision
    2); for openai / anthropic, pip install the pinned package noted in
    agent/requirements.txt once, then set LLM_PROVIDER. Those two branches are untested
    until their package is installed: check the installed API first.
    """
    p = config.LLM_PROVIDER
    if p == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=config.LLM_MODEL or "qwen3:8b",
            base_url=config.OLLAMA_BASE_URL,
            temperature=0,
            reasoning=False,  # qwen3 thinking off: sends think=false (latency)
            keep_alive=-1,  # keep the model loaded between turns
            client_kwargs={"timeout": config.LLM_TIMEOUT},
        ), "json_schema"
    if p == "openai":  # OpenAI, or any OpenAI-compatible API through LLM_BASE_URL
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=config.LLM_MODEL, api_key=config.LLM_API_KEY,
                          base_url=config.LLM_BASE_URL, temperature=0,
                          timeout=config.LLM_TIMEOUT), "json_schema"
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=config.LLM_MODEL, api_key=config.LLM_API_KEY,
                             temperature=0, timeout=config.LLM_TIMEOUT), "function_calling"
    raise ValueError(f"unknown LLM_PROVIDER {p!r} (ollama | openai | anthropic | none)")


class ChatLLM:
    def __init__(self, model: Any, method: str):
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        self._msgs = (SystemMessage, HumanMessage, AIMessage)
        self.model = model
        self.extractor = model.with_structured_output(Extraction, method=method)
        # Fixed prefix (system + examples) so a local server can reuse its prompt cache.
        self._prefix = [SystemMessage(SYSTEM_EXTRACT)]
        for user, assistant in FEW_SHOT:
            self._prefix += [HumanMessage(user), AIMessage(assistant)]
        self.state = "cold"
        self._last_call = 0.0

    def extract(self, msg: str, asking: str | None = None) -> Extraction | None:
        _, Human, _ = self._msgs
        self._last_call = time.monotonic()
        t0 = time.perf_counter()
        try:
            out = self.extractor.invoke([*self._prefix, Human(_user(msg, asking))])
        except Exception as e:  # timeout, server down, schema violation: fall back
            log.warning("extract failed after %.2fs: %s", time.perf_counter() - t0, e)
            return None
        log.info("extract %.2fs: %s", time.perf_counter() - t0, out)
        return out if isinstance(out, Extraction) else None

    def answer(self, msg: str, lang: str, kb: str, profile: dict[str, Any]) -> str | None:
        System, Human, _ = self._msgs
        system = SYSTEM_ANSWER.format(language=LANG_NAMES.get(lang, "English"), kb=kb,
                                      profile=json.dumps(profile, ensure_ascii=False))
        t0 = time.perf_counter()
        try:
            out = self.model.invoke([System(system), Human(msg)])
        except Exception as e:
            log.warning("answer failed after %.2fs: %s", time.perf_counter() - t0, e)
            return None
        log.info("answer %.2fs", time.perf_counter() - t0)
        text = _THINK.sub("", str(out.content)).strip()
        return text or None

    def warmup(self) -> None:
        """Load the model and its prompt prefix once (first load took ~1 min in tests)."""
        self.state = "warming"
        t0 = time.perf_counter()
        ok = self.extract("hello") is not None
        self.state = "ready" if ok else "error"
        log.info("warmup %s in %.1fs", self.state, time.perf_counter() - t0)
        if ok and config.LLM_KEEPWARM > 0:
            threading.Thread(target=self._keep_warm, name="llm-keepwarm", daemon=True).start()

    def _keep_warm(self) -> None:
        """Measured on the dev laptop (RTX 3070 Ti, Ollama 0.34): after >= 10 s idle a call
        took ~3.3 s instead of ~1.05 s, with Ollama's own prompt/eval timings unchanged
        (the GPU idles at P8; ~2 s is lost outside model compute). A 1-token request on the
        cached prefix every 2 s brought 5 of 6 post-idle turns to 1.1-1.4 s; one was still
        3.4 s, so this reduces the penalty but does not remove it. LLM_KEEPWARM=0: off."""
        _, Human, _ = self._msgs
        ping = [*self._prefix, Human(_user("ok"))]
        while True:
            time.sleep(config.LLM_KEEPWARM)
            if time.monotonic() - self._last_call < config.LLM_KEEPWARM:
                continue
            try:
                self.model.invoke(ping, options={"num_predict": 1, "temperature": 0})
            except Exception as e:
                log.debug("keep-warm ping failed: %s", e)


_llm: LLM | None = None
_lock = threading.Lock()


def get_llm() -> LLM:
    global _llm
    with _lock:
        if _llm is None:
            if config.LLM_PROVIDER == "none":
                _llm = NullLLM()
            else:
                try:
                    _llm = ChatLLM(*make_chat_model())
                except Exception as e:  # missing package, bad config: run without LLM
                    log.error("LLM provider %r unavailable: %s", config.LLM_PROVIDER, e)
                    _llm = NullLLM("error")
        return _llm


def set_llm(llm: LLM | None) -> None:
    """Swap the LLM (tests use a fake; None = rebuild from config on next use)."""
    global _llm
    with _lock:
        _llm = llm


def usable(llm: LLM) -> bool:
    """Skip the LLM while it is still loading: a turn must not wait a minute for it."""
    return llm.state in ("cold", "ready", "error") and not isinstance(llm, NullLLM)
