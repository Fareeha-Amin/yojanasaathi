// The golden path by typing, in the Kannada UI (default), against the real agent:
// pension conversation -> Schemes (4 matches, reasons) -> pick pension-001 -> Review
// (edit income, read-back again) -> ಹೌದು, ಸಲ್ಲಿಸಿ -> My applications -> delete my data.
import { expect, test } from "@playwright/test";

const AGE = "ನನಗೆ 62 ವರ್ಷ. ನನಗೆ ಪಿಂಚಣಿ ಸಿಗುತ್ತಾ?";
const INCOME = "ಒಂದು ಲಕ್ಷ ಇಪ್ಪತ್ತು ಸಾವಿರ";

test("text path: interview -> schemes -> review -> yes -> applications -> delete", async ({ page }) => {
  const main = page.locator("#main"); // not the voice dock, which repeats the last reply
  await page.goto("/");
  await expect(page.getByText("Demo project, not a government website")).toBeVisible();
  await page.getByRole("button", { name: /Type instead/ }).click();
  await expect(page).toHaveURL(/#\/talk$/);

  // 1. Kannada pension line: the agent asks only the income, with "why I ask"
  const box = page.getByLabel(/Type your answer/);
  await box.fill(AGE);
  await box.press("Enter");
  const conversation = page.getByRole("list", { name: "Conversation" });
  await expect(conversation).toContainText("What is your family's total income in one year?");
  await expect(page.getByText(/I ask your annual income to check/)).toBeVisible();
  await expect(page.locator(".chips")).toContainText("62");

  // 2. Income in Kannada number words -> Schemes screen with reasons and source
  await box.fill(INCOME);
  await box.press("Enter");
  await expect(page).toHaveURL(/#\/schemes$/);
  await expect(page.getByText("You qualify for 4 of 4")).toBeVisible();
  const pension = page.locator("article", { hasText: "Senior Citizen Pension Scheme" });
  await expect(pension).toContainText("₹1,20,000");
  await expect(pension).toContainText("up to ₹3,00,000");
  await expect(pension).toContainText("effective 9 October 2026");

  // 3. Pick pension-001 (= saying its name) -> Review
  await pension.getByRole("button", { name: /Apply for this/ }).click();
  await expect(page).toHaveURL(/#\/review$/);
  await expect(main.getByText(/Please check: age 62, annual income ₹1,20,000/)).toBeVisible();
  await expect(main.getByText("Nothing is submitted without your yes.")).toBeVisible();

  // 4. Edit the income on the review screen: a new read-back, still nothing submitted
  const income = page.locator("li.field-row", { hasText: "Annual income" });
  await income.getByRole("button", { name: /Edit Annual income/ }).click();
  await income.getByLabel("Annual income").fill("150000");
  await income.getByRole("button", { name: /Save/ }).click();
  await expect(main.getByText(/Please check: age 62, annual income ₹1,50,000/)).toBeVisible();
  await expect(page).toHaveURL(/#\/review$/);

  // 5. ಹೌದು, ಸಲ್ಲಿಸಿ -> My applications with the application ID and the next schemes
  await page.getByRole("button", { name: /ಹೌದು, ಸಲ್ಲಿಸಿ.*Yes, submit/ }).click();
  await expect(page).toHaveURL(/#\/applications$/);
  const app = page.locator("article.app", { hasText: "Senior Citizen Pension Scheme" });
  await expect(app.locator(".app-id code")).toHaveText(/^DEMO-\d{4}$/);
  await expect(app.locator(".timeline")).toContainText("You corrected details");
  await expect(app.locator(".timeline")).toContainText("You said yes");
  await expect(page.getByText("You can also apply for")).toBeVisible();

  // The case survives a reload (server-side case memory, same session token)
  await page.reload();
  await expect(page.locator("article.app .app-id code")).toHaveText(/^DEMO-\d{4}$/);

  // 6. Delete my data from the Privacy screen
  await page.getByRole("link", { name: /Privacy/ }).click();
  await page.getByRole("button", { name: /Delete my data/ }).click();
  await page.getByRole("button", { name: /Yes, delete everything/ }).click();
  await expect(page.getByText(/Your data was deleted/)).toBeVisible();
  await page.goto("/#/applications");
  await expect(page.getByText("No applications yet.")).toBeVisible();
});

test("data endpoints refuse requests without the session token", async ({ request }) => {
  const s = await (await request.post("/api/session")).json();
  expect((await request.get(`/api/cases/${s.case_id}/summary`)).status()).toBe(401);
  const other = await (await request.post("/api/session")).json();
  const r = await request.get(`/api/cases/${s.case_id}/data`, { headers: { Authorization: `Bearer ${other.token}` } });
  expect(r.status()).toBe(403);
});
