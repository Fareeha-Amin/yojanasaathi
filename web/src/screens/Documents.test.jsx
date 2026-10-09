import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import Documents from "./Documents.jsx";

function withConsent(summary) {
  summary.consent = { profile: false, documents: true, at: "2026-10-09T10:00:00+05:30" };
  return summary;
}

describe("Documents", () => {
  it("asks for consent before any upload", async () => {
    const { ctx } = renderScreen(Documents, { summary: fx("eligible") });
    expect(screen.getByText("Store my documents safely?")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Take photo|Choose file/)).toBeNull(); // no file input yet
    await userEvent.click(screen.getByRole("button", { name: /Yes, store them encrypted/ }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ documents: true });
  });

  it("missing items first, progress, encryption note", () => {
    const summary = withConsent(fx("eligible"));
    summary.checklist.items[3].status = "missing";
    summary.checklist.items.unshift(summary.checklist.items.splice(3, 1)[0]); // the agent sorts; keep it
    renderScreen(Documents, { summary });
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Income Certificate");
    expect(items[0]).toHaveTextContent("You don't have it yet");
    expect(screen.getByText(/0 of 7 ready/)).toBeInTheDocument();
    expect(screen.getByText(/Encrypted \(AES-256\)/)).toBeInTheDocument();
  });

  it("uploads with the Aadhaar last 4 only", async () => {
    const { ctx } = renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const item = within(screen.getByText("Identity Proof").closest("li"));
    await userEvent.type(item.getByLabelText(/last 4 digits/), "2345678901234");
    expect(item.getByLabelText(/last 4 digits/)).toHaveValue("2345"); // never more than 4
    const file = new File(["jpeg"], "id.jpg", { type: "image/jpeg" });
    await userEvent.upload(item.getByLabelText(/Choose file: Identity Proof/), file);
    expect(ctx.upload).toHaveBeenCalledWith("identity_proof", file, "2345");
  });

  it("rejects files that are too big before uploading", async () => {
    const summary = withConsent(fx("eligible"));
    summary.limits.doc_max_bytes = 3;
    const { ctx } = renderScreen(Documents, { summary });
    const item = within(screen.getByText("Age Proof").closest("li"));
    await userEvent.upload(item.getByLabelText(/· Take photo: Age Proof/), new File(["toolarge"], "a.png", { type: "image/png" }));
    expect(ctx.upload).not.toHaveBeenCalled();
    expect(item.getByRole("alert")).toHaveTextContent("too big");
  });

  it("an uploaded document shows masked Aadhaar and can be deleted", async () => {
    const summary = withConsent(fx("eligible"));
    const id = summary.checklist.items.find((i) => i.doc === "identity_proof");
    id.status = "uploaded";
    id.document = { id: "d1", doc_type: "identity_proof", content_type: "image/jpeg", size_bytes: 20480, aadhaar: "XXXX XXXX 0123" };
    const { ctx } = renderScreen(Documents, { summary });
    const item = within(screen.getByText("Identity Proof").closest("li"));
    expect(item.getByText("Aadhaar XXXX XXXX 0123")).toBeInTheDocument();
    await userEvent.click(item.getByRole("button", { name: /Delete/ }));
    expect(ctx.removeDocument).toHaveBeenCalledWith("d1");
  });

  it("delete all = withdraw document consent", async () => {
    const { ctx } = renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    await userEvent.click(screen.getByRole("button", { name: /Delete all my documents now/ }));
    expect(ctx.setConsent).toHaveBeenCalledWith({ documents: false });
  });
});

describe("Documents: chosen scheme, file buttons, English lines", () => {
  it("Take photo opens the camera; Choose file allows a PDF or a gallery photo", () => {
    renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const item = within(screen.getByText("Identity Proof").closest("li"));
    const cam = item.getByLabelText(/· Take photo: Identity Proof/);
    const file = item.getByLabelText(/Choose file: Identity Proof/);
    expect(cam).toHaveAttribute("capture", "environment");
    expect(file).not.toHaveAttribute("capture");
    expect(file.getAttribute("accept")).toContain("application/pdf");
  });

  it("the English 'For:' line names the schemes in English", () => {
    renderScreen(Documents, { summary: withConsent(fx("eligible")) });
    const item = within(screen.getByText("Age Proof").closest("li"));
    expect(item.getByText(/^ಇದಕ್ಕೆ: ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ/)).toBeInTheDocument();
    expect(item.getByText(/^For: Senior Citizen Pension Scheme/)).toBeInTheDocument();
  });

  it("after choosing a scheme: 'For <scheme>', its list, missing count, others collapsed", () => {
    renderScreen(Documents, { summary: withConsent(fx("review")) });
    expect(screen.getByText("For Senior Citizen Pension Scheme")).toBeInTheDocument();
    expect(screen.getByText("5 missing")).toBeInTheDocument();
    const others = screen.getByText(/Documents for your other schemes \(2\)/).closest("details");
    expect(others).not.toHaveAttribute("open");
    expect(within(others).getByText("Family Details")).toBeInTheDocument();
  });
});
