"""POST /tts: Sarvam Bulbul speech for the web app's "read aloud", "replay" and typed-turn
replies when the voice bot isn't connected.

Same voice as the bot (voice/bot.py: Pipecat's SarvamTTSService defaults, verified in
pipecat-ai 1.12.0): bulbul:v3, speaker "shubh", pace 1.0, 24 kHz, preprocessing on (v3
always enables it). Keep these in step with the bot. The language comes from the text's
script, like the bot's TTS (voice/lang.py tts_language); `lang` decides Latin text only.

Sarvam's REST API returns one base64 WAV per text chunk; they are joined into one WAV.
Results are cached in memory by (language, text): replay costs no credit. Text is masked
for Aadhaar before it leaves the agent and is never logged.
"""

import base64
import io
import threading
import wave
from collections import OrderedDict

import httpx

from agent import privacy

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v3"
SPEAKER = "shubh"
PACE = 1.0
SAMPLE_RATE = 24000
MAX_CHARS = 600  # a reply or a read-back, not a document (same cap as the bot's "speak")
LANG_CODES = {"kn": "kn-IN", "hi": "hi-IN", "en": "en-IN"}


class TTSError(Exception):
    """Sarvam failed or returned something we can't play."""


def language_code(text: str, lang: str | None) -> str:
    """Kannada script -> kn-IN, Devanagari -> hi-IN, otherwise the given lang (default en-IN)."""
    kn = sum(0x0C80 <= ord(c) < 0x0D00 for c in text)
    hi = sum(0x0900 <= ord(c) < 0x0980 for c in text)
    if kn or hi:
        return "kn-IN" if kn >= hi else "hi-IN"
    return LANG_CODES.get(lang or "en", "en-IN")


def join_wavs(clips: list[bytes]) -> bytes:
    params, frames = None, []
    for clip in clips:
        with wave.open(io.BytesIO(clip)) as w:
            p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params and p != params:
                raise TTSError("audio chunks differ in format")
            params = p
            frames.append(w.readframes(w.getnframes()))
    if not params:
        raise TTSError("no audio returned")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(b"".join(frames))
    return out.getvalue()


class SarvamTTS:
    def __init__(self, api_key: str | None, *, cache_size: int = 128, timeout: float = 20.0,
                 transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self.upstream_calls = 0  # tests: replay must not call Sarvam again

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str, lang: str | None = None) -> bytes:
        """WAV bytes for `text`. Raises TTSError."""
        text, _ = privacy.mask_aadhaar(text.strip())
        code = language_code(text, lang)
        key = (code, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        wav = self._call(text, code)
        with self._lock:
            self._cache[key] = wav
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return wav

    def _call(self, text: str, code: str) -> bytes:
        if not self.api_key:
            raise TTSError("SARVAM_API_KEY is not set")
        self.upstream_calls += 1
        try:
            r = self._http.post(SARVAM_TTS_URL, headers={"api-subscription-key": self.api_key}, json={
                "text": text, "target_language_code": code, "speaker": SPEAKER, "pace": PACE,
                "speech_sample_rate": SAMPLE_RATE, "enable_preprocessing": True, "model": MODEL,
                "output_audio_codec": "wav",
            })
        except httpx.HTTPError as e:
            raise TTSError(f"Sarvam unreachable: {type(e).__name__}") from None
        if r.status_code != 200:
            raise TTSError(f"Sarvam returned HTTP {r.status_code}")
        try:
            clips = [base64.b64decode(a) for a in r.json()["audios"]]
            return join_wavs(clips)
        except (KeyError, ValueError, TypeError, wave.Error, EOFError) as e:
            raise TTSError(f"unexpected Sarvam response: {type(e).__name__}") from None
