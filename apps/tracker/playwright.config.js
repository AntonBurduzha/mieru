import { defineConfig, devices } from "@playwright/test";

// Every test loads the built dist/p.js from test/server.mjs, so it checks what ships.
// No retries: a flaky test is a bug (conventions section 6.6).
export default defineConfig({
  testDir: "test",
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5180" },
  webServer: {
    command: "node test/server.mjs --port 5180",
    url: "http://127.0.0.1:5180/page.html",
    reuseExistingServer: false,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
