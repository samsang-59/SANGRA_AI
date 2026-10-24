// FE6 in a real browser: the result screens of the design (succeeded, failed), the download saved with its name,
// apply and delete, and a 50 MB package download that keeps the page responsive (L02). Set SCREENSHOT_DIR to keep
// screenshots for comparing with design/screens.
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";

const USER = { id: 1, name: "Sangram", email: "sangram@example.com", is_email_verified: true };
const projects = [
  { id: 2, name: "payments-api", language: "python", source_type: "local_path", source_reference: "/code/payments-api",
    branch: null, test_command: "pytest" },
  { id: 3, name: "inventory-tool", language: "python", source_type: "zip", source_reference: null, branch: null,
    test_command: "pytest -q" },
];
const PACKAGE = { size_bytes: 2048, created_at: "2026-10-21T19:40:00Z", expires_at: "2026-10-28T19:40:00Z",
  deleted_at: null, available: true };
const edit = (file, line, oldCode, newCode) => ({ id: line, edit_order: 1, file_path: file, start_line: line,
  end_line: line, old_code: oldCode, new_code: newCode, explanation: null });
const result = (type, attemptId, status, log, ms) => ({ id: attemptId ?? 0, attempt_id: attemptId, test_type: type,
  status, exit_code: status === "passed" ? 0 : 1, log_excerpt: log, duration_ms: ms, docker_image: "python:3.12" });

const SUCCEEDED = { id: 41, project_id: 2, status: "succeeded", language: "python", source_type: "local_path",
  bug_description: "Late fee is added twice on overdue invoices.", stop_reason: null, applied_to_source_at: null,
  final_summary: "Fixed in 2 attempts. 1 edit in billing/late_fee.py. After-fix tests passed (exit code 0).",
  created_at: "2026-10-21T19:40:00Z", package: PACKAGE,
  attempts: [
    { id: 1, attempt_number: 1, reason_started: "first", status: "tests_failed", decision: "accepted",
      reverted_at: "2026-10-21T19:50:00Z", explanation: "The late fee is added again when the invoice total is saved. Remove the second addition.",
      edits: [edit("billing/invoice.py", 41, "        invoice.total += late_fee(invoice)", "")] },
    { id: 2, attempt_number: 2, reason_started: "after_test_failure", status: "tests_passed", decision: "accepted",
      reverted_at: null, explanation: "late_fee() returns the fee plus the fee again. Return the fee only.",
      edits: [edit("billing/late_fee.py", 19, "    return fee + base * rate", "    return fee")] },
  ],
  test_results: [
    result("baseline", null, "failed", "$ pytest\ncollected 48 items\n\ntests/test_invoice.py ....F...\ntests/test_late_fee.py ......\n\nFAILED tests/test_invoice.py::test_overdue_total\n  assert 1100.0 == 1050.0\n1 failed, 47 passed in 14.2s", 14200),
    result("after_fix", 1, "failed", "$ pytest\n2 failed", 13000),
    result("after_fix", 2, "passed", "$ pytest\ncollected 48 items\n\ntests/test_invoice.py ........\ntests/test_late_fee.py ......\n\n48 passed in 13.8s", 13800),
  ] };

const FAILED = { ...SUCCEEDED, id: 40, project_id: 3, status: "failed", source_type: "zip", stop_reason: "tests_failed_3_times",
  bug_description: "Stock count goes negative when an order is cancelled twice.",
  final_summary: "3 attempts were applied and tested. Each one failed and was undone. The package has the last attempted change and all test logs.",
  attempts: [1, 2, 3].map((n) => ({ id: n, attempt_number: n, reason_started: n === 1 ? "first" : "after_test_failure",
    status: "tests_failed", decision: "accepted", reverted_at: "2026-10-21T11:12:00Z",
    explanation: ["Mark the order as cancelled before restocking.", "Clamp stock at zero after a cancel.",
      "Skip the restock when stock is already zero."][n - 1],
    edits: [edit(n === 2 ? "inventory/stock.py" : "inventory/orders.py", [55, 24, 58][n - 1], "        restock(order)",
      "        order.status = \"cancelled\"; restock(order)")] })),
  test_results: [result("baseline", null, "failed", "$ pytest -q\n1 failed, 11 passed in 9.6s", 9600),
    result("after_fix", 3, "failed", "$ pytest -q\n2 failed, 10 passed in 9.9s", 9900)] };

async function shot(page, name) {
  if (process.env.SCREENSHOT_DIR) {
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/${test.info().project.name}-${name}.png`, fullPage: true });
  }
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
});

test("FE6: succeeded (design: runs id · result · succeeded): download, apply, then delete", async ({ page }) => {
  const api = await mockApi(page, { user: USER, projects, runDetails: { 41: structuredClone(SUCCEEDED) } });
  await page.goto("/runs/41");
  await expect(page.getByRole("heading", { name: "Tests passed" })).toBeVisible();
  await expect(page.getByRole("region", { name: "after fix (attempt #2)" })).toContainText("48 passed in 13.8s");
  await expect(page.getByText("payments-api")).toBeVisible();
  await shot(page, "result-succeeded");
  const [download] = await Promise.all([page.waitForEvent("download"),
    page.getByRole("button", { name: "download package" }).click()]);
  expect(download.suggestedFilename()).toBe("sangra-run-41.zip");
  await page.getByRole("button", { name: "apply to my folder" }).click();
  await expect(page.getByRole("button", { name: "applied" })).toBeDisabled();
  await page.getByRole("button", { name: "delete run" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "delete run" }).click();
  await expect(page).toHaveURL(/\/runs$/);
  expect(api.calls).toContain("DELETE /runs/41");
});

test("FE6: failed (design: runs id · result · failed)", async ({ page }) => {
  await mockApi(page, { user: USER, projects, runDetails: { 40: FAILED } });
  await page.goto("/runs/40");
  await expect(page.getByRole("heading", { name: "Failed: 3 fixes did not pass the tests" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "apply to my folder" })).toHaveCount(0);
  await shot(page, "result-failed");
});

test("FE6-L02: a 50 MB package download: the page stays responsive", async ({ page }) => {
  await mockApi(page, { user: USER, projects, runDetails: { 41: SUCCEEDED },
    packageBody: Buffer.alloc(50 * 1024 * 1024, 7) });
  await page.goto("/runs/41");
  await expect(page.getByRole("heading", { name: "Tests passed" })).toBeVisible();
  // Count frames while the download runs: a blocked page would draw (almost) none.
  await page.evaluate(() => {
    window.__frames = [];
    const tick = (time) => {
      window.__frames.push(time);
      if (!window.__stopFrames) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const started = Date.now();
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 60_000 }),
    page.getByRole("button", { name: "download package" }).click()]);
  const took = Date.now() - started;
  const frames = await page.evaluate(() => {
    window.__stopFrames = true;
    const gaps = window.__frames.slice(1).map((t, i) => t - window.__frames[i]);
    return { count: gaps.length, worst: Math.max(...gaps) };
  });
  const path = await download.path();
  const { statSync } = await import("node:fs");
  expect(statSync(path).size).toBe(50 * 1024 * 1024);
  console.log(`FE6-L02 [${test.info().project.name}]: 50 MB saved in ${took} ms; ${frames.count} frames drawn meanwhile, ` +
    `longest frame ${frames.worst.toFixed(0)} ms`);
  expect(frames.worst).toBeLessThan(500); // never frozen for half a second
  await expect(page.getByRole("button", { name: "download package" })).toBeEnabled();
});
