// Screenshot run for the design review (npm run shots). No backend needed: the spec stubs
// the agent API with the same fixtures the unit tests use, and only Vite serves the app.
// Uses the installed Google Chrome (channel "chrome"): no browser download.
import { defineConfig } from "@playwright/test";

const WEB_PORT = 5190;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "screenshots.spec.js",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    channel: "chrome",
  },
  webServer: [
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
