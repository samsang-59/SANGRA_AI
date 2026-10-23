// Starts the real backend in its test setup (Gaps #10: scripted agent and fake test runner, emails written to a file),
// runs the contract tests against it, then stops it. Needs the backend's virtual environment and PostgreSQL: a fresh
// database is created next to TEST_DATABASE_URL (default: the backend tests' one), migrated, and dropped at the end.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

const BACKEND = new URL("../../backend/", import.meta.url).pathname;
const baseDatabase = process.env.TEST_DATABASE_URL || "postgresql://sangra:sangra_pw_7Hq2xZ@localhost:5432/sangra_test";
const work = mkdtempSync(join(tmpdir(), "sangra-contract-"));
const PYTHON = join(BACKEND, ".venv/bin/python");
const dbName = `sangra_contract_${randomBytes(5).toString("hex")}`;
const database = baseDatabase.replace(/\/[^/?]+(\?|$)/, `/${dbName}$1`);

function python(code, extraEnv = {}) {
  const result = spawnSync(PYTHON, ["-c", code], { cwd: BACKEND, encoding: "utf8",
    env: { ...process.env, PYTHONPATH: BACKEND, BASE_DB: baseDatabase, DB_NAME: dbName, ...extraEnv } });
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stdout;
}

const ADMIN = "import os, psycopg\nconn = psycopg.connect(os.environ['BASE_DB'], autocommit=True)\n";
python(`${ADMIN}conn.execute('CREATE DATABASE "' + os.environ['DB_NAME'] + '"')`);
const migrated = spawnSync(PYTHON, ["-m", "app.migrate"], { cwd: work, encoding: "utf8",
  env: { PATH: process.env.PATH, PYTHONPATH: BACKEND, DATABASE_URL: database } });
if (migrated.status !== 0) throw new Error(`migration failed: ${migrated.stderr}`);

const freePort = () => new Promise((resolve) => {
  const server = createServer().listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => resolve(port));
  });
});

// The Amazon discount example, as a local folder.
const project = join(work, "shop");
mkdirSync(join(project, "src/main/java/shop"), { recursive: true });
writeFileSync(join(project, "src/main/java/shop/Discount.java"),
  "package shop;\n\npublic class Discount {\n    public static double summerRate() {\n        return 0.05;\n    }\n}\n");
const script = join(work, "agent.json");
writeFileSync(script, JSON.stringify({
  llm: [
    { tool: "search_files", args: { query: "discount" } },
    { answer: { outcome: "found", summary: "summerRate returns 0.05", findings: [
      { file_path: "src/main/java/shop/Discount.java", start_line: 3, end_line: 6, note: "the rate" }] } },
    { answer: { explanation: "The summer rate must be 0.10.", edits: [
      { file_path: "src/main/java/shop/Discount.java", start_line: 5, end_line: 5, old_code: "        return 0.05;",
        new_code: "        return 0.10;", reason: "the rate" }] } },
  ],
  tests: [{ status: "failed", exit_code: 1, log: "1 test failed" }, { status: "passed", exit_code: 0, log: "ok" }],
}));

const port = await freePort();
const mailFile = join(work, "mail.jsonl");
const env = {
  PATH: process.env.PATH, HOME: process.env.HOME, APP_MODE: "local", DATABASE_URL: database,
  GROQ_API_KEY: "gsk_contract_test_not_used", GROQ_MODEL: "openai/gpt-oss-120b",
  JWT_SECRET: randomBytes(32).toString("hex"), FRONTEND_URL: "http://localhost:5173",
  CORS_ORIGINS: "http://localhost:5173", WORKSPACES_DIR: join(work, "workspaces"), PACKAGES_DIR: join(work, "packages"),
  PORT: String(port), LOG_LEVEL: "WARNING", BCRYPT_ROUNDS: "4", STREAM_POLL_SECONDS: "0.1",
  AGENT_TEST_MODE: "fake", AGENT_FAKE_SCRIPT: script, MAILER_MODE: "file", MAIL_FILE: mailFile,
  DOCKER_HOST: "unix:///nonexistent/docker.sock",
};
const backend = spawn(join(BACKEND, ".venv/bin/python"), ["-m", "app"], { cwd: work, env: {
  ...env, PYTHONPATH: BACKEND } , stdio: ["ignore", "inherit", "inherit"] });

const origin = `http://127.0.0.1:${port}`;
let healthy = false;
for (let i = 0; i < 100 && !healthy; i += 1) {
  try {
    healthy = (await fetch(`${origin}/api/v1/health`)).ok;
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
let code = 1;
if (!healthy) {
  console.error("The backend did not start (is PostgreSQL up and migrated?)");
} else {
  const result = spawnSync("npx", ["vitest", "run", "--config", "vitest.contract.config.js", "--silent=false"], {
    stdio: "inherit",
    env: { ...process.env, VITE_API_URL: `${origin}/api/v1`, CONTRACT_ORIGIN: origin, CONTRACT_MAIL_FILE: mailFile,
      CONTRACT_PROJECT_DIR: project },
  });
  code = result.status ?? 1;
}
backend.kill("SIGTERM");
await new Promise((resolve) => backend.once("exit", resolve));
python(`${ADMIN}conn.execute('DROP DATABASE IF EXISTS "' + os.environ['DB_NAME'] + '" WITH (FORCE)')`);
process.exit(code);
