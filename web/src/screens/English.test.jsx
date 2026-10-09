// The selected language must reach the text the agent sends (scheme titles, field labels,
// document names, replies), not just the UI chrome. The agent always sends the case language,
// so in English mode its English form becomes the main line and the Kannada one drops away.
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Applications from "./Applications.jsx";
import Documents from "./Documents.jsx";
import Profile from "./Profile.jsx";
import Review from "./Review.jsx";
import Schemes from "./Schemes.jsx";
import Talk from "./Talk.jsx";

const en = (k, vars) => tr("en", k, vars);

describe("English mode shows agent text in English", () => {
  it("Schemes: titles, category, WHY lines and the rule date", () => {
    renderScreen(Schemes, { summary: fx("eligible"), lang: "en" });
    expect(screen.getByText("Senior Citizen Pension Scheme")).toBeInTheDocument();
    expect(screen.queryByText("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ")).toBeNull();
    expect(screen.queryByText("ಪಿಂಚಣಿ")).toBeNull();
    expect(screen.getAllByText("Pension").length).toBeGreaterThan(0);
    expect(screen.getAllByText(en("why_min", { label: "Age", value: "62", limit: "60" })).length).toBeGreaterThan(0);
    expect(screen.getByText(en("based_on", { facts: "Age 62 · Annual income ₹1,20,000" }))).toBeInTheDocument();
    expect(screen.getAllByText(en("official_rule", { date: "9 October 2026" })).length).toBeGreaterThan(0);
  });

  it("Documents: the portal's document names and the scheme subtitle", () => {
    const summary = fx("review");
    summary.consent = { ...summary.consent, documents: true };
    renderScreen(Documents, { summary, lang: "en" });
    expect(screen.getAllByText("Identity Proof").length).toBeGreaterThan(0);
    expect(screen.queryByText("ಗುರುತಿನ ಪುರಾವೆ")).toBeNull();
    expect(screen.getByText(en("docs_for_scheme", { title: "Senior Citizen Pension Scheme" }))).toBeInTheDocument();
  });

  it("Review: the scheme, the read-back and the effective date", () => {
    const r = fx("review").review;
    renderScreen(Review, { summary: fx("review"), lang: "en" });
    expect(screen.getByText(r.title_en)).toBeInTheDocument();
    expect(screen.queryByText(r.title)).toBeNull();
    expect(screen.getByText(r.readback_en)).toBeInTheDocument();
    expect(screen.queryByText(r.readback)).toBeNull();
    expect(screen.getByText(en("effective", { date: r.effective_date_text_en }))).toBeInTheDocument();
    expect(screen.getByText(en("docs_still_needed", { n: 5 }))).toBeInTheDocument();
  });

  it("Talk: Saathi's reply, the chips and 'Why I ask'", () => {
    const summary = fx("interview");
    renderScreen(Talk, { summary, lang: "en" });
    expect(screen.getByText(summary.last_reply.en)).toBeInTheDocument();
    expect(screen.queryByText(summary.last_reply.text)).toBeNull();
    expect(screen.getByText("Age 62")).toBeInTheDocument();
    expect(screen.getByText(summary.asking.why_en)).toBeInTheDocument();
  });

  it("Profile: the saved details use the English field names", () => {
    renderScreen(Profile, { summary: fx("submitted"), lang: "en" });
    expect(screen.getByText("Age")).toBeInTheDocument();
    expect(screen.queryByText("ವಯಸ್ಸು")).toBeNull();
    expect(screen.getByText("₹1,20,000")).toBeInTheDocument();
  });

  it("Applications: the scheme, the status badge and the missing documents", () => {
    const a = fx("submitted").applications[0];
    renderScreen(Applications, { summary: fx("submitted"), lang: "en" });
    expect(screen.getByText(a.title_en)).toBeInTheDocument();
    expect(screen.queryByText(a.title)).toBeNull();
    expect(screen.getByText(a.status_text_en)).toBeInTheDocument();
    expect(screen.queryByText(a.status_text)).toBeNull();
    expect(screen.getAllByText("Identity Proof").length).toBeGreaterThan(0);
  });
});
