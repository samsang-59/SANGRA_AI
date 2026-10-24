// FE1 against the real backend: F11 (every backend route has a function, and every function a real route) and
// F30 (login, me, list projects, start a scripted run, stream it to the end). Run with `npm run test:contract`.
import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { API_URL } from "../../src/config.js";
import * as auth from "../../src/api/auth.js";
import * as projects from "../../src/api/projects.js";
import * as runs from "../../src/api/runs.js";
import { openRunStream } from "../../src/api/stream.js";
import { clearToken, setToken } from "../../src/api/token.js";
import { installStorage } from "../unit/api/helpers.js";
import { ROUTE_TABLE } from "../unit/api/routes_table.js";

const ORIGIN = process.env.CONTRACT_ORIGIN;

beforeAll(() => {
  if (!ORIGIN) throw new Error("Run these tests with `npm run test:contract` (it starts the backend).");
  installStorage();
  clearToken();
});

const shape = (path) => path.split("?")[0].replace(/\{[^}]+\}/g, "{}").replace(/\/\d+(?=\/|$)/g, "/{}");

describe("FE1-F11: the routes match the backend's own description (/openapi.json)", () => {
  it("every backend route has a function and every function a real route", async () => {
    const description = await (await fetch(`${ORIGIN}/openapi.json`)).json();
    const backend = new Set();
    for (const [path, methods] of Object.entries(description.paths)) {
      for (const method of Object.keys(methods)) backend.add(`${method.toUpperCase()} ${shape(path.replace("/api/v1", ""))}`);
    }
    const ours = new Set(ROUTE_TABLE.map(([, , method, path]) => `${method} ${shape(path)}`));
    ours.add("GET /runs/{}/events"); // the live stream (stream.js)
    backend.delete("GET /health"); // read by people and monitors, not by the app (no function in F2.2)
    expect([...ours].sort()).toEqual([...backend].sort());
    expect(ours.size).toBe(23);
  });
});

describe("FE1-F30: a whole run against the real backend", () => {
  it("register, verify, login, me, projects, start a scripted run, stream it to the end, download", async () => {
    const email = `contract-${Date.now()}@example.com`;
    const registered = await auth.register("Contract Test", email, "Str0ng-pass-1");
    setToken(registered.token);
    const mail = readFileSync(process.env.CONTRACT_MAIL_FILE, "utf8").trim().split("\n").map((l) => JSON.parse(l))
      .find((m) => m.to === email);
    const token = /verify-email\?token=([A-Za-z0-9_-]+)/.exec(mail.body)[1];
    await auth.verifyEmail(token);
    const loggedIn = await auth.login(email, "Str0ng-pass-1");
    setToken(loggedIn.token);
    const me = await auth.getMe();
    expect([me.email, me.is_email_verified]).toEqual([email, true]);

    const project = await projects.createProject({ name: "Shop", language: "java", source_type: "local_path",
      source_reference: process.env.CONTRACT_PROJECT_DIR, test_command: "mvn -q test" });
    expect((await projects.listProjects()).items.map((p) => p.id)).toEqual([project.id]);
    const run = await runs.startRun(project.id, "The summer sale shows 5% discount, it should be 10%.");
    expect((await runs.listRuns({ status: "active" })).items.map((r) => r.id)).toEqual([run.id]);

    const events = [];
    let approved = false;
    await new Promise((resolve, reject) => {
      openRunStream(run.id, (event) => {
        events.push(event);
        if (event.event_type === "approval_requested" && !approved) {
          approved = true;
          runs.getRun(run.id).then((detail) =>
            runs.submitApproval(run.id, detail.attempts.at(-1).id, "accepted")).catch(reject);
        }
      }, { onEnd: resolve, onError: reject });
    });
    expect(events.map((e) => e.sequence_number)).toEqual(events.map((_, i) => i + 1));
    expect(events.at(-1)).toMatchObject({ event_type: "status_changed", details: { status: "succeeded" } });

    const detail = await runs.getRun(run.id);
    expect(detail.status).toBe("succeeded");
    let saved = null;
    const download = await runs.downloadPackage(run.id, { save: (blob, name) => (saved = { blob, name }) });
    expect(download.name).toBe(`sangra-run-${run.id}.zip`);
    expect(new Uint8Array(await saved.blob.slice(0, 2).arrayBuffer())).toEqual(new Uint8Array([80, 75]));
    expect(API_URL).toBe(`${ORIGIN}/api/v1`);
    console.log(`FE1-F30: run ${run.id} streamed ${events.length} events to "succeeded"; package ${download.size} bytes`);
  });
});
