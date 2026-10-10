"""POST /tts: Sarvam Bulbul read-aloud for the web app (Sarvam scripted, never called)."""

import base64
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import main, tts
from agent.main import app
from tests.helpers import bearer

client = TestClient(app)


def wav(n_frames: int, rate: int = tts.SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * n_frames)
    return out.getvalue()


class FakeSarvam:
    def __init__(self, status: int = 200, clips: list[bytes] | None = None):
        self.status, self.clips, self.requests = status, clips or [wav(100), wav(50)], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        return httpx.Response(200, json={"audios": [base64.b64encode(c).decode() for c in self.clips]})


@pytest.fixture
def sarvam(monkeypatch):
    fake = FakeSarvam()
    monkeypatch.setattr(main, "speech", tts.SarvamTTS("test-key", transport=httpx.MockTransport(fake)))
    return fake


def post(text: str, lang: str | None = None, headers=None):
    body = {"text": text} if lang is None else {"text": text, "lang": lang}
    return client.post("/tts", json=body, headers=bearer("tts-case") if headers is None else headers)


def test_returns_one_wav_with_the_bots_voice_settings(sarvam):
    r = post("ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?", "en")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnframes() == 150 and w.getframerate() == tts.SAMPLE_RATE  # both chunks joined
    [req] = sarvam.requests
    body = json.loads(req.content)
    assert req.headers["api-subscription-key"] == "test-key"
    assert body["target_language_code"] == "kn-IN"  # the script decides, like the bot
    assert (body["model"], body["speaker"], body["pace"], body["speech_sample_rate"]) == (
        "bulbul:v3", "shubh", 1.0, 24000)


def test_replay_is_cached(sarvam):
    assert post("How old are you?", "en").status_code == 200
    assert post("How old are you?", "en").status_code == 200
    assert len(sarvam.requests) == 1
    post("How old are you?", "hi")  # Latin text, other language: another voice language
    assert len(sarvam.requests) == 2


@pytest.mark.parametrize("text, lang, code", [
    ("आपकी उम्र कितनी है?", None, "hi-IN"), ("Shall I submit?", "kn", "kn-IN"), ("ok", None, "en-IN")])
def test_language(text, lang, code):
    assert tts.language_code(text, lang) == code


def test_aadhaar_never_sent_to_sarvam(sarvam):
    post("your aadhaar 2345 6789 0123 is noted", "en")
    assert "2345 6789" not in sarvam.requests[0].content.decode()


def test_needs_a_session_token(sarvam):
    assert post("hello", headers={}).status_code == 401
    assert post("hello", headers={"Authorization": "Bearer x.y.z"}).status_code == 401
    assert sarvam.requests == []


@pytest.mark.parametrize("text", ["", "a" * (tts.MAX_CHARS + 1)])
def test_length_limits(sarvam, text):
    assert post(text).status_code == 422


def test_without_key_503_so_the_app_falls_back():
    # tests/conftest.py: no SARVAM_API_KEY
    assert post("hello").status_code == 503


@pytest.mark.parametrize("fake", [FakeSarvam(status=500), FakeSarvam(clips=[b"not a wav"])])
def test_sarvam_failure_is_502(monkeypatch, fake):
    monkeypatch.setattr(main, "speech", tts.SarvamTTS("k", transport=httpx.MockTransport(fake)))
    assert post("hello").status_code == 502


def test_join_rejects_mixed_formats():
    with pytest.raises(tts.TTSError):
        tts.join_wavs([wav(10, 24000), wav(10, 16000)])
