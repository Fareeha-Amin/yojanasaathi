"""YojanaSaathi voice bot: Pipecat + Sarvam, talking to the agent only through /turn.

  browser mic -> SmallWebRTC -> Silero VAD -> Sarvam Saaras STT (auto language)
    -> AgentBridge (POST /turn/{case_id} with "lang") -> UserTurnProcessor (turn end, barge-in)
    -> Sarvam Bulbul TTS (language from the reply's script) -> browser speaker

Run from the repo root (agent must be running on AGENT_URL):
  .\\voice\\.venv\\Scripts\\python.exe -m voice.bot -t webrtc     # open http://localhost:7860/client

Phase 7 reuses this file for the phone line via the same runner (`-t twilio`).
"""

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from voice import config
from voice.agent_client import AgentClient
from voice.bridge import AgentBridge
from voice.lang import GREETING, GREETING_SUBTITLE

# Silence after speech before the turn is sent. Pipecat's default is 0.6 s; first-time
# and elderly speakers pause mid-sentence, so give them a little longer.
USER_SPEECH_TIMEOUT_S = 0.8

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def make_stt() -> SarvamSTTService:
    # saaras:v4 with language auto-detect. Sarvam's default minimum speech length drops
    # a short "ಹೌದು" (~0.4 s, measured with voice.smoke); 1 keeps it. Silero still decides
    # what counts as speech on our side.
    return SarvamSTTService(
        api_key=config.SARVAM_API_KEY,
        settings=SarvamSTTService.Settings(min_speech_frames=1, first_turn_min_speech_frames=1),
    )


def case_id_for(runner_args: RunnerArguments) -> str:
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    return str(body.get("case_id") or config.VOICE_CASE_ID)


async def bot(runner_args: RunnerArguments) -> None:
    if not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not set in .env")

    case_id = case_id_for(runner_args)
    transport = await create_transport(runner_args, transport_params)
    agent = AgentClient(config.AGENT_URL)

    stt = make_stt()
    tts = SarvamTTSService(api_key=config.SARVAM_API_KEY)  # bulbul:v3; language set per reply
    bridge = AgentBridge(case_id=case_id, send_turn=agent.turn)
    turns = UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            # VAD start (interrupts the bot = barge-in) / VAD silence + final transcript.
            # Not the default smart-turn model: language-agnostic and no extra download.
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=USER_SPEECH_TIMEOUT_S)],
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        VADProcessor(vad_analyzer=SileroVADAnalyzer()),
        stt,
        bridge,
        turns,
        tts,
        transport.output(),
    ])
    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"client connected, case {case_id}, agent {config.AGENT_URL}")

    # Greet once the client's RTVI layer is ready (after "client-ready"), so the "say"
    # message reaches the web app's transcript as well as the speaker. (PipelineWorker's
    # own on_client_ready handler sends bot-ready; handlers are additive.)
    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        await bridge.say(GREETING, show=True, subtitle=GREETING_SUBTITLE)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"client disconnected, case {case_id}")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        await agent.aclose()


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
