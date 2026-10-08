"""Voice round-trip without a microphone: live Sarvam + live agent, no browser.

1. Bulbul speaks a test sentence (default: the Kannada demo line) into a buffer.
2. That audio is fed, at real-time pace, through the bot's own chain:
   Silero VAD -> Saaras STT -> AgentBridge -> POST /turn -> UserTurnProcessor.
3. Prints what Saaras heard, the detected language, the agent's reply and pause.

Uses Sarvam credits (a few seconds of TTS + STT). The agent must be running.
  .\\voice\\.venv\\Scripts\\python.exe -m voice.smoke [case_id] ["sentence"]
"""

import asyncio
import base64
import io
import os
import sys
import uuid
import wave

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from sarvamai import AsyncSarvamAI

from voice import config
from voice.agent_client import AgentClient
from voice.bot import USER_SPEECH_TIMEOUT_S, make_stt
from voice.bridge import AgentBridge
from voice.lang import tts_language

RATE = 16000
CHUNK = RATE * 2 // 50  # 20 ms of 16-bit mono
DEMO_LINE = "ನನಗೆ ಅರವತ್ತೆರಡು ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?"


class Tap(FrameProcessor):
    """Records frames of the given types and passes everything through."""

    def __init__(self, *types: type[Frame], **kwargs) -> None:
        super().__init__(**kwargs)
        self.types, self.frames, self.seen = types, [], asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, self.types):
            self.frames.append(frame)
            self.seen.set()
        await self.push_frame(frame, direction)


async def run(worker: PipelineWorker, feed) -> None:
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), feed())


async def synthesize(text: str) -> bytes:
    # Bulbul over REST: a pipeline TTS with no output transport never finishes playback,
    # so its EndFrame never drains. The bot's streaming TTS is exercised by the greeting.
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    out = await client.text_to_speech.convert(
        text=text, target_language_code=tts_language(text), model="bulbul:v3",
        speech_sample_rate=RATE, output_audio_codec="wav",
    )
    pcm = b""
    for clip in out.audios:  # one WAV per text chunk
        with wave.open(io.BytesIO(base64.b64decode(clip))) as w:
            assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
            pcm += w.readframes(w.getnframes())
    return pcm


async def listen(audio: bytes, case_id: str) -> Tap:
    agent = AgentClient(config.AGENT_URL)
    tap = Tap(TranscriptionFrame, RTVIServerMessageFrame, TTSSpeakFrame)
    pipeline = Pipeline([
        VADProcessor(vad_analyzer=SileroVADAnalyzer()),
        make_stt(),
        AgentBridge(case_id=case_id, send_turn=agent.turn),
        UserTurnProcessor(user_turn_strategies=UserTurnStrategies(
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=USER_SPEECH_TIMEOUT_S)],
        )),
        tap,
    ])
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_in_sample_rate=RATE))
    silence = bytes(CHUNK)

    async def feed():
        await asyncio.sleep(1.0)
        for chunk in [silence] * 25 + [audio[i:i + CHUNK] for i in range(0, len(audio), CHUNK)]:
            await worker.queue_frame(InputAudioRawFrame(chunk.ljust(CHUNK, b"\0"), RATE, 1))
            await asyncio.sleep(0.02)
        for _ in range(150):  # up to 3 s of trailing silence while the turn closes
            if any(isinstance(f, TTSSpeakFrame) for f in tap.frames):
                break
            await worker.queue_frame(InputAudioRawFrame(silence, RATE, 1))
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.0)
        await worker.queue_frame(EndFrame())

    try:
        await run(worker, feed)
    finally:
        await agent.aclose()
    return tap


async def main() -> None:
    if not config.SARVAM_API_KEY:
        sys.exit("SARVAM_API_KEY is not set in .env")
    case_id = sys.argv[1] if len(sys.argv) > 1 else f"smoke-{uuid.uuid4().hex[:6]}"
    text = sys.argv[2] if len(sys.argv) > 2 else DEMO_LINE

    logger.remove()
    logger.add(sys.stderr, level=os.getenv("SMOKE_LOG_LEVEL", "WARNING"))

    audio = await synthesize(text)
    print(f"1. Bulbul (REST) spoke: {text!r} -> {len(audio) / (RATE * 2):.1f} s of audio")
    if not audio:
        sys.exit("FAIL: no TTS audio (check SARVAM_API_KEY / credits)")

    tap = await listen(audio, case_id)
    heard = [f for f in tap.frames if isinstance(f, TranscriptionFrame)]
    msgs = [f.data for f in tap.frames if isinstance(f, RTVIServerMessageFrame)]
    for f in heard:
        print(f"2. Saaras heard:  {f.text!r}  language={getattr(f.language, 'value', f.language)}")
    for m in msgs:
        print(f"3. /turn/{m['case_id']} sent lang={m['lang']!r}")
        print(f"   agent reply:  {m['reply']!r}")
        print(f"   pause:        {m['pause']}")
    if not heard or not msgs:
        sys.exit("FAIL: no transcript or no agent reply (is the agent running at AGENT_URL?)")
    print("OK: voice round-trip works")


if __name__ == "__main__":
    asyncio.run(main())
