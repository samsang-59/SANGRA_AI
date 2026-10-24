// FE5-F11 against the real backend (scripted agent, run with `npm run test:real`): a complete run followed on the Run
// page: events, a hint, a rejected fix, an accepted fix, success. Then FE6 on the real result: tests, attempts,
// download, apply to my folder, delete.
import { readFileSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { FILE, fix, found, localProject, newVerifiedUser, notFound, restartBackend, search, startRun, tests,
} from "./helpers.js";

test("FE5-F11: a complete run with the scripted agent: events, hint, reject, accept, success", async ({ page }) => {
  test.skip(!process.env.REAL_ORIGIN, "needs the real backend: npm run test:real");
  // Nothing found -> a hint -> found -> a fix that is rejected -> a second fix that is accepted -> tests pass.
  await restartBackend({ llm: [search("rebate"), notFound, search("summerRate"), found,
    fix("0.15", "The summer rate should be 15%."), fix("0.10")], tests: tests("failed", "passed") });
  await newVerifiedUser(page);
  const projectId = await localProject(page);
  await startRun(page, projectId);

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
  await expect(page.locator(".run-result .badge")).toContainText("succeeded");
  await expect(page.getByRole("button", { name: "cancel run" })).toHaveCount(0);

  // FE6 on the real data: the tests, the attempts, the download, "apply to my folder", delete.
  await expect(page.getByRole("region", { name: "baseline (before any change)" })).toContainText("failed");
  await expect(page.getByRole("region", { name: "after fix (attempt #2)" })).toContainText("passed");
  await expect(page.getByRole("article")).toHaveCount(2);
  await expect(page.getByRole("article", { name: "attempt #1" })).toContainText("your feedback › It must be exactly 10 percent, not 15.");
  const [download] = await Promise.all([page.waitForEvent("download"),
    page.getByRole("button", { name: "download package" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^sangra-run-\d+\.zip$/);
  expect(readFileSync(await download.path()).subarray(0, 2).toString()).toBe("PK");
  const file = `${process.env.REAL_PROJECT_DIR}/${FILE}`;
  const original = readFileSync(file, "utf8");
  expect(readFileSync(file, "utf8")).toContain("return 0.05;"); // the folder is untouched until "apply"
  await page.getByRole("button", { name: "apply to my folder" }).click();
  await expect(page.getByRole("button", { name: "applied" })).toBeDisabled();
  expect(readFileSync(file, "utf8")).toContain("return 0.10;");
  writeFileSync(file, original); // the other tests start from the original folder again
  await page.getByRole("button", { name: "delete run" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "delete run" }).click();
  await expect(page).toHaveURL(/\/runs$/);
  await expect(page.getByText("No runs yet")).toBeVisible();
});
