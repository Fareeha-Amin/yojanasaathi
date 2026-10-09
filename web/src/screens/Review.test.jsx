import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import Review from "./Review.jsx";

describe("Review & confirm", () => {
  it("reads back the fields and promises nothing without a yes", () => {
    renderScreen(Review, { summary: fx("review") });
    expect(screen.getByText("Senior Citizen Pension Scheme")).toBeInTheDocument();
    expect(screen.getByText("₹1,20,000", { selector: ".field-value" })).toBeInTheDocument();
    expect(screen.getByText(/Please check: age 62, annual income ₹1,20,000/)).toBeInTheDocument();
    expect(screen.getByText("Nothing is submitted without your yes.")).toBeInTheDocument();
  });

  it("big yes button sends the citizen's yes (the agent's gate decides)", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    const yes = screen.getByRole("button", { name: /ಹೌದು, ಸಲ್ಲಿಸಿ.*Yes, submit/ });
    await userEvent.click(yes);
    expect(ctx.confirm).toHaveBeenCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: /Not now/ }));
    expect(ctx.confirm).toHaveBeenCalledWith(false);
  });

  it("low-confidence fields are amber", () => {
    const summary = fx("review");
    summary.review.fields[1].unsure = true;
    renderScreen(Review, { summary });
    const row = screen.getByText("₹1,20,000", { selector: ".field-value" }).closest("li");
    expect(row).toHaveClass("field-unsure");
    expect(within(row).getByText("I wasn't fully sure I heard this")).toBeInTheDocument();
  });

  it("edit per field goes to the edit endpoint", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    const row = within(screen.getByText("₹1,20,000", { selector: ".field-value" }).closest("li"));
    await userEvent.click(row.getByRole("button", { name: /Edit Annual income/ }));
    const input = row.getByLabelText("Annual income");
    await userEvent.clear(input);
    await userEvent.type(input, "150000");
    await userEvent.click(row.getByRole("button", { name: /Save/ }));
    expect(ctx.edit).toHaveBeenCalledWith("annual_income", 150000, "ವಾರ್ಷಿಕ ಆದಾಯ: ₹1,50,000");
  });

  it("portal form and screenshots are placeholders until Phase 4", () => {
    renderScreen(Review, { summary: fx("review") });
    expect(screen.getByText("Senior Citizen Applicant Full Name")).toBeInTheDocument();
    expect(screen.getByText(/I solemnly affirm/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("not filled yet")).toHaveLength(10);
    // the income is already known: pre-filled and marked
    const income = screen.getByText("Current Annual Income from All Sources (INR)").closest("li");
    expect(within(income).getByText("₹1,20,000")).toBeInTheDocument();
    expect(within(income).getByText("from your answers")).toBeInTheDocument();
    expect(screen.getByText(/Screenshots of the filled portal form appear here/)).toBeInTheDocument();
  });

  it("nothing to review without a confirm pause", () => {
    renderScreen(Review, { summary: fx("eligible") });
    expect(screen.getByText(/Nothing to review right now/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Yes, submit/ })).toBeNull();
  });
});

describe("Review: missing documents, dates", () => {
  it("warns how many documents are still needed, with Upload now (submit not blocked)", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    const box = screen.getByText("5 documents still needed").closest(".docs-missing-box");
    await userEvent.click(within(box).getByRole("link", { name: /Upload now/ }));
    expect(ctx.navigate).toHaveBeenCalledWith("documents");
    expect(screen.getByRole("button", { name: /Yes, submit/ })).toBeEnabled();
  });

  it("no box when nothing is missing", () => {
    const summary = fx("review");
    summary.review.documents_missing = 0;
    renderScreen(Review, { summary });
    expect(screen.queryByText(/still needed/)).toBeNull();
  });

  it("effective date in words in both lines", () => {
    renderScreen(Review, { summary: fx("review") });
    expect(screen.getByText("effective 9 October 2026")).toBeInTheDocument();
  });
});
