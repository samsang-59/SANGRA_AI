import { defineConfig, devices } from "@playwright/test";

// Browser tests against the real backend. Run them with `npm run test:real`: it starts the backend, builds the app
// pointed at it (dist-real) and then runs this config.
export default defineConfig({
  testDir: "tests/real",
  reporter: "list",
  timeout: 180_000,
  workers: 1, // the tests share one backend and restart it with their own agent script
  projects: [{ name: "real-chromium", use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:4180" } }],
  webServer: [{ command: "npx vite preview --outDir dist-real --port 4180 --strictPort", url: "http://localhost:4180",
    reuseExistingServer: false, timeout: 60_000 }],
});
