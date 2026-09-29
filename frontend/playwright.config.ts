import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a RUNNING app (API + web); see e2e/README.md for how to start both and seed data.
//   E2E_BASE_URL      web app under test (default http://127.0.0.1:3103)
//   PW_CHROMIUM_PATH  use a preinstalled Chromium instead of Playwright's download (optional)
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3103",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { executablePath },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], launchOptions: { executablePath } } }],
});
