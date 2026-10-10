// Voice in the web app = the Pipecat voice bot (voice/bot.py, port 7860) over SmallWebRTC,
// through Vite's proxy (/voice -> :7860). The bot is the same one as before: Sarvam STT ->
// POST /turn for THIS case (sent in the /start body) -> Bulbul TTS. It sends RTVI server
// messages: {type: "turn", text, lang, reply, subtitle, pause, ui} after every turn and
// {type: "say", text, subtitle} for lines it says on its own (the greeting).
// Web-side turns (typed text, buttons, edits) go to /turn over HTTP; speak() then asks the
// bot to read the reply aloud (RTVI client message "speak", see voice/bridge.py).
//
// Checked against @pipecat-ai/client-js 1.13.1 + small-webrtc-transport 1.10.8 (RTVI
// protocol 2.1.0, same as pipecat-ai 1.12.0 on the server). startBotAndConnect() POSTs
// /voice/start, then offers WebRTC at /voice/sessions/<id>/api/offer.
// SmallWebRTC reports the bot's audio track with NO participant (onTrackStarted(track));
// only local tracks carry {local: true}. So: play every audio track that is not local.

import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

export const VOICE_URL = import.meta.env.VITE_VOICE_URL ?? "/voice";

/** Why voice could not start, as an i18n key suffix (mic_err_<reason>). */
export class VoiceError extends Error {
  constructor(reason, cause) {
    super(reason);
    this.reason = reason;
    this.cause = cause;
  }
}

export function micErrorReason(err) {
  const name = err?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") return "blocked";
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") return "missing";
  if (name === "NotReadableError" || name === "TrackStartError") return "busy";
  return "failed";
}

const DEVICE_REASON = {
  permissions: "blocked", "not-found": "missing", "undefined-mediadevices": "missing", "in-use": "busy",
  constraints: "missing", unknown: "failed",
};

/** The <audio> element the bot plays through: in the DOM, autoplay, playsInline. */
export function botAudioElement(doc = document) {
  let el = doc.getElementById("ys-bot-audio");
  if (!el) {
    el = doc.createElement("audio");
    el.id = "ys-bot-audio";
    el.autoplay = true;
    el.playsInline = true;
    el.setAttribute("playsinline", "");
    el.setAttribute("aria-hidden", "true");
    doc.body.appendChild(el);
  }
  return el;
}

/** Attach a remote track and play it; resolves true when playing, false if autoplay is blocked. */
export async function playTrack(el, track) {
  el.srcObject = new MediaStream([track]);
  try {
    await el.play();
    return true;
  } catch (e) {
    if (e?.name === "NotAllowedError") return false; // autoplay policy: needs a tap
    throw e;
  }
}

/**
 * on: { state(transportState), user(bool), bot(bool), level(0..1), turn(msg), say(msg),
 *       error(reason), disconnected(), audioBlocked(), audioPlaying() }
 */
export function createVoice({ caseId, on = {}, baseUrl = VOICE_URL, Client = PipecatClient, Transport = SmallWebRTCTransport }) {
  const audio = botAudioElement();

  const client = new Client({
    transport: new Transport(),
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
        if (data?.type === "turn") on.turn?.(data);
        else if (data?.type === "say") on.say?.(data);
      },
      onTrackStarted: (track, participant) => {
        if (track.kind !== "audio" || participant?.local) return;
        playTrack(audio, track)
          .then((ok) => (ok ? on.audioPlaying?.() : on.audioBlocked?.()))
          .catch(() => on.audioBlocked?.());
      },
      onDeviceError: (err) => {
        if (!err?.devices || err.devices.includes("mic")) on.error?.(DEVICE_REASON[err?.type] || "failed");
      },
      onError: (m) => {
        if (m?.data?.fatal) on.error?.("failed"); // non-fatal RTVI errors don't stop the call
      },
      onDisconnected: () => on.disconnected?.(),
    },
  });

  return {
    async connect() {
      // 1. The microphone, first: a clear "blocked" instead of a silent half-connection.
      if (!navigator.mediaDevices?.getUserMedia) throw new VoiceError("missing");
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
      } catch (e) {
        throw new VoiceError(micErrorReason(e), e);
      }
      // 2. Is the voice bot there?
      try {
        const r = await fetch(`${baseUrl}/status`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      } catch (e) {
        throw new VoiceError("unreachable", e);
      }
      // 3. Connect, with this browser's case.
      try {
        await client.startBotAndConnect({
          endpoint: `${baseUrl}/start`,
          requestData: { transport: "webrtc", body: { case_id: caseId } },
        });
      } catch (e) {
        throw new VoiceError("failed", e);
      }
    },
    /** After "Tap to hear Saathi": play() inside the click. */
    async resumeAudio() {
      await audio.play();
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
        audio.pause();
        audio.srcObject = null;
      }
    },
  };
}
