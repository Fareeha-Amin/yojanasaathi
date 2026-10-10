// Screenshots of every screen in Kannada and English at three widths, for the side-by-side
// check against docs/design/YojanaSaathi UI.pdf. The agent API is stubbed with the unit-test
// fixtures (no backend, no DB, no voice): the browser only ever talks to Vite.
//
//   npx playwright test -c playwright.screenshots.config.js
//
// Output: web/screenshots/<screen>-<lang>-<viewport>.png (gitignored).
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const fixture = (name) =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../src/test/fixtures/${name}.json`, import.meta.url)), "utf8"));

const SHOT_DIR = fileURLToPath(new URL("../screenshots", import.meta.url));

const VIEWPORTS = [
  { name: "360x780", width: 360, height: 780 },
  { name: "390x844", width: 390, height: 844 },
  { name: "1366x768", width: 1366, height: 768 },
];

const SCREENS = [
  { name: "landing", route: "", data: () => fixture("interview") },
  { name: "talk", route: "talk", data: () => fixture("interview") },
  { name: "schemes", route: "schemes", data: () => fixture("eligible") },
  {
    name: "documents", route: "documents",
    // consented, so the pinned missing item and the Take photo / Choose file actions show
    data: () => { const s = fixture("eligible"); s.consent = { ...s.consent, documents: true }; return s; },
  },
  {
    name: "prefill", route: "prefill",
    // paused on an OTP: the step list and the "your turn" card, the portal's masked phone
    data: () => { const s = fixture("eligible"); s.pause = { type: "otp", masked_phone: "+91 •••••• 4321" }; return s; },
  },
  { name: "review", route: "review", data: () => fixture("review") },
  { name: "applications", route: "applications", data: () => fixture("submitted") },
  { name: "profile", route: "profile", data: () => fixture("submitted") },
];

for (const vp of VIEWPORTS) {
  for (const lang of ["kn", "en"]) {
    for (const screen of SCREENS) {
      test(`${screen.name} · ${lang} · ${vp.name}`, async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
        await context.addInitScript((l) => {
          try {
            localStorage.setItem("ys.lang", l);
            localStorage.removeItem("ys.session");
          } catch {
            /* about:blank has no storage */
          }
        }, lang);
        const page = await context.newPage();

        const data = screen.data();
        await page.route("**/api/**", (route) => {
          const url = route.request().url();
          if (url.includes("/session")) return route.fulfill({ json: { case_id: "shot-case", token: "shot-token" } });
          if (url.includes("/summary")) return route.fulfill({ json: data });
          return route.fulfill({ status: 404, json: { detail: "not stubbed" } });
        });

        await page.goto(`/#/${screen.route}`);
        await page.waitForSelector(".topbar");
        // Give the webfonts a moment to settle, but never hang on a network-less machine.
        await page.evaluate(() =>
          Promise.race([document.fonts ? document.fonts.ready : Promise.resolve(), new Promise((r) => setTimeout(r, 2500))]),
        );
        await page.waitForTimeout(250);
        await page.screenshot({ path: `${SHOT_DIR}/${screen.name}-${lang}-${vp.name}.png`, fullPage: true });

        // "No horizontal scroll at 360px": assert it on the narrow viewports (the screenshot
        // is already saved above, so a failure here still leaves the image for review).
        if (vp.width <= 390) {
          const o = await page.evaluate(() => {
            const w = window.innerWidth;
            const bad = [];
            for (const el of document.querySelectorAll("*")) {
              const r = el.getBoundingClientRect();
              if (r.width === 0 && r.height === 0) continue;
              if (r.right > w + 1) bad.push(`${el.tagName.toLowerCase()}.${el.className}`.slice(0, 70));
            }
            return { scrollWidth: document.documentElement.scrollWidth, w, bad: bad.slice(0, 8) };
          });
          expect(o.scrollWidth, `horizontal overflow at ${vp.width}px: ${o.bad.join(" | ")}`).toBeLessThanOrEqual(o.w + 1);
        }
        await context.close();
      });
    }
  }
}
