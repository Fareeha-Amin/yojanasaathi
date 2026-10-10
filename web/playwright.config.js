// End-to-end test of the text path (npm run e2e). Starts its own agent on :8010 with a
// throwaway database (<db>_e2e_test, no LLM; e2e/agent_server.py) and a Vite dev server on
// :5180 proxied to it. Uses the installed Google Chrome (channel "chrome"): no browser download.
import { defineConfig } from "@playwright/test";

const PY = process.platform === "win32" ? "..\\.venv\\Scripts\\python.exe" : "../.venv/bin/python";
const AGENT_PORT = 8010;
const WEB_PORT = 5180;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    channel: "chrome",
    viewport: { width: 390, height: 844 }, // phone first
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${PY} e2e/agent_server.py`,
      url: `http://127.0.0.1:${AGENT_PORT}/health`,
      env: { E2E_AGENT_PORT: String(AGENT_PORT) },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      env: { AGENT_URL: `http://127.0.0.1:${AGENT_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
