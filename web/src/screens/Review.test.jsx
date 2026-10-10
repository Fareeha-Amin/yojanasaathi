import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Review from "./Review.jsx";

const kn = (k, vars) => tr("kn", k, vars);

describe("Review & confirm", () => {
  it("reads the form back, with the English line, and promises nothing without a yes", () => {
    const r = fx("review").review;
    renderScreen(Review, { summary: fx("review") });
    expect(screen.getByText(r.title_en)).toBeInTheDocument();
    expect(screen.getByText("₹1,20,000", { selector: ".field-value" })).toBeInTheDocument();
    expect(screen.getByText(/Please check: age 62, annual income ₹1,20,000/)).toBeInTheDocument();
    expect(screen.getByText(kn("nothing_without_yes"))).toBeInTheDocument();
    expect(screen.getByText(kn("effective", { date: r.effective_date_text }))).toBeInTheDocument();
  });

  it("warns about missing documents and links to uploads, without blocking submit", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    const box = screen.getByText(kn("docs_still_needed", { n: 5 })).closest(".docs-missing-box");
    await userEvent.click(within(box).getByRole("link", { name: kn("upload_now") }));
    expect(ctx.navigate).toHaveBeenCalledWith("documents");
    expect(screen.getByRole("button", { name: /Yes, submit/ })).toBeEnabled();
  });

  it("shows no missing-documents warning when nothing is missing", () => {
    const summary = fx("review");
    summary.review.documents_missing = 0;
    renderScreen(Review, { summary });
    expect(document.querySelector(".docs-missing-box")).toBeNull();
  });

  it("the big yes goes through the same gate, and can be declined", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    await userEvent.click(screen.getByRole("button", { name: /Yes, submit/ }));
    expect(ctx.confirm).toHaveBeenCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: kn("not_now") }));
    expect(ctx.confirm).toHaveBeenCalledWith(false);
  });

  it("flags a value it wasn't sure it heard", () => {
    const summary = fx("review");
    summary.review.fields[1].unsure = true;
    renderScreen(Review, { summary });
    const row = screen.getByText("₹1,20,000", { selector: ".field-value" }).closest("li");
    expect(row).toHaveClass("field-unsure");
    expect(within(row).getByText(kn("unsure_note"))).toBeInTheDocument();
  });

  it("lets the citizen correct one value", async () => {
    const { ctx } = renderScreen(Review, { summary: fx("review") });
    const row = screen.getByText("₹1,20,000", { selector: ".field-value" }).closest("li");
    await userEvent.click(within(row).getByRole("button", { name: /Edit Annual income/ }));
    const input = within(row).getByLabelText("Annual income");
    await userEvent.clear(input);
    await userEvent.type(input, "150000");
    await userEvent.click(within(row).getByRole("button", { name: kn("save") }));
    expect(ctx.edit).toHaveBeenCalledWith("annual_income", 150000, "ವಾರ್ಷಿಕ ಆದಾಯ: ₹1,50,000");
  });

  it("shows the portal form, marking what it can prefill from the answers", () => {
    const r = fx("review").review;
    renderScreen(Review, { summary: fx("review") });
    expect(screen.getByText(r.form_fields[0].label)).toBeInTheDocument();
    expect(screen.getAllByLabelText("not filled yet")).toHaveLength(10);
    const income = screen.getByText(r.form_fields[4].label).closest("li");
    expect(within(income).getByText("₹1,20,000")).toBeInTheDocument();
    expect(within(income).getByText(kn("from_answers"))).toBeInTheDocument();
    expect(screen.getByText(kn("screenshots_placeholder"))).toBeInTheDocument();
  });

  it("says there is nothing to review when the case is not paused at confirm", () => {
    renderScreen(Review, { summary: fx("eligible") });
    expect(screen.getByText(kn("review_empty"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Yes, submit/ })).toBeNull();
  });
});
