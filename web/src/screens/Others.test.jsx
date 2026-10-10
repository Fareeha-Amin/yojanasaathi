// The screens that are not Talk, Schemes, Documents or Review: My applications, Preparing your
// application (Prefill), Profile, the landing page and the shared "tap to hear" control.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Applications from "./Applications.jsx";
import Landing from "./Landing.jsx";
import Prefill from "./Prefill.jsx";
import Profile from "./Profile.jsx";
import { TapToHear } from "../components.jsx";

const kn = (k, vars) => tr("kn", k, vars);

describe("My applications", () => {
  it("shows the id, status, timeline and what to do next", async () => {
    const { ctx } = renderScreen(Applications, { summary: fx("submitted") });
    expect(screen.getByText("DEMO-0001", { selector: "p.app-id code" })).toBeInTheDocument();
    expect(screen.getByText("Submitted", { selector: ".badge .bi-en" })).toBeInTheDocument();
    const timeline = document.querySelector(".timeline");
    expect(timeline).toHaveTextContent(kn("tl_eligibility_decided"));
    expect(timeline).toHaveTextContent(kn("tl_citizen_approved"));
    expect(timeline).toHaveTextContent(kn("tl_submitted"));
    expect(screen.getByText(kn("status_soon"))).toBeInTheDocument();
    const next = screen.getByText("ಸಾಮಾಜಿಕ ಭದ್ರತಾ ಪಿಂಚಣಿ ನೆರವು ಯೋಜನೆ").closest("li");
    await userEvent.click(within(next).getByRole("button", { name: kn("apply_this") }));
    expect(ctx.pickScheme).toHaveBeenCalledWith(fx("submitted").next[0]);
  });

  it("What to do lists the missing documents with an upload link", async () => {
    const { ctx } = renderScreen(Applications, { summary: fx("submitted") });
    const todo = document.querySelector(".todo");
    expect(todo).toHaveTextContent(kn("todo_upload"));
    expect(todo).toHaveTextContent("ಗುರುತಿನ ಪುರಾವೆ");
    await userEvent.click(within(todo).getByRole("link", { name: kn("upload_now") }));
    expect(ctx.navigate).toHaveBeenCalledWith("documents");
  });

  it("empty state before anything is applied for", () => {
    const summary = fx("submitted");
    summary.applications = [];
    renderScreen(Applications, { summary });
    expect(screen.getByText(kn("apps_empty"))).toBeInTheDocument();
  });
});

describe("Preparing your application", () => {
  it("is honest before the demo portal is connected", () => {
    renderScreen(Prefill, { summary: fx("eligible") });
    expect(screen.getByText(kn("prefill_placeholder"))).toBeInTheDocument();
    expect(screen.getByText(kn("step_stop"))).toBeInTheDocument();
    expect(screen.queryByText(kn("otp_title"))).toBeNull();
  });

  it("OTP pause: type the code and continue", async () => {
    const summary = fx("eligible");
    summary.pause = { type: "otp" };
    const { ctx } = renderScreen(Prefill, { summary });
    expect(screen.getByText(kn("otp_title"))).toBeInTheDocument();
    expect(screen.getByText(kn("otp_never"))).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/OTP code/), "4821a93");
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(ctx.send).toHaveBeenCalledWith("482193", { shown: "••••••" });
  });

  it("safe stop and recorded progress", () => {
    const summary = fx("eligible");
    summary.pause = { type: "safe_stop", message: "Field bank_ifsc not found" };
    summary.progress = { steps: [{ key: "open", label: "ಪೋರ್ಟಲ್", label_en: "Open the portal", status: "done", screenshot: "/api/shots/1.png" }] };
    renderScreen(Prefill, { summary });
    expect(screen.getByRole("alert")).toHaveTextContent(kn("safe_stop_title"));
    expect(screen.getByText("Field bank_ifsc not found")).toBeInTheDocument();
    expect(screen.getByAltText(/Screenshot: Open the portal/)).toHaveAttribute("src", "/api/shots/1.png");
    expect(screen.queryByText(kn("prefill_placeholder"))).toBeNull();
  });
});

describe("Profile", () => {
  it("delete my data asks twice before anything is deleted", async () => {
    const { ctx } = renderScreen(Profile, { summary: fx("submitted") });
    await userEvent.click(screen.getByRole("button", { name: kn("delete_data") }));
    expect(ctx.deleteMyData).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(kn("delete_confirm"));
    await userEvent.click(screen.getByRole("button", { name: kn("delete_yes") }));
    expect(ctx.deleteMyData).toHaveBeenCalled();
  });

  it("consent switches and see-all-my-data", async () => {
    const loadMyData = vi.fn().mockResolvedValue({ events: [], audit: [1, 2, 3], consent: {}, saved_profile: [], case_memory: {}, documents: [] });
    const { ctx } = renderScreen(Profile, { summary: fx("submitted"), loadMyData });
    await userEvent.click(screen.getByRole("switch", { name: kn("consent_profile") }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ profile: true });
    await userEvent.click(screen.getByRole("button", { name: kn("view_data") }));
    expect(await screen.findByText(kn("data_audit", { n: 3 }))).toBeInTheDocument();
  });

  it("speak-replies switch is remembered on the case", async () => {
    const { ctx } = renderScreen(Profile, { summary: fx("submitted"), speakReplies: true, setSpeakReplies: vi.fn() });
    await userEvent.click(screen.getByRole("switch", { name: kn("speak_replies") }));
    expect(ctx.setSpeakReplies).toHaveBeenCalledWith(false);
  });
});

describe("Landing", () => {
  it("start talking opens Talk and requests the mic, with no helper or CSC wording", async () => {
    const { ctx } = renderScreen(Landing, { route: "" });
    await userEvent.click(screen.getByRole("button", { name: kn("start_talking") }));
    expect(ctx.navigate).toHaveBeenCalledWith("talk");
    expect(ctx.connectVoice).toHaveBeenCalled();
    expect(screen.getByText(kn("phone_soon"))).toBeInTheDocument();
    expect(screen.getByText(kn("trust_yes"))).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Helping someone|helper|CSC|NGO|1800|00000 00000/i);
    expect(document.body.textContent).not.toMatch(/toll[- ]?free|Every day · Free/i);
  });
});

describe("Tap to hear", () => {
  it("plays Saathi's voice when autoplay was blocked", async () => {
    const resumeAudio = vi.fn();
    renderScreen(TapToHear, { audioBlocked: true, resumeAudio });
    await userEvent.click(screen.getByRole("button", { name: /Tap to hear Saathi/ }));
    expect(resumeAudio).toHaveBeenCalled();
  });
});
