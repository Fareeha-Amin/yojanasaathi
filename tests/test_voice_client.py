"""The voice bot's /turn client against the real agent app (in-process, no network)."""

import asyncio
import uuid

import httpx

from agent.main import app, graph
from voice.agent_client import AgentClient


def run(coro):
    return asyncio.run(coro)


async def _turns(case_id: str, *turns: tuple[str, str | None]) -> list[dict]:
    client = AgentClient("http://agent", transport=httpx.ASGITransport(app=app))
    try:
        return [await client.turn(case_id, text, lang) for text, lang in turns]
    finally:
        await client.aclose()


def lang_of(case_id: str) -> str | None:
    return graph.get_state({"configurable": {"thread_id": case_id}}).values.get("lang")


def test_voice_turn_sends_detected_lang_and_follows_contract():
    case_id = f"voice-{uuid.uuid4().hex[:8]}"
    [out] = run(_turns(case_id, ("ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ", "kn")))
    assert set(out) == {"reply", "pause", "ui"}
    assert out["pause"] is None and "ಆದಾಯ" in out["reply"]  # asks income, in Kannada
    assert lang_of(case_id) == "kn"


def test_voice_resume_keeps_lang_when_not_detected():
    case_id = f"voice-{uuid.uuid4().hex[:8]}"
    *_, paused, done = run(_turns(case_id, ("मैं 62 साल का हूँ, आमदनी एक लाख है, पेंशन चाहिए", "hi"),
                                  ("वरिष्ठ नागरिक पेंशन", None), ("ಹೌದು", None)))
    assert paused["pause"]["type"] == "confirm"
    assert done["pause"] is None and "DEMO-0001" in done["reply"]  # Kannada yes passed the gate
    assert lang_of(case_id) == "hi"  # no lang sent -> case keeps its language


def test_voice_lang_switch_on_resume():
    case_id = f"voice-{uuid.uuid4().hex[:8]}"
    run(_turns(case_id, ("I need a pension", "en"), ("नहीं", "hi")))
    assert lang_of(case_id) == "hi"


def test_case_id_is_url_encoded():
    # Phase 7 maps caller numbers to cases; "+91..." must survive the path.
    case_id = f"+9198{uuid.uuid4().int % 10**8:08d}"
    run(_turns(case_id, ("hello", "en")))
    assert lang_of(case_id) == "en"
