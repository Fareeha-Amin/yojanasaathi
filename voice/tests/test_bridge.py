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
from pipecat.processors.frameworks.rtvi import RTVIClientMessageFrame, RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transcriptions.language import Language

from voice.bridge import AgentBridge
from voice import bridge as bridge_mod
from voice.lang import AGENT_UNREACHABLE, FILLER

KN_REPLY = "ನಿಮ್ಮ ಅರ್ಜಿ ಸಿದ್ಧವಾಗಿದೆ. ಸಲ್ಲಿಸಲೇ?"


class FakeAgent:
    def __init__(self, reply: str = KN_REPLY, pause: dict | None = None,
                 delay: float = 0.0, fail: bool = False, ui: dict | None = None,
                 subtitle: str | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.reply, self.pause, self.delay, self.fail, self.ui = reply, pause, delay, fail, ui
        self.subtitle = subtitle

    async def turn(self, case_id: str, text: str, lang: str | None) -> dict:
        self.calls.append((case_id, text, lang))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("agent down")
        return {"reply": self.reply, "pause": self.pause, "ui": self.ui, "subtitle": self.subtitle}


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
    agent = FakeAgent(pause={"type": "confirm", "preview": {}}, ui={"type": "eligibility", "schemes": []})
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
    assert msg.data["ui"] == {"type": "eligibility", "schemes": []}  # screen payload passed through


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


def test_subtitle_passed_to_the_web_app():
    agent = FakeAgent(subtitle="Your application is ready. Shall I submit?")
    down = run(agent, transcript("ನನಗೆ ಪಿಂಚಣಿ ಬೇಕು ದಯವಿಟ್ಟು", Language.KN_IN), *end_of_turn())
    [msg] = [f for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert msg.data["subtitle"] == "Your application is ready. Shall I submit?"
    assert msg.data["reply"] == KN_REPLY


def test_speak_message_from_web_app_is_spoken_without_a_turn():
    agent = FakeAgent()
    down = run(agent, RTVIClientMessageFrame(msg_id="1", type="speak", data={"text": KN_REPLY}))
    assert agent.calls == []  # speak never calls /turn
    assert said(down) == [(Language.KN_IN, KN_REPLY)]


def test_speak_ignores_empty_other_types_and_caps_length():
    agent = FakeAgent()
    down = run(agent,
               RTVIClientMessageFrame(msg_id="1", type="speak", data={"text": "  "}),
               RTVIClientMessageFrame(msg_id="2", type="other", data={"text": "hello"}),
               RTVIClientMessageFrame(msg_id="3", type="speak", data="not a dict"),
               RTVIClientMessageFrame(msg_id="4", type="speak", data={"text": "a" * 1000}))
    assert said(down) == [(Language.EN_IN, "a" * 600)]


def test_greeting_is_spoken_and_shown_in_the_web_app():
    from voice.lang import GREETING, GREETING_SUBTITLE

    agent = FakeAgent()
    bridge = AgentBridge(case_id="case-1", send_turn=agent.turn)

    async def go():
        async def greet():
            await asyncio.sleep(0.05)
            await bridge.say(GREETING, show=True, subtitle=GREETING_SUBTITLE)
        task = asyncio.create_task(greet())
        down, _ = await run_test(bridge, frames_to_send=[SleepFrame(sleep=0.3)])
        await task
        return down

    down = asyncio.run(go())
    [msg] = [f for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert msg.data == {"type": "say", "case_id": "case-1", "text": GREETING, "subtitle": GREETING_SUBTITLE}
    assert said(down) == [(Language.KN_IN, GREETING)]
    assert agent.calls == []


def test_plain_say_sends_no_ui_message():
    agent = FakeAgent()
    down = run(agent, RTVIClientMessageFrame(msg_id="1", type="speak", data={"text": KN_REPLY}))
    assert [f for f in down if isinstance(f, RTVIServerMessageFrame)] == []


def test_slow_turn_speaks_one_filler_then_the_reply(monkeypatch):
    monkeypatch.setattr(bridge_mod, "FILLER_AFTER_S", 0.05)
    agent = FakeAgent(reply="Done.", delay=0.15)
    down = run(agent, transcript("I need a pension please now", Language.EN_IN), *end_of_turn())
    assert [t for _, t in said(down)] == [FILLER["en"], "Done."]


def test_fast_turn_has_no_filler(monkeypatch):
    monkeypatch.setattr(bridge_mod, "FILLER_AFTER_S", 0.2)
    agent = FakeAgent(reply="Done.", delay=0.0)
    down = run(agent, transcript("I need a pension please now", Language.EN_IN), *end_of_turn())
    assert [t for _, t in said(down)] == ["Done."]
