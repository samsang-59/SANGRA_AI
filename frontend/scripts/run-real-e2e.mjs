// Browser tests against the real backend (FE5-F11, FE6, FE7-F03, E01-E08, X01): starts the backend in its test setup
// (scripts/backend_harness.mjs), builds the app pointed at it (dist-real), serves the build and runs tests/real with
// Playwright. A small control address lets each test restart the backend with its own agent script, or stop and
// start it. At the end the backend is stopped and its database dropped.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { startTestBackend } from "./backend_harness.mjs";

const FRONTEND = "http://localhost:4180";
const backend = await startTestBackend({ corsOrigins: FRONTEND, frontendUrl: FRONTEND,
  agentScript: { llm: [{ answer: { outcome: "not_found", findings: [] } }], tests: [{ status: "passed" }] } });

// POST /restart with { llm, tests } as the body, POST /stop, POST /start.
const control = createServer((request, response) => {
  let body = "";
  request.on("data", (chunk) => { body += chunk; });
  request.on("end", async () => {
    try {
      if (request.url === "/restart") await backend.restart(body ? JSON.parse(body) : null);
      else if (request.url === "/stop") await backend.stopProcess();
      else if (request.url === "/start") await backend.startProcess();
      else throw new Error(`unknown control ${request.url}`);
      response.writeHead(200).end("ok");
    } catch (error) {
      response.writeHead(500).end(String(error));
    }
  });
});
await new Promise((resolve) => control.listen(0, "127.0.0.1", resolve));

let code = 1;
try {
  const env = { ...process.env, VITE_API_URL: `${backend.origin}/api/v1`, REAL_ORIGIN: backend.origin,
    REAL_MAIL_FILE: backend.mailFile, REAL_PROJECT_DIR: backend.project, REAL_ZIP: backend.zipFile,
    REAL_CONTROL: `http://127.0.0.1:${control.address().port}` };
  // Not spawnSync: the control address must keep answering while the tests run.
  const run = (args) => new Promise((resolve) => {
    spawn("npx", args, { stdio: "inherit", env }).once("exit", (status) => resolve(status ?? 1));
  });
  if (await run(["vite", "build", "--outDir", "dist-real", "--emptyOutDir"]) === 0) {
    code = await run(["playwright", "test", "--config", "playwright.real.config.js", ...process.argv.slice(2)]);
  }
} finally {
  control.close();
  await backend.stop();
}
process.exit(code);
