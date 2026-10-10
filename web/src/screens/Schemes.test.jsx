import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fx, renderScreen } from "../test/render.jsx";
import { tr } from "../i18n.js";
import Schemes from "./Schemes.jsx";

const kn = (k, vars) => tr("kn", k, vars);
const card = (titleKn) => screen.getByText(titleKn).closest("article") || screen.getByText(titleKn).closest("details.scheme");

describe("Schemes for you", () => {
  it("lists all four with you-qualify, the WHY box and the source date", () => {
    renderScreen(Schemes, { summary: fx("eligible") });
    expect(screen.getAllByText(kn("st_eligible"))).toHaveLength(4);
    const pension = within(card("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ"));
    expect(pension.getByText(/₹1,20,000/)).toBeInTheDocument(); // the citizen's value
    expect(pension.getByText(/₹3,00,000/)).toBeInTheDocument(); // the rule
    expect(pension.getByText(/60/)).toBeInTheDocument();
    expect(pension.getByText(kn("why_box"))).toBeInTheDocument();
    expect(pension.getByText(/9 ಅಕ್ಟೋಬರ್ 2026/)).toBeInTheDocument();
  });

  it("apply = saying the scheme's name", async () => {
    const summary = fx("eligible");
    const { ctx } = renderScreen(Schemes, { summary });
    await userEvent.click(within(card("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ")).getByRole("button", { name: /Apply with YojanaSaathi/ }));
    expect(ctx.pickScheme).toHaveBeenCalledWith(summary.schemes[0]);
    expect(ctx.pickScheme.mock.calls[0][0].title).toBe("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ");
  });

  it("question left: answer inline", async () => {
    const { ctx } = renderScreen(Schemes, { summary: fx("interview") });
    const pension = within(card("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ"));
    expect(pension.getByText(kn("st_unknown_1"))).toBeInTheDocument();
    await userEvent.type(pension.getByLabelText(/Annual income/), "120000");
    await userEvent.click(pension.getByRole("button", { name: kn("answer") }));
    expect(ctx.answerField).toHaveBeenCalledWith("annual_income", 120000, "ವಾರ್ಷಿಕ ಆದಾಯ: 120000");
  });

  it("not eligible: one collapsed row that expands to the WHY tables", async () => {
    const summary = fx("eligible");
    for (const s of summary.schemes.slice(0, 2)) {
      s.status = "not_eligible";
      s.clauses[0] = { ...s.clauses[0], value: 45, result: false };
    }
    renderScreen(Schemes, { summary });
    const row = screen.getByText(kn("not_eligible_group", { n: 2 }));
    const group = row.closest("details");
    expect(group).not.toHaveAttribute("open");
    expect(screen.queryByText("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ").closest("article")).toBeNull(); // not a card
    await userEvent.click(row);
    expect(group).toHaveAttribute("open");
    expect(within(group).getByText("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ")).toBeInTheDocument();
    expect(within(group).getAllByText(/45/)).toHaveLength(2);
  });

  it("the inline question repeats the English field name in its label", () => {
    renderScreen(Schemes, { summary: fx("interview") });
    expect(screen.getAllByLabelText(/Annual income/).length).toBeGreaterThan(0);
  });

  it("while a review is open, other applications wait", () => {
    renderScreen(Schemes, { summary: fx("review") });
    expect(within(card("ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ")).getByRole("button", { name: /Open review/ })).toBeInTheDocument();
    expect(within(card("ರಾಷ್ಟ್ರೀಯ ಆರೋಗ್ಯ ಬೆಂಬಲ ಯೋಜನೆ")).getByRole("button", { name: /Apply with YojanaSaathi/ })).toBeDisabled();
  });

  it("read aloud speaks the qualifying schemes", async () => {
    const { ctx } = renderScreen(Schemes, { summary: fx("eligible") });
    await userEvent.click(screen.getByRole("button", { name: /Read aloud/ }));
    expect(ctx.speak.mock.calls[0][0]).toMatch(/ಹಿರಿಯ ನಾಗರಿಕರ ಪಿಂಚಣಿ ಯೋಜನೆ/);
  });

  it("empty before the interview", () => {
    const summary = fx("interview");
    summary.profile = [];
    renderScreen(Schemes, { summary });
    expect(screen.getByText(kn("schemes_empty"))).toBeInTheDocument();
  });
});
