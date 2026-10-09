"""AgentBridge: turns one finished user turn into one POST /turn, and speaks the reply.

Pipeline position (see bot.py):

  transport.in -> VAD -> Sarvam STT -> AgentBridge -> UserTurnProcessor -> Sarvam TTS -> transport.out

Final transcripts pass through the bridge on their way down, so they are buffered
before the turn processor can end the turn. The turn processor broadcasts
UserStarted/UserStoppedSpeakingFrame in both directions; the upstream copy tells the
bridge that a turn started (barge-in) or ended (send it).

Barge-in: if the citizen starts speaking again while /turn is still in flight, that
reply is dropped. The agent has already processed the turn, so the next turn carries
on from the newer state; the citizen only hears the latest reply.

Web app (Phase 5): every turn goes to the client as an RTVI server message
{"type": "turn", ...} with reply, subtitle (English), pause and ui. The web app's own turns
(typed text, buttons, review edits) go to /turn over HTTP; it then sends an RTVI client
message {"t": "speak", "d": {"text": ...}} so Bulbul reads that reply (and "read aloud" /
"replay") in the same voice. "speak" only speaks: it never calls /turn.
"""

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIClientMessageFrame, RTVIServerMessageFrame
from pipecat.services.settings import TTSSettings
from pipecat.transcriptions.language import Language

from voice.lang import (AGENT_UNREACHABLE, TurnLang, mask_for_log, tts_language, turn_lang,
                        turn_lang_for)

SendTurn = Callable[[str, str, TurnLang | None], Awaitable[dict]]

SPEAK_MAX_CHARS = 600  # "speak" from the web app: a reply or a read-back, not a document


class AgentBridge(FrameProcessor):
    def __init__(self, *, case_id: str, send_turn: SendTurn, **kwargs) -> None:
        super().__init__(**kwargs)
        self._case_id = case_id
        self._send_turn = send_turn
        self._parts: list[str] = []
        self._lang: TurnLang | None = None  # language Saaras detected in the turn being heard
        self._last_lang: TurnLang | None = None  # last lang sent to /turn; for the fallback reply
        self._turn_no = 0  # bumped when the citizen starts speaking; older replies are stale
        self._tasks: set[asyncio.Task] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                self._parts.append(frame.text.strip())
            lang = turn_lang(getattr(frame.language, "value", frame.language))
            if lang:
                self._lang = lang
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._turn_no += 1
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._end_turn()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._cancel_tasks()
        elif isinstance(frame, RTVIClientMessageFrame) and frame.type == "speak":
            data = frame.data if isinstance(frame.data, dict) else {}
            text = str(data.get("text") or "").strip()[:SPEAK_MAX_CHARS]
            if text:
                await self.say(text)

        await self.push_frame(frame, direction)

    async def say(self, text: str) -> None:
        """Speak text in the language its script calls for."""
        language = Language(tts_language(text))
        await self.push_frame(TTSUpdateSettingsFrame(delta=TTSSettings(language=language)))
        await self.push_frame(TTSSpeakFrame(text, append_to_context=False))

    def _end_turn(self) -> None:
        text = " ".join(self._parts)
        heard = self._lang
        self._parts, self._lang = [], None
        if not text:
            return
        lang = turn_lang_for(text, heard)  # None for short turns: case keeps its language
        if lang:
            self._last_lang = lang
        task = self.create_task(self._answer(text, lang, heard, self._turn_no), "agent_turn")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(
        self, text: str, lang: TurnLang | None, heard: TurnLang | None, turn_no: int
    ) -> None:
        shown = mask_for_log(text)  # Aadhaar: last 4 digits only, in logs and on screen
        logger.info(f"case {self._case_id} <- [{lang or '-'}, heard {heard or '?'}] {shown}")
        try:
            out = await self._send_turn(self._case_id, text, lang)
            reply, pause, ui = out["reply"], out.get("pause"), out.get("ui")
            subtitle = out.get("subtitle")
        except Exception as e:
            logger.error(f"/turn failed for case {self._case_id}: {e!r}")
            reply, pause, ui = AGENT_UNREACHABLE[lang or self._last_lang or heard or "kn"], None, None
            subtitle = None

        if turn_no != self._turn_no:
            logger.info(f"case {self._case_id}: dropped reply, citizen spoke again: {reply!r}")
            return
        logger.info(f"case {self._case_id} -> {reply!r} pause={pause}")

        # For the client UI (review screen, OTP box); the web app reads it over RTVI.
        await self.push_frame(
            RTVIServerMessageFrame(
                data={"type": "turn", "case_id": self._case_id, "text": shown,
                      "lang": lang, "reply": reply, "subtitle": subtitle, "pause": pause, "ui": ui}
            )
        )
        if reply:
            await self.say(reply)

    async def _cancel_tasks(self) -> None:
        for task in list(self._tasks):
            await self.cancel_task(task)
        self._tasks.clear()
