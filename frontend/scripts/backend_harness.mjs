// Starts the real backend in its test setup (Gaps #10: scripted agent and fake test runner, emails written to a file)
// for the contract tests and the real-backend browser tests. Needs the backend's virtual environment and PostgreSQL:
// a fresh database is created next to TEST_DATABASE_URL (default: the backend tests' one), migrated, and dropped by
// stop(). The project is the Amazon discount example, as a local folder and as a zip. The process can be stopped,
// started and restarted with a new agent script on the same address and database (FE7-E06, FE7-X01).
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

const BACKEND = new URL("../../backend/", import.meta.url).pathname;
const PYTHON = join(BACKEND, ".venv/bin/python");
const ADMIN = "import os, psycopg\nconn = psycopg.connect(os.environ['BASE_DB'], autocommit=True)\n";
const ZIP = "import os, sys, zipfile\nroot, target = sys.argv[1], sys.argv[2]\n" +
  "with zipfile.ZipFile(target, 'w') as z:\n    for folder, _, files in os.walk(root):\n" +
  "        for name in files:\n            path = os.path.join(folder, name)\n" +
  "            z.write(path, os.path.relpath(path, root))\n";

export const freePort = () => new Promise((resolve) => {
  const server = createServer().listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => resolve(port));
  });
});

/** agentScript: the AGENT_FAKE_SCRIPT content ({ llm: [...], tests: [...] }). corsOrigins: the frontend origins. */
export async function startTestBackend({ agentScript, corsOrigins = "http://localhost:5173",
  frontendUrl = "http://localhost:5173" }) {
  const baseDatabase = process.env.TEST_DATABASE_URL || "postgresql://sangra:sangra_pw_7Hq2xZ@localhost:5432/sangra_test";
  const work = mkdtempSync(join(tmpdir(), "sangra-backend-"));
  const dbName = `sangra_contract_${randomBytes(5).toString("hex")}`;
  const database = baseDatabase.replace(/\/[^/?]+(\?|$)/, `/${dbName}$1`);
  const python = (code, args = []) => {
    const result = spawnSync(PYTHON, ["-c", code, ...args], { cwd: BACKEND, encoding: "utf8",
      env: { ...process.env, PYTHONPATH: BACKEND, BASE_DB: baseDatabase, DB_NAME: dbName } });
    if (result.status !== 0) throw new Error(result.stderr);
  };
  const dropDatabase = () => python(`${ADMIN}conn.execute('DROP DATABASE IF EXISTS "' + os.environ['DB_NAME'] + '" WITH (FORCE)')`);

  python(`${ADMIN}conn.execute('CREATE DATABASE "' + os.environ['DB_NAME'] + '"')`);
  const migrated = spawnSync(PYTHON, ["-m", "app.migrate"], { cwd: work, encoding: "utf8",
    env: { PATH: process.env.PATH, PYTHONPATH: BACKEND, DATABASE_URL: database } });
  if (migrated.status !== 0) {
    dropDatabase();
    throw new Error(`migration failed: ${migrated.stderr}`);
  }

  const project = join(work, "shop");
  mkdirSync(join(project, "src/main/java/shop"), { recursive: true });
  writeFileSync(join(project, "src/main/java/shop/Discount.java"),
    "package shop;\n\npublic class Discount {\n    public static double summerRate() {\n        return 0.05;\n    }\n}\n");
  // A file at the top too: a zip whose files all sit in one top folder has that folder removed when unpacked.
  writeFileSync(join(project, "pom.xml"), "<project><modelVersion>4.0.0</modelVersion><groupId>shop</groupId>" +
    "<artifactId>shop</artifactId><version>1</version></project>\n");
  const zipFile = join(work, "shop.zip");
  python(ZIP, [project, zipFile]);
  const script = join(work, "agent.json");
  writeFileSync(script, JSON.stringify(agentScript));

  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const mailFile = join(work, "mail.jsonl");
  const env = {
    PATH: process.env.PATH, HOME: process.env.HOME, APP_MODE: "local", DATABASE_URL: database,
    GROQ_API_KEY: "gsk_contract_test_not_used", GROQ_MODEL: "openai/gpt-oss-120b",
    JWT_SECRET: randomBytes(32).toString("hex"), FRONTEND_URL: frontendUrl, CORS_ORIGINS: corsOrigins,
    WORKSPACES_DIR: join(work, "workspaces"), PACKAGES_DIR: join(work, "packages"), PORT: String(port),
    LOG_LEVEL: "WARNING", BCRYPT_ROUNDS: "4", STREAM_POLL_SECONDS: "0.1", AGENT_TEST_MODE: "fake",
    AGENT_FAKE_SCRIPT: script, MAILER_MODE: "file", MAIL_FILE: mailFile, DOCKER_HOST: "unix:///nonexistent/docker.sock",
    PYTHONPATH: BACKEND,
  };

  let child = null;
  let exited = null;
  async function startProcess() {
    child = spawn(PYTHON, ["-m", "app"], { cwd: work, env, stdio: ["ignore", "inherit", "inherit"] });
    exited = new Promise((resolve) => child.once("exit", resolve));
    for (let i = 0; i < 150; i += 1) {
      try {
        if ((await fetch(`${origin}/api/v1/health`)).ok) return;
      } catch {
        // not up yet
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new Error("The backend did not start (is PostgreSQL up and migrated?)");
  }
  async function stopProcess() {
    if (!child) return;
    child.kill("SIGTERM");
    await exited;
    child = null;
  }
  async function restart(newScript) {
    await stopProcess();
    if (newScript) writeFileSync(script, JSON.stringify(newScript));
    await startProcess(); // a new process also starts the agent script from its first step
  }
  const stop = async () => {
    await stopProcess();
    dropDatabase();
  };

  try {
    await startProcess();
  } catch (error) {
    await stop();
    throw error;
  }
  return { origin, mailFile, project, zipFile, work, stop, stopProcess, startProcess, restart };
}
