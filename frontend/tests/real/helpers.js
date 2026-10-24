// Helpers for the browser tests against the real backend (run with `npm run test:real`).
import { readFileSync } from "node:fs";
import { expect } from "@playwright/test";

export const API = `${process.env.REAL_ORIGIN}/api/v1`;
export const FILE = "src/main/java/shop/Discount.java";
export const BUG = "The summer sale shows 5% discount, it should be 10%.";
export const PASSWORD = "Str0ng-pass-1";

/** Restarts the backend with this agent script (a new process starts the script from its first step). */
export async function restartBackend(script) {
  const response = await fetch(`${process.env.REAL_CONTROL}/restart`, { method: "POST", body: JSON.stringify(script) });
  if (!response.ok) throw new Error(await response.text());
}

export async function stopBackend() {
  await fetch(`${process.env.REAL_CONTROL}/stop`, { method: "POST" });
}

export async function startBackend() {
  const response = await fetch(`${process.env.REAL_CONTROL}/start`, { method: "POST" });
  if (!response.ok) throw new Error(await response.text());
}

// The scripted agent's steps.
export const search = (query, seconds) => ({ tool: "search_files", args: { query }, ...(seconds ? { seconds } : {}) });
export const notFound = { answer: { outcome: "not_found", findings: [], summary: "nothing found" } };
export const found = { answer: { outcome: "found", summary: "summerRate returns 0.05", findings: [
  { file_path: FILE, start_line: 3, end_line: 6, note: "the rate" }] } };
export const fix = (rate, explanation = `The summer rate must be ${rate}.`) => ({ answer: { explanation, edits: [
  { file_path: FILE, start_line: 5, end_line: 5, old_code: "        return 0.05;", new_code: `        return ${rate};`,
    reason: "the summer rate" }] } });
export const tests = (...statuses) => statuses.map((s) => (typeof s === "string"
  ? { status: s, exit_code: s === "passed" ? 0 : 1, log: `tests ${s}` } : s));

export function mailLink(email, kind = "verify-email") {
  const mails = readFileSync(process.env.REAL_MAIL_FILE, "utf8").trim().split("\n").map((line) => JSON.parse(line));
  const mail = mails.filter((m) => m.to === email).at(-1);
  return new RegExp(`/${kind}\\?token=[A-Za-z0-9_-]+`).exec(mail.body)[0];
}

let counter = 0;
/** Registers a new user in the page, verifies the email by the link in the test mailer; returns the email. */
export async function newVerifiedUser(page, name = "Real Test") {
  counter += 1;
  const email = `real-${Date.now()}-${counter}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "create account" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.goto(mailLink(email));
  await expect(page.getByRole("heading", { name: "Email verified" })).toBeVisible();
  return email;
}

/** Calls the API with the page's token (to set up data quickly). */
export function apiCall(page, method, path, body) {
  return page.evaluate(async ({ url, method: verb, data }) => {
    const response = await fetch(url, { method: verb, headers: { "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("sangra_token")}` }, body: data ? JSON.stringify(data) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { url: `${API}${path}`, method, data: body });
}

export async function localProject(page, name = "summer-sale-service") {
  const created = await apiCall(page, "POST", "/projects", { name, language: "java", source_type: "local_path",
    source_reference: process.env.REAL_PROJECT_DIR, test_command: "mvn test" });
  return created.body.data.id;
}

/** Starts a run from the start page; returns the run's id. */
export async function startRun(page, projectId) {
  await page.goto(`/projects/${projectId}/run`);
  await page.getByLabel("Describe the bug in plain English").fill(BUG);
  await page.getByRole("button", { name: /start run/ }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  return Number(page.url().split("/").pop());
}

export const heading = (page, name) => page.getByRole("heading", { name });
