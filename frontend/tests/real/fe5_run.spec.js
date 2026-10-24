// FE5-F11 against the real backend (scripted agent, run with `npm run test:real`): a complete run followed on the Run
// page: events, a hint, a rejected fix, an accepted fix, success.
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const API = `${process.env.REAL_ORIGIN}/api/v1`;

function verifyLink(email) {
  const mail = readFileSync(process.env.REAL_MAIL_FILE, "utf8").trim().split("\n").map((line) => JSON.parse(line))
    .find((m) => m.to === email);
  return /\/verify-email\?token=[A-Za-z0-9_-]+/.exec(mail.body)[0];
}

test("FE5-F11: a complete run with the scripted agent: events, hint, reject, accept, success", async ({ page }) => {
  test.skip(!process.env.REAL_ORIGIN, "needs the real backend: npm run test:real");
  const email = `real-${Date.now()}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("Real Test");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("Str0ng-pass-1");
  await page.getByRole("button", { name: "create account" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.goto(verifyLink(email));
  await expect(page.getByRole("heading", { name: "Email verified" })).toBeVisible();

  const projectId = await page.evaluate(async ({ api, folder }) => {
    const response = await fetch(`${api}/projects`, { method: "POST", headers: { "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("sangra_token")}` },
    body: JSON.stringify({ name: "summer-sale-service", language: "java", source_type: "local_path",
      source_reference: folder, test_command: "mvn test" }) });
    return (await response.json()).data.id;
  }, { api: API, folder: process.env.REAL_PROJECT_DIR });

  await page.goto(`/projects/${projectId}/run`);
  await page.getByLabel("Describe the bug in plain English").fill("The summer sale shows 5% discount, it should be 10%.");
  await page.getByRole("button", { name: /start run/ }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);

  // Nothing found: the hint box.
  await expect(page.getByRole("heading", { name: "The agent needs a hint" })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".info-bar")).toContainText("hint 1 of 3");
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  const log = page.locator(".log-text");
  await expect(log.filter({ hasText: "status → started" })).toHaveCount(1);
  await expect(log.filter({ hasText: "status → waiting_for_hint" })).toHaveCount(1);
  await page.getByLabel("Your hint").fill("Look at summerRate in Discount.java");
  await page.getByRole("button", { name: "send hint" }).click();

  // The first fix: reject it.
  await expect(page.getByRole("heading", { name: "Review the proposed fix" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("The summer rate should be 15%.")).toBeVisible();
  await expect(page.locator(".info-bar")).toContainText("rejected 0 of 3");
  await expect(page.locator(".diff-new")).toContainText("return 0.15;");
  await page.getByLabel(/Feedback/).fill("It must be exactly 10 percent, not 15.");
  await page.getByRole("button", { name: "reject" }).click();

  // The second fix: accept it.
  await expect(page.getByText("The summer rate must be 0.10.")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".info-bar")).toContainText("rejected 1 of 3");
  await expect(page.locator(".info-bar")).toContainText("attempt #2 · after_reject");
  await expect(page.locator(".diff-old")).toContainText("return 0.05;");
  await expect(page.locator(".diff-new")).toContainText("return 0.10;");
  // The timeline: every event once, in order (001, 002, ... with no gap and no repeat).
  await expect(log.filter({ hasText: "status → waiting_for_approval" })).toHaveCount(2);
  const shown = (await page.locator(".log-number").allTextContents()).map(Number);
  expect(shown.length).toBeGreaterThan(10);
  expect(shown).toEqual(shown.map((_, i) => i + 1));
  await page.getByRole("button", { name: "accept & test" }).click();

  // The end: the result view.
  await expect(page.getByRole("heading", { name: "Tests passed" })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".result-card .badge")).toContainText("succeeded");
  await expect(page.getByRole("button", { name: "cancel run" })).toHaveCount(0);
});
