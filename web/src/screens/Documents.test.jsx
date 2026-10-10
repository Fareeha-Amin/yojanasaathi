import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Documents from "./Documents.jsx";

const kn = (k, vars) => tr("kn", k, vars);
/** A case whose owner has agreed to store documents (mirrors tapping "Yes"). */
const withConsent = (summary) => {
  summary.consent = { ...summary.consent, documents: true };
  return summary;
};
const itemOf = (labelKn) => within(screen.getByText(labelKn).closest("li"));

describe("Your documents", () => {
  it("asks for consent before any upload is possible", async () => {
    const { ctx } = renderScreen(Documents, { summary: fx("eligible") });
    expect(screen.getByText(kn("docs_consent_title"))).toBeInTheDocument();
    const inputs = screen.getAllByLabelText(/Take photo|Choose file/);
    expect(inputs.length).toBeGreaterThan(0);
    expect(inputs.every((el) => el.disabled)).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /Yes, store them encrypted/ }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ documents: true });
  });

  it("pins the first missing document, with progress and the encryption note", () => {
    const summary = withConsent(fx("eligible"));
    const i = summary.checklist.items.findIndex((x) => x.doc === "income_certificate");
    summary.checklist.items[i].status = "missing";
    summary.checklist.items.unshift(summary.checklist.items.splice(i, 1)[0]);
    renderScreen(Documents, { summary });
    const pinned = document.querySelector(".doc-list li.doc-pinned");
    expect(pinned).toHaveTextContent("ಆದಾಯ ಪ್ರಮಾಣಪತ್ರ");
    expect(pinned).toHaveTextContent(kn("doc_missing_now"));
    expect(screen.getByText(kn("docs_progress", { ready: 0, total: 7 }))).toBeInTheDocument();
    expect(screen.getByText(kn("docs_note", { hours: 24 }))).toBeInTheDocument();
  });

  it("uploads with only the Aadhaar last 4 digits", async () => {
    const { ctx } = renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const item = itemOf("ಗುರುತಿನ ಪುರಾವೆ");
    await userEvent.type(item.getByLabelText(kn("aadhaar_last4")), "2345678901234");
    expect(item.getByLabelText(kn("aadhaar_last4"))).toHaveValue("2345");
    const file = new File(["jpeg"], "id.jpg", { type: "image/jpeg" });
    await userEvent.upload(item.getByLabelText(/Choose file: Identity Proof/), file);
    expect(ctx.upload).toHaveBeenCalledWith("identity_proof", file, "2345");
  });

  it("refuses a file that is too big", async () => {
    const summary = withConsent(fx("eligible"));
    summary.limits.doc_max_bytes = 3;
    const { ctx } = renderScreen(Documents, { summary });
    const item = itemOf("ವಯಸ್ಸಿನ ಪುರಾವೆ");
    await userEvent.upload(item.getByLabelText(/· Take photo: Age Proof/), new File(["toolarge"], "a.png", { type: "image/png" }));
    expect(ctx.upload).not.toHaveBeenCalled();
    expect(item.getByRole("alert")).toHaveTextContent(kn("file_too_big", { mb: 0 }));
  });

  it("shows an uploaded document masked, with delete", async () => {
    const summary = withConsent(fx("eligible"));
    const id = summary.checklist.items.find((x) => x.doc === "identity_proof");
    id.status = "uploaded";
    id.document = { id: "d1", doc_type: "identity_proof", size_bytes: 20480, aadhaar: "XXXX XXXX 0123" };
    const { ctx } = renderScreen(Documents, { summary });
    const item = itemOf("ಗುರುತಿನ ಪುರಾವೆ");
    expect(item.getByText("XXXX XXXX 0123")).toBeInTheDocument();
    await userEvent.click(item.getByRole("button", { name: /Delete/ }));
    expect(ctx.removeDocument).toHaveBeenCalledWith("d1");
  });

  it("Delete now withdraws consent to store documents", async () => {
    const { ctx } = renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    await userEvent.click(screen.getByRole("button", { name: kn("delete_now") }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ documents: false });
  });

  it("Take photo uses the camera; Choose file allows a PDF from the gallery", () => {
    renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const item = itemOf("ಗುರುತಿನ ಪುರಾವೆ");
    expect(item.getByLabelText(/· Take photo: Identity Proof/)).toHaveAttribute("capture", "environment");
    const file = item.getByLabelText(/Choose file: Identity Proof/);
    expect(file).not.toHaveAttribute("capture");
    expect(file.getAttribute("accept")).toContain("application/pdf");
  });

  it("names the schemes each document is for, in both languages", () => {
    renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const li = screen.getByText("ವಯಸ್ಸಿನ ಪುರಾವೆ").closest("li");
    expect(li).toHaveTextContent("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ");
    expect(li).toHaveTextContent("For: Senior Citizen Pension Scheme");
  });

  it("after a scheme is chosen, shows that scheme's list and collapses the rest", () => {
    renderScreen(Documents, { summary: withConsent(fx("review")) });
    expect(screen.getByText(kn("docs_for_scheme", { title: "ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ" }))).toBeInTheDocument();
    expect(screen.getByText(kn("docs_missing_n", { n: 5 }))).toBeInTheDocument();
    const others = screen.getByText(kn("other_docs", { n: 2 })).closest("details");
    expect(others).not.toHaveAttribute("open");
    expect(within(others).getByText("ಕುಟುಂಬದ ವಿವರಗಳು")).toBeInTheDocument();
  });
});
