import { defineConfig, devices } from "@playwright/test";

import { stack } from "./support/stack.ts";

// The journeys share one stack and one address, and the proxy limits sign-ins by address, so they run
// one after another. Chromium only.
export default defineConfig({
  testDir: "tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env["CI"],
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: process.env["CI"] ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    baseURL: stack.baseURL,
    // The stack's certificate is self-signed.
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    locale: "uz-UZ",
    timezoneId: "Asia/Tashkent",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
