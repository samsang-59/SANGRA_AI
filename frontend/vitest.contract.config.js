import { defineConfig } from "vitest/config";

// Tests against the real backend (FE1-F11, FE1-F30). Run them with `npm run test:contract`, which starts a backend
// in its test setup (scripted agent, emails to a file) and points VITE_API_URL at it.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/contract/**/*.test.js"],
    testTimeout: 60_000,
  },
});
