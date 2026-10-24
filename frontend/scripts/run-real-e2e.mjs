// Browser tests against the real backend (FE5-F11, later FE7): starts the backend in its test setup with a scripted
// agent (scripts/backend_harness.mjs), builds the app pointed at it (dist-real), serves the build and runs
// tests/real with Playwright. Then stops the backend and drops its database.
import { spawnSync } from "node:child_process";
import { startTestBackend } from "./backend_harness.mjs";

const FRONTEND = "http://localhost:4180";
const FILE = "src/main/java/shop/Discount.java";
const fix = (rate, explanation) => ({ answer: { explanation, edits: [{ file_path: FILE, start_line: 5, end_line: 5,
  old_code: "        return 0.05;", new_code: `        return ${rate};`, reason: "the summer rate" }] } });

// FE5-F11: nothing found -> a hint -> found -> a fix that is rejected -> a second fix that is accepted -> tests pass.
const backend = await startTestBackend({ corsOrigins: FRONTEND, frontendUrl: FRONTEND, agentScript: {
  llm: [
    { tool: "search_files", args: { query: "rebate" } },
    { answer: { outcome: "not_found", findings: [], summary: "no rebate code" } },
    { tool: "search_files", args: { query: "summerRate" } },
    { answer: { outcome: "found", summary: "summerRate returns 0.05", findings: [
      { file_path: FILE, start_line: 3, end_line: 6, note: "the rate" }] } },
    fix("0.15", "The summer rate should be 15%."),
    fix("0.10", "The summer rate must be 0.10."),
  ],
  tests: [{ status: "failed", exit_code: 1, log: "1 test failed" }, { status: "passed", exit_code: 0, log: "ok" }],
} });
let code = 1;
try {
  const env = { ...process.env, VITE_API_URL: `${backend.origin}/api/v1`, REAL_ORIGIN: backend.origin,
    REAL_MAIL_FILE: backend.mailFile, REAL_PROJECT_DIR: backend.project };
  const build = spawnSync("npx", ["vite", "build", "--outDir", "dist-real", "--emptyOutDir"], { stdio: "inherit", env });
  if (build.status === 0) {
    const result = spawnSync("npx", ["playwright", "test", "--config", "playwright.real.config.js"], { stdio: "inherit", env });
    code = result.status ?? 1;
  }
} finally {
  await backend.stop();
}
process.exit(code);
