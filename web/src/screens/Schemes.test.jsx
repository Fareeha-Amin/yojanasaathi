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
    await userEvent.type(pension.getByLabelText(/annual income/), "120000");
    await userEvent.click(pension.getByRole("button", { name: /ಉತ್ತರಿಸಿ/ }));
    expect(ctx.answerField).toHaveBeenCalledWith("annual_income", 120000, "ವಾರ್ಷಿಕ ಆದಾಯ: 120000");
  });

  it("not eligible: see why", () => {
    const summary = fx("eligible");
    const s = summary.schemes[0];
    s.status = "not_eligible";
    s.clauses[1] = { ...s.clauses[1], value: 450000, result: false };
    renderScreen(Schemes, { summary });
    const pension = within(card("Senior Citizen Pension Scheme"));
    expect(pension.getByText("Don't qualify, see why")).toBeInTheDocument();
    expect(pension.getByText("₹4,50,000")).toBeInTheDocument();
    expect(pension.queryByRole("button", { name: /Apply for this/ })).toBeNull();
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
