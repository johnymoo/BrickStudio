// =============================================================================
// playwright.config.ts — root-level E2E test runner for the full stack.
//
// These tests run against a *running* blocktool stack — bring it up first:
//
//   bash scripts/up.sh           # full stack via docker compose
//   # or, in a second shell, start only the bits you need locally.
//
// In CI / when `BLOCKTOOL_E2E_BASE_URL` is set, the test will use that URL
// (e.g. http://localhost:80 if going through Caddy, or http://localhost:5173
// if going through Vite's dev proxy).
//
// Notes:
//   - We run a single chromium worker. The full-flow test is timing-sensitive
//     (it waits for a 3D reconstruction job to complete) and the API only
//     has one Celery worker; running more than one browser in parallel would
//     make the first jobs wait on each other.
//   - `webServer` is intentionally NOT used: docker compose owns the
//     lifecycle of the stack, and we want the tests to fail loudly if the
//     stack isn't already up.
// =============================================================================
import { defineConfig, devices } from "@playwright/test";

const BASE_URL =
  process.env.BLOCKTOOL_E2E_BASE_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  "http://localhost:80"; // Caddy proxy port; the README default.

const HEADLESS = process.env.PLAYWRIGHT_HEADED !== "1";

export default defineConfig({
  testDir: "./tests/e2e",
  // Match the brief: the "full-flow" test runs the upload → 3D render
  // pipeline end to end. The default Playwright timeout is 30s; the brief
  // allows up to 10 minutes for the 3D reconstruction to finish.
  timeout: 10 * 60 * 1000,
  expect: {
    // Default expect timeout of 5s is too short for the SSE-driven
    // status changes; bump it for the full-flow test to use.
    timeout: 30 * 1000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [["list"], ["github"]]
    : [["list"], ["html", { open: "never", outputFolder: "tests/e2e/playwright-report" }]],
  outputDir: "tests/e2e/test-results",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    headless: HEADLESS,
    // Most staging test environments have slow DB/3D-pipeline round trips.
    actionTimeout: 30 * 1000,
    navigationTimeout: 60 * 1000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
