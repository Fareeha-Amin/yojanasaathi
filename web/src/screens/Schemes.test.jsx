import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import Schemes from "./Schemes.jsx";

const card = (titleEn) => screen.getByText(titleEn).closest("article");

describe("Schemes for you", () => {
  it("lists all four with you-qualify, the WHY box and the source date", () => {
    renderScreen(Schemes, { summary: fx("eligible") });
    expect(screen.getByText("You qualify for 4 of 4")).toBeInTheDocument();
    expect(screen.getAllByText("You qualify")).toHaveLength(4);
    const pension = within(card("Senior Citizen Pension Scheme"));
    expect(pension.getByText("₹1,20,000")).toBeInTheDocument(); // the citizen's value
    expect(pension.getByText("up to ₹3,00,000")).toBeInTheDocument(); // the rule
    expect(pension.getByText("60 or more")).toBeInTheDocument();
    expect(pension.getByText(/effective 9 October 2026/)).toBeInTheDocument();
    expect(pension.getByText("DEMO scheme on the mock portal")).toBeInTheDocument();
  });

  it("apply = saying the scheme's name", async () => {
    const summary = fx("eligible");
    const { ctx } = renderScreen(Schemes, { summary });
    await userEvent.click(within(card("Senior Citizen Pension Scheme")).getByRole("button", { name: /Apply for this/ }));
    expect(ctx.pickScheme).toHaveBeenCalledWith(summary.schemes[0]);
    expect(ctx.pickScheme.mock.calls[0][0].title).toBe("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ");
  });

  it("question left: answer inline", async () => {
    const { ctx } = renderScreen(Schemes, { summary: fx("interview") });
    const pension = within(card("Senior Citizen Pension Scheme"));
    expect(pension.getByText("1 question left")).toBeInTheDocument();
    expect(pension.getByText("not told yet")).toBeInTheDocument();
    await userEvent.type(pension.getByLabelText(/Annual income/), "120000");
    await userEvent.click(pension.getByRole("button", { name: /ಉತ್ತರಿಸಿ/ }));
    expect(ctx.answerField).toHaveBeenCalledWith("annual_income", 120000, "ವಾರ್ಷಿಕ ಆದಾಯ: 120000");
  });

  it("not eligible: one collapsed row that expands to the WHY tables", async () => {
    const summary = fx("eligible");
    for (const s of summary.schemes.slice(0, 2)) {
      s.status = "not_eligible";
      s.clauses[0] = { ...s.clauses[0], value: 45, result: false };
    }
    renderScreen(Schemes, { summary });
    const row = screen.getByText("2 schemes you don't qualify for · see why");
    const group = row.closest("details");
    expect(group).not.toHaveAttribute("open");
    expect(screen.queryByText("Senior Citizen Pension Scheme").closest("article")).toBeNull(); // not a card
    await userEvent.click(row);
    expect(group).toHaveAttribute("open");
    expect(within(group).getByText("Senior Citizen Pension Scheme")).toBeInTheDocument();
    expect(within(group).getAllByText("45")).toHaveLength(2);
  });

  it("the English line of an inline question uses the English field name", () => {
    renderScreen(Schemes, { summary: fx("interview") });
    expect(screen.getAllByText("Annual income?").length).toBeGreaterThan(0);
  });

  it("while a review is open, other applications wait", () => {
    renderScreen(Schemes, { summary: fx("review") });
    expect(within(card("Senior Citizen Pension Scheme")).getByRole("button", { name: /Open review/ })).toBeInTheDocument();
    expect(within(card("National Health Support Scheme")).getByRole("button", { name: /Apply for this/ })).toBeDisabled();
  });

  it("read aloud speaks title and reasons", async () => {
    const { ctx } = renderScreen(Schemes, { summary: fx("eligible") });
    await userEvent.click(within(card("Senior Citizen Pension Scheme")).getByRole("button", { name: /Read aloud/ }));
    expect(ctx.speak.mock.calls[0][0]).toMatch(/^ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ\. /);
  });

  it("empty before the interview", () => {
    const summary = fx("interview");
    summary.profile = [];
    renderScreen(Schemes, { summary });
    expect(screen.getByText(/Tell me your age and income/)).toBeInTheDocument();
  });
});
