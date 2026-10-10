import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Talk from "./Talk.jsx";

const kn = (k, vars) => tr("kn", k, vars);

describe("Talk", () => {
  it("shows what I know so far, the question and why I ask", () => {
    renderScreen(Talk, { summary: fx("interview") });
    expect(screen.getByText(kn("known_title"))).toBeInTheDocument();
    expect(screen.getByText("ವಯಸ್ಸು 62")).toBeInTheDocument();
    // the question card (and the "where we left off" bubble on a reload)
    expect(screen.getAllByText("What is your family's total income in one year?").length).toBeGreaterThan(0);
    // "Why I ask" is a heading of its own: no line holding just a colon
    const why = document.querySelector(".why-note");
    expect(why).toHaveTextContent("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ");
    expect(why.querySelector(".why-title")).toHaveTextContent(kn("why_title"));
    expect([...why.querySelectorAll("*")].some((el) => el.textContent.trim() === ":")).toBe(false);
  });

  it("shows Kannada bubbles with the English subtitle underneath, and replay", async () => {
    const { ctx } = renderScreen(Talk, {
      summary: fx("interview"),
      messages: [
        { id: 1, from: "user", text: "ನನಗೆ 62 ವರ್ಷ", via: "voice" },
        { id: 2, from: "agent", text: "ನಿಮ್ಮ ಕುಟುಂಬದ ಒಂದು ವರ್ಷದ ಒಟ್ಟು ಆದಾಯ ಎಷ್ಟು?", en: "What is your family's total income in one year?" },
      ],
    });
    const list = screen.getByRole("list", { name: "Conversation" });
    expect(list).toHaveTextContent("ನನಗೆ 62 ವರ್ಷ");
    expect(list).toHaveTextContent(kn("via_voice"));
    expect(list).toHaveTextContent("What is your family's total income in one year?");
    await userEvent.click(screen.getByRole("button", { name: /Replay/ }));
    expect(ctx.speak).toHaveBeenCalledWith("ನಿಮ್ಮ ಕುಟುಂಬದ ಒಂದು ವರ್ಷದ ಒಟ್ಟು ಆದಾಯ ಎಷ್ಟು?");
  });

  it("type-instead sends the text through /turn", async () => {
    const { ctx } = renderScreen(Talk, { summary: fx("interview") });
    await userEvent.type(screen.getByLabelText(/Type your answer/), "ಒಂದು ಲಕ್ಷ{Enter}");
    expect(ctx.send).toHaveBeenCalledWith("ಒಂದು ಲಕ್ಷ");
  });

  it("mic button starts and stops voice, with a live state", async () => {
    const off = renderScreen(Talk, { summary: fx("interview") });
    const mic = screen.getByRole("button", { name: /Start voice conversation/ });
    expect(mic).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(mic);
    expect(off.ctx.connectVoice).toHaveBeenCalled();
    off.unmount();

    const on = renderScreen(Talk, { summary: fx("interview"), voice: { status: "user" } });
    expect(screen.getByRole("status")).toHaveTextContent(kn("mic_user"));
    await userEvent.click(screen.getByRole("button", { name: /Voice is on/ }));
    expect(on.ctx.disconnectVoice).toHaveBeenCalled();
  });

  it("points to the review when the case waits for confirmation", async () => {
    const { ctx } = renderScreen(Talk, { summary: fx("review") });
    await userEvent.click(document.querySelector(".banner-action"));
    expect(ctx.navigate).toHaveBeenCalledWith("review");
  });
});

describe("Talk: voice states and audio", () => {
  it("an error names its reason", () => {
    renderScreen(Talk, { summary: fx("interview"), voice: { status: "error", reason: "blocked" } });
    expect(screen.getByRole("status")).toHaveTextContent(kn("mic_err_blocked"));
  });

  it.each([
    ["connecting", "mic_connecting"], ["listening", "mic_listening_long"], ["thinking", "mic_thinking"], ["bot", "mic_bot"],
  ])("%s is shown", (status, key) => {
    renderScreen(Talk, { summary: fx("interview"), voice: { status } });
    expect(screen.getByRole("status")).toHaveTextContent(kn(key));
  });
});
