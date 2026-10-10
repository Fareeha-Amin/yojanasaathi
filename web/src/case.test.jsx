import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { answerText, CaseProvider, routeForTurn, routeFromHash, useCase } from "./case.jsx";
import { browserSpeak, speakViaTts } from "./speech.js";
import { fx } from "./test/render.jsx";

vi.mock("./speech.js", () => ({
  speakViaTts: vi.fn().mockResolvedValue({ state: "playing" }),
  browserSpeak: vi.fn(() => false),
  stopSpeech: vi.fn(),
}));

describe("routing", () => {
  it("opens the screen each turn result belongs on", () => {
    expect(routeForTurn({ pause: { type: "confirm" }, ui: { type: "eligibility" } })).toBe("review");
    expect(routeForTurn({ pause: { type: "otp" } })).toBe("prefill");
    expect(routeForTurn({ pause: { type: "safe_stop" } })).toBe("prefill");
    expect(routeForTurn({ pause: null, ui: { type: "submitted" } })).toBe("applications");
    expect(routeForTurn({ pause: null, ui: { type: "eligibility" } })).toBe("schemes");
    expect(routeForTurn({ pause: null, ui: { type: "progress" } })).toBe("prefill");
    expect(routeForTurn({ pause: null, ui: { type: "form", field: "full_name" } })).toBe("talk");
    expect(routeForTurn({ reply: "How old are you?", pause: null, ui: null })).toBeNull();
  });

  it("parses hashes", () => {
    expect(routeFromHash("#/review")).toBe("review");
    expect(routeFromHash("#/nope")).toBe("");
    expect(routeFromHash("")).toBe("");
  });

  it("inline answers are in words the agent's parsers read", () => {
    expect(answerText("age", 62)).toBe("I am 62 years old");
    expect(answerText("annual_income", 120000)).toBe("my annual income is 120000");
  });
});

function fakeApi(overrides = {}) {
  return {
    ensureSession: vi.fn().mockResolvedValue({ case_id: "web-abc", token: "tok", expires_at: 1 }),
    newSession: vi.fn().mockResolvedValue({ case_id: "web-new", token: "tok2", expires_at: 1 }),
    summary: vi.fn().mockResolvedValue(fx("interview")),
    turn: vi.fn(),
    edit: vi.fn(),
    setConsent: vi.fn(),
    uploadDocument: vi.fn(),
    deleteDocument: vi.fn(),
    myData: vi.fn(),
    deleteMyData: vi.fn().mockResolvedValue({ deleted: true }),
    ...overrides,
  };
}

function fakeVoice() {
  const v = {
    handlers: null, connect: vi.fn().mockResolvedValue(), speak: vi.fn(() => true),
    disconnect: vi.fn().mockResolvedValue(), resumeAudio: vi.fn().mockResolvedValue(),
  };
  const createVoice = vi.fn(({ caseId, on }) => {
    v.caseId = caseId;
    v.handlers = on;
    return v;
  });
  return { v, createVoice };
}

let ctx;
function Probe() {
  ctx = useCase();
  return <div data-testid="route">{ctx.route}</div>;
}

describe("CaseProvider: one case, text and voice", () => {
  beforeEach(() => {
    window.location.hash = "#/talk";
    localStorage.clear();
  });

  it("text turn: script decides lang, bubbles with subtitle, schemes screen, summary reload", async () => {
    const api = fakeApi({
      turn: vi.fn().mockResolvedValue({
        reply: "ನೀವು 4 ಯೋಜನೆಗಳಿಗೆ ಅರ್ಹರು.", subtitle: "You qualify for 4 schemes.", pause: null, ui: { type: "eligibility" },
      }),
    });
    render(<CaseProvider api={api} createVoice={fakeVoice().createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalledWith({ case_id: "web-abc", token: "tok", expires_at: 1 }, "kn"));
    await act(() => ctx.setLang("en"));
    await act(() => ctx.send("ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ 1,20,000"));
    expect(api.turn).toHaveBeenCalledWith(expect.objectContaining({ case_id: "web-abc" }), "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ 1,20,000", "kn");
    expect(ctx.lang).toBe("kn"); // typed Kannada switches the UI to Kannada
    expect(ctx.messages.map((m) => [m.from, m.text, m.en ?? null])).toEqual([
      ["user", "ನನಗೆ 62 ವರ್ಷ, ಆದಾಯ 1,20,000", null],
      ["agent", "ನೀವು 4 ಯೋಜನೆಗಳಿಗೆ ಅರ್ಹರು.", "You qualify for 4 schemes."],
    ]);
    expect(screen.getByTestId("route")).toHaveTextContent("schemes");
    expect(api.summary).toHaveBeenCalledTimes(3); // start, language switch, after the turn
  });

  it("latin text keeps the UI language; Aadhaar is masked in the bubble", async () => {
    const api = fakeApi({ turn: vi.fn().mockResolvedValue({ reply: "ok", pause: null, ui: null }) });
    render(<CaseProvider api={api} createVoice={fakeVoice().createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.send("my aadhaar is 2345 6789 0123"));
    expect(api.turn.mock.calls[0][2]).toBe("kn");
    expect(ctx.messages[0].text).toBe("my aadhaar is XXXX XXXX 0123");
  });

  it("voice uses the same case; its turn messages land in the transcript and open the review", async () => {
    const api = fakeApi();
    const { v, createVoice } = fakeVoice();
    render(<CaseProvider api={api} createVoice={createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.connectVoice());
    expect(v.caseId).toBe("web-abc");
    act(() => v.handlers.state("ready"));
    expect(ctx.voice.status).toBe("listening");
    act(() => v.handlers.user(true));
    expect(ctx.voice.status).toBe("user");
    act(() => v.handlers.user(false));
    expect(ctx.voice.status).toBe("thinking");
    act(() => v.handlers.turn({
      type: "turn", text: "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ", lang: "kn", reply: "ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ…", subtitle: "Please check…",
      pause: { type: "confirm", preview: {} }, ui: null,
    }));
    expect(ctx.messages.map((m) => [m.from, m.via])).toEqual([["user", "voice"], ["agent", "voice"]]);
    expect(screen.getByTestId("route")).toHaveTextContent("review");
    expect(v.speak).not.toHaveBeenCalled(); // the bot already speaks its own replies
  });

  it("web-side turns are read aloud by the bot while voice is on", async () => {
    const api = fakeApi({ turn: vi.fn().mockResolvedValue({ reply: "ಸಲ್ಲಿಸಲಾಗಿದೆ!", pause: null, ui: { type: "submitted" } }) });
    const { v, createVoice } = fakeVoice();
    render(<CaseProvider api={api} createVoice={createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.connectVoice());
    act(() => v.handlers.state("ready"));
    await act(() => ctx.confirm(true));
    expect(api.turn.mock.calls[0][1]).toBe("ಹೌದು");
    expect(v.speak).toHaveBeenCalledWith("ಸಲ್ಲಿಸಲಾಗಿದೆ!");
    expect(screen.getByTestId("route")).toHaveTextContent("applications");
  });

  it("voice that can't connect falls back to typing", async () => {
    const api = fakeApi();
    const { v, createVoice } = fakeVoice();
    v.connect.mockRejectedValue(Object.assign(new Error("blocked"), { reason: "blocked" }));
    render(<CaseProvider api={api} createVoice={createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.connectVoice());
    expect(ctx.voice).toEqual({ status: "error", reason: "blocked" }); // the reason is shown
  });

  it("delete my data: new case, empty transcript, landing", async () => {
    const api = fakeApi({ turn: vi.fn().mockResolvedValue({ reply: "ok", pause: null, ui: null }) });
    render(<CaseProvider api={api} createVoice={fakeVoice().createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.send("hello"));
    await act(() => ctx.deleteMyData());
    expect(api.deleteMyData).toHaveBeenCalledWith(expect.objectContaining({ case_id: "web-abc" }));
    expect(ctx.session.case_id).toBe("web-new");
    expect(ctx.messages).toEqual([]);
    expect(ctx.notice).toBe("deleted");
    expect(screen.getByTestId("route")).toHaveTextContent("");
  });

  it("agent down: error state, not a crash", async () => {
    const api = fakeApi({ ensureSession: vi.fn().mockRejectedValue(new Error("network")) });
    render(<CaseProvider api={api} createVoice={fakeVoice().createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(ctx.error).toBe("agent"));
  });
});

describe("speech: bot output, read aloud, speak replies", () => {
  beforeEach(() => {
    window.location.hash = "#/talk";
    localStorage.clear();
    speakViaTts.mockClear();
    browserSpeak.mockClear();
  });

  async function setup(apiOverrides = {}) {
    const api = fakeApi({ tts: vi.fn().mockResolvedValue(new Blob(["wav"])), ...apiOverrides });
    const { v, createVoice } = fakeVoice();
    render(<CaseProvider api={api} createVoice={createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    return { api, v };
  }

  it("the greeting ('say') is a Saathi bubble with its subtitle; repeats are shown once", async () => {
    const { v } = await setup();
    await act(() => ctx.connectVoice());
    act(() => v.handlers.say({ type: "say", text: "ನಮಸ್ಕಾರ, ನಾನು ಯೋಜನಾಸಾಥಿ.", subtitle: "Hello, I'm YojanaSaathi." }));
    act(() => v.handlers.say({ type: "say", text: "ನಮಸ್ಕಾರ, ನಾನು ಯೋಜನಾಸಾಥಿ.", subtitle: "Hello, I'm YojanaSaathi." }));
    expect(ctx.messages.map((m) => [m.from, m.text, m.en])).toEqual([
      ["agent", "ನಮಸ್ಕಾರ, ನಾನು ಯೋಜನಾಸಾಥಿ.", "Hello, I'm YojanaSaathi."],
    ]);
  });

  it("a voice turn after the same HTTP reply is not shown twice", async () => {
    const { v } = await setup({ turn: vi.fn().mockResolvedValue({ reply: "ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?", pause: null, ui: null }) });
    await act(() => ctx.send("ಪಿಂಚಣಿ"));
    await act(() => ctx.connectVoice());
    act(() => v.handlers.turn({ type: "turn", text: null, reply: "ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?", pause: null, ui: null }));
    expect(ctx.messages.filter((m) => m.from === "agent")).toHaveLength(1);
  });

  it("typed turn with voice off: the reply is spoken via /tts (Speak replies, default on)", async () => {
    const { api } = await setup({ turn: vi.fn().mockResolvedValue({ reply: "ಆದಾಯ ಎಷ್ಟು?", pause: null, ui: null }) });
    expect(ctx.speakReplies).toBe(true);
    await act(() => ctx.send("ನನಗೆ 62 ವರ್ಷ"));
    await waitFor(() => expect(speakViaTts).toHaveBeenCalledTimes(1));
    const [fetchAudio, text, lang] = speakViaTts.mock.calls[0];
    expect([text, lang]).toEqual(["ಆದಾಯ ಎಷ್ಟು?", "kn"]);
    await fetchAudio(text, lang);
    expect(api.tts).toHaveBeenCalledWith(expect.objectContaining({ case_id: "web-abc" }), "ಆದಾಯ ಎಷ್ಟು?", "kn");
  });

  it("Speak replies off: typed turns stay silent; the setting is remembered", async () => {
    await setup({ turn: vi.fn().mockResolvedValue({ reply: "ok", pause: null, ui: null }) });
    act(() => ctx.setSpeakReplies(false));
    await act(() => ctx.send("hello"));
    expect(speakViaTts).not.toHaveBeenCalled();
    expect(localStorage.getItem("ys.speakReplies")).toBe("0");
  });

  it("read aloud: the bot when voice is live, else /tts, else the browser", async () => {
    const { v } = await setup();
    await act(() => ctx.speak("ಓದಿ"));
    expect(speakViaTts).toHaveBeenCalledTimes(1);
    expect(browserSpeak).not.toHaveBeenCalled();

    speakViaTts.mockRejectedValueOnce(new Error("503"));
    await act(() => ctx.speak("ಓದಿ"));
    expect(browserSpeak).toHaveBeenCalledWith("ಓದಿ", "kn");

    await act(() => ctx.connectVoice());
    act(() => v.handlers.state("ready"));
    await act(() => ctx.speak("ಓದಿ"));
    expect(v.speak).toHaveBeenCalledWith("ಓದಿ");
    expect(speakViaTts).toHaveBeenCalledTimes(2);
  });

  it("autoplay blocked: 'Tap to hear Saathi' state, resumed by a tap", async () => {
    const { v } = await setup();
    await act(() => ctx.connectVoice());
    act(() => v.handlers.audioBlocked());
    expect(ctx.audioBlocked).toBe(true);
    await act(() => ctx.resumeAudio());
    expect(v.resumeAudio).toHaveBeenCalled();
    expect(ctx.audioBlocked).toBe(false);
  });

  it("each voice phase is visible: connecting, listening, user, thinking, bot, error", async () => {
    const { v } = await setup();
    let resolve;
    v.connect.mockImplementation(() => new Promise((r) => { resolve = r; }));
    act(() => { ctx.connectVoice(); });
    expect(ctx.voice.status).toBe("connecting");
    act(() => v.handlers.state("ready"));
    expect(ctx.voice.status).toBe("listening");
    act(() => v.handlers.user(true));
    expect(ctx.voice.status).toBe("user");
    act(() => v.handlers.user(false));
    expect(ctx.voice.status).toBe("thinking");
    act(() => v.handlers.bot(true));
    expect(ctx.voice.status).toBe("bot");
    act(() => v.handlers.error("blocked"));
    expect(ctx.voice).toEqual({ status: "error", reason: "blocked" });
    await act(async () => resolve());
  });
});
