// The golden path by typing, in the English UI (the UI shows one language at a time; the Kannada
// gate and replies are covered by pytest), against the real agent (no LLM) and the in-process
// fake portal (e2e/agent_server.py; OTP 123456), the Phase 4 flow:
// pension conversation -> Schemes (4 matches, reasons) -> documents uploaded -> pick pension-001
// -> the portal form questions, one per turn -> OTP -> Review (edit income, read-back again)
// -> Yes, submit -> My applications (YJS- ID) -> delete my data.
import { expect, test } from "@playwright/test";

const AGE = "I'm 62. Can I get a pension?";
const INCOME = "1 lakh 20 thousand";
const OTP = "123456";

// What the citizen answers to each portal form question (keyed by the field the agent asks for).
const FORM = {
  full_name: "Ramesh Kumar", dob: "1 January 1964", gender: "male", marital_status: "married",
  disbursement_mode: "bank transfer", bank_account_number: "3456 7890 123", bank_ifsc: "SBIN0001234",
  nominee_name: "Sita Kumar", address: "12 Temple Road, Tumakuru 572101", mobile: "98765 43210",
  declaration_consent: "yes",
};
const DOCS = ["identity_proof", "age_proof", "residence_proof", "income_certificate", "bank_account_details"];

const isTurn = (r) => r.request().method() === "POST" && /\/turn\//.test(r.url());

/** Type one message into the Talk box and return the /turn response body. */
async function say(page, text) {
  const box = page.getByLabel(/Type your answer/);
  const turn = page.waitForResponse(isTurn);
  await box.fill(text);
  await box.press("Enter");
  return (await turn).json();
}

test("text path: interview -> schemes -> documents -> form -> OTP -> review -> yes -> applications -> delete", async ({ page }) => {
  const main = page.locator("#main"); // not the voice dock, which repeats the last reply
  await page.goto("/");
  await page.getByRole("combobox", { name: /Language/ }).selectOption("en");
  await expect(page.getByTitle("Demo project, not a government website")).toBeVisible();
  await page.getByRole("button", { name: /Type instead/ }).click();
  await expect(page).toHaveURL(/#\/talk$/);

  // 1. Kannada pension line: the agent asks only the income, with "why I ask"
  await say(page, AGE);
  const conversation = page.getByRole("list", { name: "Conversation" });
  await expect(conversation).toContainText("What is your family's total income in one year?");
  await expect(page.getByText(/I ask your annual income to check/)).toBeVisible();
  await expect(page.locator(".chips")).toContainText("62");

  // 2. Income in Kannada number words -> Schemes screen with reasons and source
  await say(page, INCOME);
  await expect(page).toHaveURL(/#\/schemes$/);
  await expect(page.getByRole("heading", { name: "Schemes for you" })).toBeVisible();
  for (const title of ["Social Security Pension Assistance", "National Health Support Scheme", "Family Healthcare Assistance"]) {
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }
  const pension = page.locator("article", { hasText: "Senior Citizen Pension Scheme" });
  await expect(pension).toContainText("Annual income ₹1,20,000");
  await expect(pension).toContainText("limit is ₹3,00,000");
  await expect(pension).toContainText("effective 9 October 2026");

  // 3. The portal needs every document: consent + upload (the web app's own session token)
  const session = await page.evaluate(() => JSON.parse(localStorage.getItem("ys.session")));
  const auth = { Authorization: `Bearer ${session.token}` };
  const base = `/api/cases/${session.case_id}`;
  const consent = await page.request.put(`${base}/consent`, { headers: auth, data: { documents: true } });
  expect(consent.ok()).toBeTruthy();
  for (const doc of DOCS) {
    const up = await page.request.put(`${base}/documents/${doc}`, {
      headers: { ...auth, "Content-Type": "application/pdf" }, data: Buffer.from(`%PDF-1.4 e2e ${doc}`),
    });
    expect(up.ok(), `${doc} upload`).toBeTruthy();
  }
  await page.goto("/#/documents");
  await page.reload(); // the summary was read before the uploads
  // 5 documents for pension-001 + the 2 only the other schemes need (not uploaded)
  await expect(page.getByText("5 of 7 ready")).toBeVisible();
  await page.goto("/#/schemes");

  // 4. Pick pension-001 (= saying its name): the agent asks the portal's form questions, one per turn
  const picked = page.waitForResponse(isTurn);
  await pension.getByRole("button", { name: /Apply with YojanaSaathi/ }).click();
  let out = await (await picked).json();
  let asked = 0;
  while (out.ui?.type === "form") {
    const field = out.ui.field;
    expect(FORM[field], `an answer for the form question "${field}"`).toBeTruthy();
    out = await say(page, FORM[field]);
    expect(++asked).toBeLessThan(20);
  }
  expect(asked).toBeGreaterThan(5);

  // 5. The portal's OTP is the citizen's to give (never read by the agent)
  expect(out.pause?.type).toBe("otp");
  await expect(page).toHaveURL(/#\/prefill$/);
  await expect(page.getByText(/OTP/).first()).toBeVisible();
  await page.locator("#otp").fill(OTP);
  const afterOtp = page.waitForResponse(isTurn);
  await page.getByRole("button", { name: /Continue/ }).click();
  expect((await (await afterOtp).json()).pause?.type).toBe("confirm");

  // 6. Review: the read-back of what the PAGE shows, nothing submitted without a yes
  await expect(page).toHaveURL(/#\/review$/);
  await expect(main.getByText(/Please check: Ramesh Kumar/)).toBeVisible();
  await expect(main.getByText(/₹1,20,000/).first()).toBeVisible();
  await expect(main.getByText("Nothing is submitted without your yes.")).toBeVisible();
  await expect(main.locator(".docs-missing-box")).toHaveCount(0);

  // 7. Edit the income on the review screen: a new read-back, still nothing submitted
  const income = page.locator("li.field-row", { hasText: "Annual income" });
  await income.getByRole("button", { name: /Edit Annual income/ }).click();
  await income.getByLabel("Annual income").fill("150000");
  await income.getByRole("button", { name: /Save/ }).click();
  await expect(main.getByText(/income ₹1,50,000/).first()).toBeVisible();
  await expect(page).toHaveURL(/#\/review$/);

  // 8. ಹೌದು, ಸಲ್ಲಿಸಿ -> My applications with the application ID and the next schemes
  await page.getByRole("button", { name: /Yes, submit/ }).click();
  await expect(page).toHaveURL(/#\/applications$/);
  const app = page.locator("article.app", { hasText: "Senior Citizen Pension Scheme" });
  await expect(app.locator(".app-id code")).toHaveText(/^YJS-[0-9A-Z]{10}$/);
  await expect(app.locator(".timeline")).toContainText("You said yes");
  await expect(app.locator(".timeline")).toContainText("Submitted with your yes");
  await expect(page.getByText("You can also apply for")).toBeVisible();

  // The case survives a reload (server-side case memory, same session token)
  const appId = await app.locator(".app-id code").textContent();
  await page.reload();
  await expect(page.locator("article.app .app-id code")).toHaveText(appId);

  // 9. Delete my data (privacy section of the Profile screen)
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await page.getByRole("button", { name: /Delete my data/ }).click();
  await page.getByRole("button", { name: /Yes, delete everything/ }).click();
  await expect(page.getByText(/Your data was deleted/)).toBeVisible();
  await page.goto("/#/applications");
  await expect(page.getByText("No applications yet.")).toBeVisible();
});
