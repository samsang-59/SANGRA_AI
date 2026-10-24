// FE7-U01 (an axe scan of every page: no serious or critical problem) and FE7-U02 (widths 360, 768 and 1280: no
// sideways scrolling of the page), with the mocked API so every page and state can be shown.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";
import { installBrowserStream, pushEvents, sse, status } from "./stream_fake.js";

const VERIFIED = { id: 1, name: "Sangram", email: "sangram@example.com", is_email_verified: true };
const UNVERIFIED = { ...VERIFIED, is_email_verified: false };
const projects = [
  { id: 1, name: "summer-sale-service", language: "java", source_type: "github",
    source_reference: "https://github.com/sangram/summer-sale-service", branch: "main", test_command: "mvn test" },
  { id: 2, name: "payments-api", language: "python", source_type: "local_path",
    source_reference: "/home/sangram/code/payments-api-with-a-rather-long-folder-name", branch: null,
    test_command: "pytest" },
  { id: 3, name: "inventory-tool", language: "python", source_type: "zip", source_reference: null, branch: null,
    test_command: "pytest -q" },
];
const runs = Array.from({ length: 30 }, (_, i) => ({ id: 60 - i, project_id: (i % 3) + 1,
  project_name: projects[i % 3].name, status: ["succeeded", "failed", "stopped"][i % 3],
  created_at: new Date(Date.UTC(2026, 9, 22, 14) - i * 3_600_000).toISOString() }));
const longLine = "        return applyDiscount(order.total(), SummerSale.rate(), customer.loyaltyLevel(), Region.EU);";
const attempt = (n, extra = {}) => ({ id: n, attempt_number: n, reason_started: n === 1 ? "first" : "after_reject",
  status: "waiting_for_approval", decision: null, reverted_at: null,
  explanation: "The summer sale rate is set to 0.05 in DiscountService. Changing it to 0.10.",
  edits: [{ id: n, edit_order: 1, file_path: "src/main/java/com/shop/sale/DiscountService.java", start_line: 18,
    end_line: 18, old_code: longLine, new_code: longLine.replace("EU", "EUROPE"), explanation: "the rate" }],
  ...extra });
const base = { project_id: 1, language: "java", source_type: "github", stop_reason: null, reject_count: 0,
  hint_count: 0, final_summary: null, attempts: [], test_results: [], package: null, applied_to_source_at: null,
  bug_description: "The summer sale shows 5% discount, it should be 10%.", created_at: "2026-10-22T14:05:00Z" };
const runDetails = {
  10: { ...base, id: 10, status: "searching" },
  11: { ...base, id: 11, status: "waiting_for_approval", reject_count: 1, attempts: [attempt(2)] },
  12: { ...base, id: 12, status: "waiting_for_hint", hint_count: 2 },
  13: { ...base, id: 13, project_id: 2, source_type: "local_path", status: "succeeded",
    final_summary: "The fix was applied and the tests passed.",
    package: { size_bytes: 10, available: true, deleted_at: null },
    attempts: [attempt(1, { status: "tests_passed", decision: "accepted" })],
    test_results: [{ id: 1, attempt_id: null, test_type: "baseline", status: "failed", exit_code: 1,
      log_excerpt: `$ mvn test\n${"FAILED DiscountServiceTest.summerSale expected 0.1 but was 0.05 ".repeat(3)}`,
      duration_ms: 9000, docker_image: "maven:3.9-eclipse-temurin-17" },
    { id: 2, attempt_id: 1, test_type: "after_fix", status: "passed", exit_code: 0, log_excerpt: "BUILD SUCCESS",
      duration_ms: 8000, docker_image: "maven:3.9-eclipse-temurin-17" }] },
  14: { ...base, id: 14, status: "failed", stop_reason: "tests_failed_3_times",
    final_summary: "3 attempts were applied and tested.", package: null },
};
const LIVE_EVENTS = [status(1, "started"), sse(2, "tool_result", "Copied the code from GitHub (commit 3f9a1c2)"),
  status(3, "searching"), sse(4, "tool_called", `read_file_lines(${"src/main/java/com/shop/sale/".repeat(3)}Discount.java)`),
  sse(5, "error", "A long error message that has to wrap on a phone screen without making the page scroll.")].join("");

// [name, path, heading, user (null = logged out), extra setup]
const PAGES = [
  ["login", "/login", null, null],
  ["register", "/register", null, null],
  ["forgot password", "/forgot-password", null, null],
  ["reset password", "/reset-password?token=abc", null, null],
  ["verify email", "/verify-email?token=abc", null, null],
  ["projects", "/projects", "Projects", VERIFIED],
  ["projects, unverified, empty", "/projects", "Projects", UNVERIFIED, { projects: [] }],
  ["new project", "/projects/new", "New project", VERIFIED],
  ["edit project", "/projects/2/edit", "Edit project", VERIFIED],
  ["start run (zip)", "/projects/3/run", "Start a run", VERIFIED],
  ["history", "/runs", "History", VERIFIED, { waitFor: "#60" }],
  ["run: searching", "/runs/10", "Searching the code for the bug", VERIFIED, { live: true }],
  ["run: waiting for approval", "/runs/11", "Review the proposed fix", VERIFIED, { live: true }],
  ["run: waiting for a hint", "/runs/12", "The agent needs a hint", VERIFIED, { live: true }],
  ["run: succeeded", "/runs/13", "Tests passed", VERIFIED],
  ["run: failed", "/runs/14", "Failed: 3 fixes did not pass the tests", VERIFIED],
  ["account", "/account", "Account", UNVERIFIED],
  ["not found", "/no/such/page", "Page not found", VERIFIED],
];

async function open(page, [, path, title, user, extra = {}]) {
  if (user) await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
  await installBrowserStream(page);
  await mockApi(page, { user: user ?? VERIFIED, projects: extra.projects ?? projects, runs,
    activeRun: user ? { id: 10, status: "searching" } : null, runDetails: structuredClone(runDetails) });
  await page.goto(path);
  if (title) await expect(page.getByRole("heading", { name: title, exact: true }).first()).toBeVisible();
  else await page.waitForLoadState("networkidle");
  if (extra.waitFor) await expect(page.getByText(extra.waitFor, { exact: true })).toBeVisible();
  if (extra.live) {
    await pushEvents(page, LIVE_EVENTS);
    await expect(page.locator(".log-line")).toHaveCount(5);
  }
}

for (const entry of PAGES) {
  test(`FE7-U01: axe finds no serious or critical problem: ${entry[0]}`, async ({ page }) => {
    await open(page, entry);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`)).toEqual([]);
  });

  for (const width of [360, 768, 1280]) {
    test(`FE7-U02: ${width} px wide, no sideways scrolling: ${entry[0]}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await open(page, entry);
      const sizes = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, view: window.innerWidth }));
      expect(sizes.page).toBeLessThanOrEqual(sizes.view);
      if (process.env.SCREENSHOT_DIR && width === 360) {
        await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/w360-${entry[0].replace(/[^a-z0-9]+/gi, "-")}.png`,
          fullPage: true });
      }
    });
  }
}
