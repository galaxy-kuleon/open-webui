import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for Open WebUI E2E tests.
 *
 * Design: plain data config — no classes, no inheritance.
 * Only Chromium — Firefox/WebKit not needed for this project.
 *
 * Environment variables:
 *   HEADED=1    — run tests in headed mode (visible browser)
 *   BASE_URL    — override the default base URL (default: http://localhost:8083)
 */
export default defineConfig({
  testDir: "./tests",

  /* 60 seconds per test — generous for network-dependent UI */
  timeout: 60_000,

  /* Expect assertions timeout */
  expect: {
    timeout: 10_000,
  },

  /* No parallel by default — E2E tests share server state */
  fullyParallel: false,

  /* Fail fast: stop after first failure in CI */
  retries: 0,

  /* Single worker — sequential execution for predictable state */
  workers: 1,

  /* HTML reporter for local debugging, line reporter for CI */
  reporter: [["html", { open: "never" }]],

  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8083",

    /* Headed mode via env var for debugging */
    headless: process.env.HEADED !== "1",

    /* Screenshots on failure for diagnostics */
    screenshot: "only-on-failure",

    /* Trace on first retry (useful when retries > 0) */
    trace: "on-first-retry",

    /* Viewport — standard desktop */
    viewport: { width: 1280, height: 720 },
  },

  /* Output directories */
  outputDir: "./test-results",

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
