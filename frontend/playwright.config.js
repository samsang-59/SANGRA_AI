import { defineConfig, devices } from "@playwright/test";

// Browser tests. "dev" runs against `npm run dev`, "preview" against the production build (`npm run build` first).
// Firefox runs when it is installed (E2E_FIREFOX=1); this build machine only has Chromium.
const projects = [
  { name: "dev-chromium", use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:5173" } },
  { name: "preview-chromium", use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:4173" } },
];
if (process.env.E2E_FIREFOX === "1") {
  projects.push({ name: "preview-firefox", use: { ...devices["Desktop Firefox"], baseURL: "http://localhost:4173" } });
}

export default defineConfig({
  testDir: "tests/e2e",
  reporter: "list",
  projects,
  webServer: [
    { command: "npm run dev", url: "http://localhost:5173", reuseExistingServer: false, timeout: 60_000 },
    { command: "npm run preview", url: "http://localhost:4173", reuseExistingServer: false, timeout: 60_000 },
  ],
});
