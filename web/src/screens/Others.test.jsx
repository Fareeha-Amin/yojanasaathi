import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import Applications from "./Applications.jsx";
import Landing from "./Landing.jsx";
import Prefill from "./Prefill.jsx";
import Profile from "./Profile.jsx";

describe("My applications", () => {
  it("shows the ID, status, timeline and the next schemes", async () => {
    const summary = fx("submitted");
    const { ctx } = renderScreen(Applications, { summary });
    expect(screen.getByText("DEMO-0001", { selector: "p.app-id code" })).toBeInTheDocument();
    expect(screen.getByText("submitted")).toBeInTheDocument();
    const timeline = document.querySelector(".timeline");
    expect(timeline).toHaveTextContent("Eligibility checked");
    expect(timeline).toHaveTextContent("You said yes");
    expect(timeline).toHaveTextContent("Submitted");
    expect(screen.getByText(/Live status from the portal is coming soon/)).toBeInTheDocument();
    const next = screen.getByText("Social Security Pension Assistance").closest("li");
    await userEvent.click(within(next).getByRole("button", { name: /Apply for this/ }));
    expect(ctx.pickScheme).toHaveBeenCalledWith(summary.next[0]);
  });

  it("empty state", () => {
    renderScreen(Applications, { summary: fx("eligible") });
    expect(screen.getByText("No applications yet.")).toBeInTheDocument();
  });
});

describe("Pre-fill + OTP", () => {
  it("placeholder steps until the browser agent sends progress", () => {
    renderScreen(Prefill, { summary: fx("eligible") });
    expect(screen.getByText(/demo portal isn't connected yet/)).toBeInTheDocument();
    expect(screen.getByText("Stop before Submit · you review")).toBeInTheDocument();
    expect(screen.queryByText("Your turn: enter the OTP")).toBeNull();
  });

  it("otp pause: the citizen types the code; it is not shown back", async () => {
    const summary = fx("eligible");
    summary.pause = { type: "otp" };
    const { ctx } = renderScreen(Prefill, { summary });
    expect(screen.getByText("Your turn: enter the OTP")).toBeInTheDocument();
    expect(screen.getByText("We never read your messages.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/OTP code/), "4821a93");
    await userEvent.click(screen.getByRole("button", { name: /Send code/ }));
    expect(ctx.send).toHaveBeenCalledWith("482193", { shown: "••••••" });
  });

  it("safe stop and real progress render from the pause / summary", () => {
    const summary = fx("eligible");
    summary.pause = { type: "safe_stop", message: "Field bank_ifsc not found" };
    summary.progress = { steps: [{ key: "open", label: "ಪೋರ್ಟಲ್", label_en: "Open the portal", status: "done", screenshot: "/api/shots/1.png" }] };
    renderScreen(Prefill, { summary });
    expect(screen.getByRole("alert")).toHaveTextContent("I stopped, to be safe");
    expect(screen.getByText("Field bank_ifsc not found")).toBeInTheDocument();
    expect(screen.getByAltText(/Screenshot: Open the portal/)).toHaveAttribute("src", "/api/shots/1.png");
    expect(screen.queryByText(/isn't connected yet/)).toBeNull();
  });
});

describe("Privacy", () => {
  it("delete my data takes two steps", async () => {
    const { ctx } = renderScreen(Profile, { summary: fx("submitted") });
    await userEvent.click(screen.getByRole("button", { name: /Delete my data/ }));
    expect(ctx.deleteMyData).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent("can't be undone");
    await userEvent.click(screen.getByRole("button", { name: /Yes, delete everything/ }));
    expect(ctx.deleteMyData).toHaveBeenCalled();
  });

  it("has no helper mode / new-case button (delete my data gives a fresh case)", () => {
    renderScreen(Profile, { summary: fx("submitted") });
    expect(document.body.textContent).not.toMatch(/helper|new case/i);
  });

  it("consent switches and my data", async () => {
    const loadMyData = vi.fn().mockResolvedValue({ events: [1, 2], audit: [1, 2, 3], consent: {}, saved_profile: {}, case_memory: {}, documents: [] });
    const { ctx } = renderScreen(Profile, { summary: fx("submitted"), loadMyData });
    await userEvent.click(screen.getByRole("switch", { name: /Save my details for next time/ }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ profile: true });
    await userEvent.click(screen.getByRole("button", { name: /See all my data/ }));
    expect(await screen.findByText("3 entries in the audit log")).toBeInTheDocument();
  });
});

describe("Landing", () => {
  it("start talking opens Talk and the mic; the demo label is there", async () => {
    const { ctx } = renderScreen(Landing, { route: "" });
    await userEvent.click(screen.getByRole("button", { name: /Start talking/ }));
    expect(ctx.navigate).toHaveBeenCalledWith("talk");
    expect(ctx.connectVoice).toHaveBeenCalled();
    expect(screen.getByText(/Phone line coming soon/)).toBeInTheDocument();
    expect(screen.getByText("Nothing is submitted until you say yes")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Helping someone|helper|CSC|NGO/i);
  });
});

describe("Tap to hear Saathi", () => {
  it("appears only when autoplay was blocked, and resumes on tap", async () => {
    const { TapToHear } = await import("../components.jsx");
    const off = renderScreen(TapToHear, { audioBlocked: false, resumeAudio: vi.fn() });
    expect(screen.queryByRole("button", { name: /Tap to hear Saathi/ })).toBeNull();
    off.unmount();
    const { ctx } = renderScreen(TapToHear, { audioBlocked: true, resumeAudio: vi.fn() });
    await userEvent.click(screen.getByRole("button", { name: /Tap to hear Saathi/ }));
    expect(ctx.resumeAudio).toHaveBeenCalled();
  });
});

describe("Profile: speak replies", () => {
  it("toggles the setting", async () => {
    const { ctx } = renderScreen(Profile, { summary: fx("submitted"), speakReplies: true, setSpeakReplies: vi.fn() });
    await userEvent.click(screen.getByRole("switch", { name: /Speak replies aloud/ }));
    expect(ctx.setSpeakReplies).toHaveBeenCalledWith(false);
  });
});
