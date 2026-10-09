// Render one screen with a fake case context (no network, no voice).
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { CaseContext } from "../case.jsx";
import eligible from "./fixtures/eligible.json";
import interview from "./fixtures/interview.json";
import review from "./fixtures/review.json";
import submitted from "./fixtures/submitted.json";

export const fixtures = { interview, eligible, review, submitted };

/** A deep copy, so a test can change a fixture freely. */
export const fx = (name) => structuredClone(fixtures[name]);

export function renderScreen(Screen, ctx = {}) {
  const value = {
    lang: "kn", session: { case_id: "web-test", token: "t" }, summary: null, messages: [], busy: false,
    error: null, notice: null, route: "talk", voice: { status: "off" }, levelRef: { current: 0 }, canInstall: false,
    navigate: vi.fn(), refresh: vi.fn(), retry: vi.fn(), setLang: vi.fn(), send: vi.fn(), edit: vi.fn(),
    speak: vi.fn(), connectVoice: vi.fn(), disconnectVoice: vi.fn(), pickScheme: vi.fn(), answerField: vi.fn(),
    confirm: vi.fn(), setConsent: vi.fn(), upload: vi.fn(), removeDocument: vi.fn(), loadMyData: vi.fn(),
    deleteMyData: vi.fn(), install: vi.fn(), dismissNotice: vi.fn(),
    ...ctx,
  };
  const utils = render(<CaseContext.Provider value={value}><Screen /></CaseContext.Provider>);
  return { ...utils, ctx: value };
}
