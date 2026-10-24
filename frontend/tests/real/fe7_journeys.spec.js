// FE7 journeys against the real backend (scripted agent, run with `npm run test:real`): F03 (the built app with the
// real CORS settings), E01-E08 and X01. Each test restarts the backend with its own agent script.
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import {
  API, BUG, PASSWORD, apiCall, fix, found, heading, localProject, newVerifiedUser, notFound,
  restartBackend, search, startBackend, startRun, stopBackend, tests,
} from "./helpers.js";

test.skip(!process.env.REAL_ORIGIN, "needs the real backend: npm run test:real");
const LONG = 60_000;
const runBanner = (page) => page.getByRole("region", { name: "Run in progress" });

async function cancel(page) {
  await page.getByRole("button", { name: "cancel run" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "cancel run" }).click();
  await expect(heading(page, "You cancelled the run")).toBeVisible({ timeout: LONG });
}

test("FE7-F03: the production build talks to the backend with the real CORS settings", async ({ page }) => {
  await page.goto("/login");
  const fromApp = await page.evaluate(async (url) => (await fetch(url)).status, `${API}/health`);
  expect(fromApp).toBe(200);
  const foreign = await fetch(`${API}/auth/me`, { method: "OPTIONS", headers: { Origin: "http://evil.example",
    "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" } });
  expect(foreign.headers.get("access-control-allow-origin")).toBeNull();
  const ours = await fetch(`${API}/auth/me`, { method: "OPTIONS", headers: { Origin: "http://localhost:4180",
    "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" } });
  expect(ours.headers.get("access-control-allow-origin")).toBe("http://localhost:4180");
});

test("FE7-E01: register, verify, create a zip project, run, approve, success, download", async ({ page }) => {
  await restartBackend({ llm: [search("discount"), found, fix("0.10")], tests: tests("failed", "passed") });
  await newVerifiedUser(page);
  await page.goto("/projects/new");
  await page.getByLabel("Name").fill("shop-zip");
  await page.getByRole("radio", { name: "java" }).check();
  await page.getByRole("radio", { name: "zip" }).check();
  await page.getByLabel("Test command").fill("mvn test");
  await page.getByRole("button", { name: "create project" }).click();
  await expect(heading(page, "Projects")).toBeVisible();
  await page.getByRole("link", { name: /run/i }).first().click();
  await page.getByLabel("Describe the bug in plain English").fill(BUG);
  await page.getByLabel("Code (.zip)").setInputFiles(process.env.REAL_ZIP);
  await page.getByRole("button", { name: /start run/ }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  await expect(runBanner(page)).toBeVisible();
  await expect(heading(page, "Review the proposed fix")).toBeVisible({ timeout: LONG });
  await expect(page.locator(".diff-new")).toContainText("return 0.10;");
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(heading(page, "Tests passed")).toBeVisible({ timeout: LONG });
  await expect(runBanner(page)).toHaveCount(0); // the run ended: the banner goes
  await expect(page.getByRole("button", { name: "apply to my folder" })).toHaveCount(0); // a zip project
  const [download] = await Promise.all([page.waitForEvent("download"),
    page.getByRole("button", { name: "download package" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^sangra-run-\d+\.zip$/);
  expect(readFileSync(await download.path()).subarray(0, 2).toString()).toBe("PK");
});

test("FE7-E02: a hint, a rejected fix, a fix that fails the tests, then a second attempt that passes", async ({ page }) => {
  await restartBackend({ llm: [search("rebate"), notFound, search("summerRate"), found, fix("0.15"), fix("0.12"),
    fix("0.10")], tests: tests("failed", "failed", "passed") });
  await newVerifiedUser(page);
  await startRun(page, await localProject(page));
  await expect(heading(page, "The agent needs a hint")).toBeVisible({ timeout: LONG });
  await page.getByLabel("Your hint").fill("Look at summerRate in Discount.java");
  await page.getByRole("button", { name: "send hint" }).click();
  await expect(page.locator(".diff-new")).toContainText("return 0.15;", { timeout: LONG });
  await page.getByLabel(/Feedback/).fill("Not 15 percent.");
  await page.getByRole("button", { name: "reject" }).click();
  await expect(page.locator(".diff-new")).toContainText("return 0.12;", { timeout: LONG });
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(page.locator(".diff-new")).toContainText("return 0.10;", { timeout: LONG }); // after the test failure
  await expect(page.locator(".info-bar")).toContainText("attempt #3 · after_test_failure");
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(heading(page, "Tests passed")).toBeVisible({ timeout: LONG });
  await expect(page.getByRole("region", { name: "after fix (attempt #3)" })).toContainText("passed");
  await expect(page.getByRole("article", { name: "attempt #2" })).toContainText("reverted");
  await expect(page.getByRole("article", { name: "attempt #2" })).toContainText("tests_failed");
  await expect(page.getByRole("article", { name: "attempt #1" })).toContainText("decision: rejected");
});

// FE7-E03: cancel in each phase that can be held. "applying" passes too quickly to catch from a browser.
const PHASES = [
  ["started", "Preparing the workspace", { llm: [notFound], tests: tests({ status: "failed", seconds: 60 }) }],
  ["searching", "Searching the code for the bug", { llm: [search("discount", 60)], tests: tests("failed") }],
  ["waiting_for_hint", "The agent needs a hint", { llm: [notFound], tests: tests("failed") }],
  ["waiting_for_approval", "Review the proposed fix", { llm: [found, fix("0.10")], tests: tests("failed") }],
  ["testing", "Running the tests in Docker", { llm: [found, fix("0.10")],
    tests: tests("failed", { status: "passed", seconds: 60 }) }],
];
for (const [phase, title, script] of PHASES) {
  test(`FE7-E03: cancel while ${phase}`, async ({ page }) => {
    await restartBackend(script);
    await newVerifiedUser(page);
    await startRun(page, await localProject(page));
    if (phase === "testing") {
      await page.getByRole("button", { name: "accept & test" }).click({ timeout: LONG });
    }
    await expect(heading(page, title)).toBeVisible({ timeout: LONG });
    await cancel(page);
    await expect(page.locator(".run-result .badge")).toContainText("stopped");
    await expect(runBanner(page)).toHaveCount(0);
  });
}

test("FE7-E04: the session ends in the middle of a run: log in again and return to the run", async ({ page, browser }) => {
  await restartBackend({ llm: [found, fix("0.10")], tests: tests("failed", "passed") });
  const email = await newVerifiedUser(page);
  const runId = await startRun(page, await localProject(page));
  await expect(heading(page, "Review the proposed fix")).toBeVisible({ timeout: LONG });
  // The password is changed somewhere else: every older token stops working.
  const other = await browser.newContext();
  const elsewhere = await other.newPage();
  await elsewhere.goto("/login");
  await elsewhere.getByLabel("Email").fill(email);
  await elsewhere.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await elsewhere.getByRole("button", { name: "log in" }).click();
  await expect(heading(elsewhere, "Projects")).toBeVisible();
  await elsewhere.goto("/account");
  await elsewhere.getByLabel("Old password").fill(PASSWORD);
  await elsewhere.getByLabel("New password", { exact: true }).fill("N3w-pass-word");
  await elsewhere.getByLabel("Confirm new password").fill("N3w-pass-word");
  await elsewhere.getByRole("button", { name: "change password" }).click();
  await expect(elsewhere).toHaveURL(/\/login/);
  await other.close();

  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(page).toHaveURL(new RegExp(`/login\\?returnTo=%2Fruns%2F${runId}$`));
  await expect(page.getByText("Session ended, please log in again")).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("N3w-pass-word");
  await page.getByRole("button", { name: "log in" }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await expect(heading(page, "Review the proposed fix")).toBeVisible();
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(heading(page, "Tests passed")).toBeVisible({ timeout: LONG });
});

test("FE7-E05: change password: logged out, and the old token no longer works", async ({ page }) => {
  await newVerifiedUser(page);
  const oldToken = await page.evaluate(() => localStorage.getItem("sangra_token"));
  // Tokens are compared with the change time in whole seconds (Gap #1): a token made in the same second as the
  // change stays valid on purpose. A person never changes the password within a second of logging in.
  await page.waitForTimeout(1100);
  await page.goto("/account");
  await page.getByLabel("Old password").fill(PASSWORD);
  await page.getByLabel("New password", { exact: true }).fill("N3w-pass-word");
  await page.getByLabel("Confirm new password").fill("N3w-pass-word");
  await page.getByRole("button", { name: "change password" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText("Password changed, please log in again")).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("sangra_token"))).toBeNull();
  const me = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${oldToken}` } });
  expect(me.status).toBe(401);
});

test("FE7-E06: the backend restarts while a run waits for approval: the page reconnects and the run continues", async ({ page }) => {
  await restartBackend({ llm: [found, fix("0.10")], tests: tests("failed", "passed") });
  await newVerifiedUser(page);
  await startRun(page, await localProject(page));
  await expect(heading(page, "Review the proposed fix")).toBeVisible({ timeout: LONG });
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  // A new process: its scripted test runner starts again from the top, so the after-fix test is the first entry.
  await restartBackend({ llm: [notFound], tests: tests("passed") });
  await expect(page.getByText("live", { exact: true })).toBeVisible({ timeout: LONG });
  await expect(heading(page, "Review the proposed fix")).toBeVisible();
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(heading(page, "Tests passed")).toBeVisible({ timeout: LONG });
});

test("FE7-E07: two tabs on the same run stay correct; a double approval is handled", async ({ page, context }) => {
  await restartBackend({ llm: [notFound, search("summerRate"), found, fix("0.10")], tests: tests("failed", "passed") });
  await newVerifiedUser(page);
  const runId = await startRun(page, await localProject(page));
  await expect(heading(page, "The agent needs a hint")).toBeVisible({ timeout: LONG });
  const second = await context.newPage(); // the same browser: the same login
  await second.goto(`/runs/${runId}`);
  await expect(heading(second, "The agent needs a hint")).toBeVisible();
  await page.getByLabel("Your hint").fill("Look at summerRate");
  await page.getByRole("button", { name: "send hint" }).click();
  await expect(heading(page, "Review the proposed fix")).toBeVisible({ timeout: LONG });
  await expect(heading(second, "Review the proposed fix")).toBeVisible({ timeout: LONG }); // the other tab follows

  // A third tab that misses the live updates (its stream never connects) still shows the proposal.
  const stale = await context.newPage();
  await stale.route("**/events", () => {});
  await stale.goto(`/runs/${runId}`);
  await expect(heading(stale, "Review the proposed fix")).toBeVisible();
  await page.getByRole("button", { name: "accept & test" }).click();
  await expect(heading(second, "Tests passed")).toBeVisible({ timeout: LONG });
  await stale.getByRole("button", { name: "accept & test" }).click(); // the double approval
  await expect(stale.getByText(/You cannot|already/)).toBeVisible();
  await expect(heading(stale, "Tests passed")).toBeVisible({ timeout: LONG }); // reloaded: the real state
  await expect(heading(page, "Tests passed")).toBeVisible();
});

test("FE7-E08: a second user cannot see the first user's runs or projects", async ({ page, browser }) => {
  await restartBackend({ llm: [notFound], tests: tests("failed") });
  await newVerifiedUser(page, "First User");
  const projectId = await localProject(page);
  const runId = await startRun(page, projectId);
  await expect(heading(page, "The agent needs a hint")).toBeVisible({ timeout: LONG });

  const other = await browser.newContext();
  const intruder = await other.newPage();
  await newVerifiedUser(intruder, "Second User");
  for (const path of [`/runs/${runId}`, `/projects/${projectId}/edit`, `/projects/${projectId}/run`]) {
    await intruder.goto(path);
    await expect(heading(intruder, "Page not found")).toBeVisible();
  }
  await intruder.goto("/runs");
  await expect(intruder.getByText("No runs yet")).toBeVisible();
  await intruder.goto("/projects");
  await expect(intruder.getByText("summer-sale-service")).toHaveCount(0);
  expect((await apiCall(intruder, "POST", `/runs/${runId}/cancel`)).status).toBe(404);
  await other.close();
  await cancel(page);
});

test("FE7-X01: the backend stops in the middle of use: a clear message, then the app recovers", async ({ page }) => {
  await restartBackend({ llm: [notFound], tests: tests("failed") });
  await newVerifiedUser(page);
  await localProject(page);
  await page.goto("/projects");
  await expect(page.getByText("summer-sale-service")).toBeVisible();
  await stopBackend();
  try {
    await page.goto("/runs");
    await expect(page.getByText("Cannot reach the server").first()).toBeVisible({ timeout: LONG });
  } finally {
    await startBackend();
  }
  await page.getByRole("button", { name: "retry" }).first().click();
  await expect(heading(page, "History")).toBeVisible();
  await expect(page.getByText("No runs yet")).toBeVisible({ timeout: LONG });
  await page.goto("/projects");
  await expect(page.getByText("summer-sale-service")).toBeVisible();
});

test("FE7-X01: the backend stops during a live run: 'Reconnecting…', then live again", async ({ page }) => {
  await restartBackend({ llm: [notFound], tests: tests("failed") });
  await newVerifiedUser(page);
  await startRun(page, await localProject(page));
  await expect(heading(page, "The agent needs a hint")).toBeVisible({ timeout: LONG });
  await stopBackend();
  try {
    await expect(page.getByText("Reconnecting…")).toBeVisible({ timeout: LONG });
  } finally {
    await startBackend();
  }
  await expect(page.getByText("live", { exact: true })).toBeVisible({ timeout: LONG });
  await page.getByLabel("Your hint").fill("Look at summerRate");
  await page.getByRole("button", { name: "send hint" }).click();
  await expect(page.locator(".info-bar")).toContainText("hint 2 of 3", { timeout: LONG }); // not found again
});
