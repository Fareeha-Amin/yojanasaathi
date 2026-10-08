"""AgentBridge in a real Pipecat pipeline with a fake /turn.

Run with the voice venv:  .\\voice\\.venv\\Scripts\\python.exe -m pytest voice/tests -q
"""

import asyncio

from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transcriptions.language import Language

from voice.bridge import AgentBridge
from voice.lang import AGENT_UNREACHABLE

KN_REPLY = "ನಿಮ್ಮ ಅರ್ಜಿ ಸಿದ್ಧವಾಗಿದೆ. ಸಲ್ಲಿಸಲೇ?"


class FakeAgent:
    def __init__(self, reply: str = KN_REPLY, pause: dict | None = None,
                 delay: float = 0.0, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.reply, self.pause, self.delay, self.fail = reply, pause, delay, fail

    async def turn(self, case_id: str, text: str, lang: str | None) -> dict:
        self.calls.append((case_id, text, lang))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("agent down")
        return {"reply": self.reply, "pause": self.pause}


def said(frames) -> list[tuple[str, str]]:
    """(language, text) pairs the TTS would speak, in order."""
    out, lang = [], None
    for f in frames:
        if isinstance(f, TTSUpdateSettingsFrame):
            lang = f.delta.language
        elif isinstance(f, TTSSpeakFrame):
            out.append((lang, f.text))
    return out


def transcript(text: str, language: Language | None) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="", timestamp="", language=language, finalized=True)


def end_of_turn() -> list:
    # UserStoppedSpeakingFrame is a SystemFrame and overtakes queued data frames. In
    # the bot it can't: the turn processor only ends a turn after the transcript has
    # passed through the bridge. The sleep reproduces that order here.
    return [SleepFrame(sleep=0.05), UserStoppedSpeakingFrame()]


def run(agent: FakeAgent, *frames):
    bridge = AgentBridge(case_id="case-1", send_turn=agent.turn)
    down, _ = asyncio.run(run_test(bridge, frames_to_send=[*frames, SleepFrame(sleep=0.3)]))
    return down


def test_one_turn_one_call_with_detected_lang_and_spoken_reply():
    agent = FakeAgent(pause={"type": "confirm", "preview": {}})
    down = run(
        agent,
        UserStartedSpeakingFrame(),
        transcript("ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ.", Language.KN_IN),
        transcript("ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", Language.KN_IN),
        *end_of_turn(),
    )
    assert agent.calls == [("case-1", "ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ. ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?", "kn")]
    assert said(down) == [(Language.KN_IN, KN_REPLY)]
    [msg] = [f for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert msg.data["pause"] == {"type": "confirm", "preview": {}} and msg.data["lang"] == "kn"


def test_unsupported_or_missing_language_is_not_sent():
    agent = FakeAgent(reply="Okay.")
    run(agent, transcript("vanakkam", Language.TA_IN), *end_of_turn())
    assert agent.calls == [("case-1", "vanakkam", None)]


def test_short_turn_sends_no_lang_but_fallback_uses_heard_language():
    agent = FakeAgent(fail=True)
    down = run(agent, transcript("ಹೌದು.", Language.KN_IN), *end_of_turn())
    assert agent.calls == [("case-1", "ಹೌದು.", None)]  # case keeps its language
    assert said(down) == [(Language.KN_IN, AGENT_UNREACHABLE["kn"])]


def test_reply_language_follows_reply_script_not_citizen():
    agent = FakeAgent(reply="आपको पेंशन मिल सकती है।")
    down = run(agent, transcript("I need a pension", Language.EN_IN), *end_of_turn())
    assert said(down) == [(Language.HI_IN, "आपको पेंशन मिल सकती है।")]


def test_no_transcript_no_call():
    agent = FakeAgent()
    down = run(agent, UserStartedSpeakingFrame(), *end_of_turn())
    assert agent.calls == [] and said(down) == []


def test_barge_in_drops_stale_reply():
    agent = FakeAgent(delay=0.15)
    down = run(
        agent,
        transcript("ನನಗೆ ಪಿಂಚಣಿ ಬೇಕು", Language.KN_IN),
        *end_of_turn(),
        UserStartedSpeakingFrame(),  # citizen speaks again before the reply arrives
    )
    assert len(agent.calls) == 1
    assert said(down) == []


def test_agent_down_speaks_fallback_in_turn_language():
    agent = FakeAgent(fail=True)
    down = run(agent, transcript("मुझे पेंशन चाहिए", Language.HI_IN), *end_of_turn())
    assert said(down) == [(Language.HI_IN, AGENT_UNREACHABLE["hi"])]
