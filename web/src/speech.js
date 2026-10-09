// Read aloud when the voice bot is not connected: POST /tts (Sarvam Bulbul, the bot's
// voice) -> one <audio> element. Cached by language + text (object URLs), so "Replay"
// costs nothing. Browser speechSynthesis is the last fallback only (often no Kannada voice).

const MAX_CACHE = 40;
const cache = new Map(); // `${lang}|${text}` -> object URL

let player = null;
function audioEl() {
  if (!player) {
    player = document.createElement("audio");
    player.id = "ys-tts-audio";
    player.setAttribute("aria-hidden", "true");
    player.playsInline = true;
    document.body.appendChild(player);
  }
  return player;
}

export function stopSpeech() {
  if (player) player.pause();
  window.speechSynthesis?.cancel?.();
}

/**
 * Speak via /tts. Resolves "playing" | "blocked" (autoplay policy: call the returned
 * retry from a tap). Throws when /tts fails (503 not configured, 502 Sarvam, network).
 */
export async function speakViaTts(fetchAudio, text, lang) {
  const key = `${lang}|${text}`;
  let url = cache.get(key);
  if (!url) {
    const blob = await fetchAudio(text, lang);
    url = URL.createObjectURL(blob);
    cache.set(key, url);
    if (cache.size > MAX_CACHE) {
      const [oldKey, oldUrl] = cache.entries().next().value;
      cache.delete(oldKey);
      URL.revokeObjectURL(oldUrl);
    }
  }
  const el = audioEl();
  el.pause();
  el.src = url;
  try {
    await el.play();
    return { state: "playing" };
  } catch (e) {
    if (e?.name === "NotAllowedError") return { state: "blocked", retry: () => el.play() };
    throw e;
  }
}

export function browserSpeak(text, lang) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
  if (!synth || !text || typeof SpeechSynthesisUtterance === "undefined") return false;
  const voice = synth.getVoices().find((v) => v.lang?.toLowerCase().startsWith(lang));
  if (!voice) return false;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  u.lang = voice.lang;
  synth.speak(u);
  return true;
}

export function _resetSpeechCache() {
  cache.clear();
}
