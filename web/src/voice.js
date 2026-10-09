// Voice in the web app = the Pipecat voice bot (voice/bot.py, port 7860) over SmallWebRTC,
// through Vite's proxy (/voice -> :7860). The bot is the same one as before: Sarvam STT ->
// POST /turn for THIS case (sent in the /start body) -> Bulbul TTS. After every turn it
// sends an RTVI server message {type: "turn", text, lang, reply, subtitle, pause, ui}.
// Web-side turns (typed text, buttons, edits) go to /turn over HTTP; speak() then asks the
// bot to read the reply aloud (RTVI client message "speak", see voice/bridge.py).
//
// Checked against @pipecat-ai/client-js 1.13.1 + small-webrtc-transport 1.10.8 (RTVI
// protocol 2.1.0, same as pipecat-ai 1.12.0 on the server). startBotAndConnect() POSTs
// /voice/start, then offers WebRTC at /voice/sessions/<id>/api/offer.

import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

export const VOICE_URL = import.meta.env.VITE_VOICE_URL ?? "/voice";

/**
 * on: { state(transportState), user(bool), bot(bool), level(0..1), turn(msg), error(msg), disconnected() }
 */
export function createVoice({ caseId, on = {}, baseUrl = VOICE_URL }) {
  const audio = new Audio();
  audio.autoplay = true;

  const client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onTransportStateChanged: (s) => on.state?.(s),
      onUserStartedSpeaking: () => on.user?.(true),
      onUserStoppedSpeaking: () => on.user?.(false),
      onBotStartedSpeaking: () => on.bot?.(true),
      onBotStoppedSpeaking: () => on.bot?.(false),
      onLocalAudioLevel: (level) => on.level?.(level),
      onServerMessage: (data) => {
        if (data && data.type === "turn") on.turn?.(data);
      },
      onTrackStarted: (track, participant) => {
        if (track.kind !== "audio" || !participant || participant.local) return;
        audio.srcObject = new MediaStream([track]);
        audio.play().catch(() => {});
      },
      onError: (m) => on.error?.(m),
      onDisconnected: () => on.disconnected?.(),
    },
  });

  return {
    async connect() {
      await client.startBotAndConnect({
        endpoint: `${baseUrl}/start`,
        requestData: { transport: "webrtc", body: { case_id: caseId } },
      });
    },
    speak(text) {
      try {
        client.sendClientMessage("speak", { text });
        return true;
      } catch {
        return false;
      }
    },
    async disconnect() {
      try {
        await client.disconnect();
      } finally {
        audio.srcObject = null;
      }
    },
  };
}
