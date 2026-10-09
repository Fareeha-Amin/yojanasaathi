import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { botAudioElement, createVoice, micErrorReason, playTrack } from "./voice.js";
import { _resetSpeechCache, browserSpeak, speakViaTts } from "./speech.js";

class FakeMediaStream {
  constructor(tracks) {
    this.tracks = tracks;
  }
}

/** A PipecatClient stand-in that records its callbacks. */
function fakeClient() {
  const made = {};
  class Client {
    constructor(opts) {
      made.opts = opts;
      made.client = this;
      this.startBotAndConnect = vi.fn().mockResolvedValue({});
      this.sendClientMessage = vi.fn();
      this.disconnect = vi.fn().mockResolvedValue();
    }
  }
  return { made, Client, Transport: class {} };
}

beforeEach(() => {
  globalThis.MediaStream = FakeMediaStream;
  document.body.innerHTML = "";
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue();
  HTMLMediaElement.prototype.pause = vi.fn();
  navigator.mediaDevices = { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) };
  globalThis.fetch = vi.fn().mockResolvedValue({ ok: true });
});

afterEach(() => vi.restoreAllMocks());

describe("bot audio", () => {
  it("plays a remote track that comes WITHOUT a participant (SmallWebRTC), in a DOM <audio>", async () => {
    const { made, Client, Transport } = fakeClient();
    const playing = vi.fn();
    createVoice({ caseId: "web-1", on: { audioPlaying: playing }, Client, Transport });
    const track = { kind: "audio" };
    made.opts.callbacks.onTrackStarted(track); // no participant, like SmallWebRTC
    const el = document.getElementById("ys-bot-audio");
    expect(el).toBeInstanceOf(HTMLAudioElement);
    expect(el.autoplay).toBe(true);
    expect(el.hasAttribute("playsinline")).toBe(true);
    expect(el.srcObject.tracks).toEqual([track]);
    await vi.waitFor(() => expect(playing).toHaveBeenCalled());
  });

  it("ignores local tracks and video", () => {
    const { made, Client, Transport } = fakeClient();
    createVoice({ caseId: "web-1", Client, Transport });
    made.opts.callbacks.onTrackStarted({ kind: "audio" }, { local: true });
    made.opts.callbacks.onTrackStarted({ kind: "video" });
    expect(document.getElementById("ys-bot-audio").srcObject ?? null).toBeNull();
  });

  it("autoplay blocked: reports it instead of swallowing, and resumeAudio() plays", async () => {
    HTMLMediaElement.prototype.play = vi.fn().mockRejectedValueOnce(Object.assign(new Error("no"), { name: "NotAllowedError" }))
      .mockResolvedValue();
    const { made, Client, Transport } = fakeClient();
    const blocked = vi.fn();
    const v = createVoice({ caseId: "web-1", on: { audioBlocked: blocked }, Client, Transport });
    made.opts.callbacks.onTrackStarted({ kind: "audio" });
    await vi.waitFor(() => expect(blocked).toHaveBeenCalled());
    await v.resumeAudio();
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
  });

  it("playTrack rethrows other errors", async () => {
    const el = botAudioElement();
    el.play = vi.fn().mockRejectedValue(Object.assign(new Error("x"), { name: "AbortError" }));
    await expect(playTrack(el, { kind: "audio" })).rejects.toThrow("x");
  });
});

describe("connect", () => {
  it("starts the bot with this browser's case", async () => {
    const { made, Client, Transport } = fakeClient();
    await createVoice({ caseId: "web-abc", Client, Transport }).connect();
    expect(made.client.startBotAndConnect).toHaveBeenCalledWith({
      endpoint: "/voice/start", requestData: { transport: "webrtc", body: { case_id: "web-abc" } },
    });
  });

  it.each([
    ["NotAllowedError", "blocked"], ["NotFoundError", "missing"], ["NotReadableError", "busy"], ["Weird", "failed"],
  ])("microphone %s -> reason %s, before any connection", async (name, reason) => {
    navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error(name), { name }));
    const { made, Client, Transport } = fakeClient();
    await expect(createVoice({ caseId: "c", Client, Transport }).connect()).rejects.toMatchObject({ reason });
    expect(made.client.startBotAndConnect).not.toHaveBeenCalled();
    expect(micErrorReason({ name })).toBe(reason);
  });

  it("voice bot down -> unreachable", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    const { Client, Transport } = fakeClient();
    await expect(createVoice({ caseId: "c", Client, Transport }).connect()).rejects.toMatchObject({ reason: "unreachable" });
  });

  it("passes turn and say messages; speak() sends the client message", () => {
    const { made, Client, Transport } = fakeClient();
    const turn = vi.fn(), say = vi.fn();
    const v = createVoice({ caseId: "c", on: { turn, say }, Client, Transport });
    made.opts.callbacks.onServerMessage({ type: "say", text: "ನಮಸ್ಕಾರ", subtitle: "Hello" });
    made.opts.callbacks.onServerMessage({ type: "turn", reply: "x" });
    made.opts.callbacks.onServerMessage({ type: "other" });
    expect(say).toHaveBeenCalledWith({ type: "say", text: "ನಮಸ್ಕಾರ", subtitle: "Hello" });
    expect(turn).toHaveBeenCalledTimes(1);
    v.speak("hello");
    expect(made.client.sendClientMessage).toHaveBeenCalledWith("speak", { text: "hello" });
  });

  it("device errors and fatal RTVI errors carry a reason; non-fatal ones don't", () => {
    const { made, Client, Transport } = fakeClient();
    const error = vi.fn();
    createVoice({ caseId: "c", on: { error }, Client, Transport });
    made.opts.callbacks.onDeviceError({ devices: ["mic"], type: "permissions" });
    made.opts.callbacks.onDeviceError({ devices: ["cam"], type: "not-found" });
    made.opts.callbacks.onError({ data: { fatal: false } });
    made.opts.callbacks.onError({ data: { fatal: true } });
    expect(error.mock.calls).toEqual([["blocked"], ["failed"]]);
  });
});

describe("read aloud via /tts", () => {
  beforeEach(() => {
    _resetSpeechCache();
    URL.createObjectURL = vi.fn((b) => `blob:${b.size}`);
    URL.revokeObjectURL = vi.fn();
  });

  it("fetches once per text + language (replay is cached)", async () => {
    const fetchAudio = vi.fn().mockResolvedValue(new Blob(["wav"]));
    expect(await speakViaTts(fetchAudio, "ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?", "kn")).toEqual({ state: "playing" });
    await speakViaTts(fetchAudio, "ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?", "kn");
    await speakViaTts(fetchAudio, "How old?", "en");
    expect(fetchAudio).toHaveBeenCalledTimes(2);
    expect(document.getElementById("ys-tts-audio").src).toContain("blob:");
  });

  it("autoplay blocked -> blocked + retry", async () => {
    HTMLMediaElement.prototype.play = vi.fn().mockRejectedValue(Object.assign(new Error("no"), { name: "NotAllowedError" }));
    const out = await speakViaTts(vi.fn().mockResolvedValue(new Blob(["w"])), "hi", "en");
    expect(out.state).toBe("blocked");
    expect(typeof out.retry).toBe("function");
  });

  it("/tts failing throws (caller falls back)", async () => {
    await expect(speakViaTts(vi.fn().mockRejectedValue(new Error("503")), "x", "en")).rejects.toThrow("503");
  });

  it("browser speech only when it has a voice for the language", () => {
    const speak = vi.fn();
    window.speechSynthesis = { getVoices: () => [{ lang: "en-IN" }], cancel: vi.fn(), speak };
    globalThis.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
    expect(browserSpeak("ನಮಸ್ಕಾರ", "kn")).toBe(false);
    expect(browserSpeak("hello", "en")).toBe(true);
    expect(speak).toHaveBeenCalledTimes(1);
  });
});
