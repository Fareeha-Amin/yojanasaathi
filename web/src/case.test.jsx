import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { answerText, CaseProvider, routeForTurn, routeFromHash, useCase } from "./case.jsx";
import { fx } from "./test/render.jsx";

describe("routing", () => {
  it("opens the screen each turn result belongs on", () => {
    expect(routeForTurn({ pause: { type: "confirm" }, ui: { type: "eligibility" } })).toBe("review");
    expect(routeForTurn({ pause: { type: "otp" } })).toBe("prefill");
    expect(routeForTurn({ pause: { type: "safe_stop" } })).toBe("prefill");
    expect(routeForTurn({ pause: null, ui: { type: "submitted" } })).toBe("applications");
    expect(routeForTurn({ pause: null, ui: { type: "eligibility" } })).toBe("schemes");
    expect(routeForTurn({ pause: null, ui: { type: "progress" } })).toBe("prefill");
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
  const v = { handlers: null, connect: vi.fn().mockResolvedValue(), speak: vi.fn(() => true), disconnect: vi.fn().mockResolvedValue() };
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
    await act(() => ctx.confirm(true));
    expect(api.turn.mock.calls[0][1]).toBe("ಹೌದು");
    expect(v.speak).toHaveBeenCalledWith("ಸಲ್ಲಿಸಲಾಗಿದೆ!");
    expect(screen.getByTestId("route")).toHaveTextContent("applications");
  });

  it("voice that can't connect falls back to typing", async () => {
    const api = fakeApi();
    const { v, createVoice } = fakeVoice();
    v.connect.mockRejectedValue(new Error("no bot"));
    render(<CaseProvider api={api} createVoice={createVoice}><Probe /></CaseProvider>);
    await waitFor(() => expect(api.summary).toHaveBeenCalled());
    await act(() => ctx.connectVoice());
    expect(ctx.voice.status).toBe("error");
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
