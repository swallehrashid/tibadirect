import { defineConfig } from "@playwright/test";

/**
 * End-to-end and real-browser checks. They expect the stack to be running (see README: infrastructure,
 * migrations, seed data, the API on port 5000 and the Vite dev server, or the production build on one port).
 * Locally the installed Chrome is used; in CI set E2E_BROWSER_CHANNEL="" and install Playwright's Chromium.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    channel: process.env.E2E_BROWSER_CHANNEL === "" ? undefined : (process.env.E2E_BROWSER_CHANNEL ?? "chrome"),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
