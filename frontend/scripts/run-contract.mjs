// Starts the real backend in its test setup (scripts/backend_harness.mjs), runs the contract tests against it, then
// stops it and drops its database.
import { spawnSync } from "node:child_process";
import { startTestBackend } from "./backend_harness.mjs";

const backend = await startTestBackend({ agentScript: {
  llm: [
    { tool: "search_files", args: { query: "discount" } },
    { answer: { outcome: "found", summary: "summerRate returns 0.05", findings: [
      { file_path: "src/main/java/shop/Discount.java", start_line: 3, end_line: 6, note: "the rate" }] } },
    { answer: { explanation: "The summer rate must be 0.10.", edits: [
      { file_path: "src/main/java/shop/Discount.java", start_line: 5, end_line: 5, old_code: "        return 0.05;",
        new_code: "        return 0.10;", reason: "the rate" }] } },
  ],
  tests: [{ status: "failed", exit_code: 1, log: "1 test failed" }, { status: "passed", exit_code: 0, log: "ok" }],
} });
let code = 1;
try {
  const result = spawnSync("npx", ["vitest", "run", "--config", "vitest.contract.config.js", "--silent=false"], {
    stdio: "inherit",
    env: { ...process.env, VITE_API_URL: `${backend.origin}/api/v1`, CONTRACT_ORIGIN: backend.origin,
      CONTRACT_MAIL_FILE: backend.mailFile, CONTRACT_PROJECT_DIR: backend.project },
  });
  code = result.status ?? 1;
} finally {
  await backend.stop();
}
process.exit(code);
