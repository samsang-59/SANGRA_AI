// FE5 in a real browser: the three live screens of the design (searching + reconnecting, waiting_for_approval,
// waiting_for_hint), 2,000 events with scrolling (L01), the keyboard in the approval box (U01) and leaving the page
// (X05). Set SCREENSHOT_DIR to keep screenshots for comparing with design/screens.
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";
import { connectionCount, installBrowserStream, pushEvents, sse, status } from "./stream_fake.js";

const USER = { id: 1, name: "Sangram", email: "sangram@example.com", is_email_verified: true };
const projects = [
  { id: 1, name: "summer-sale-service", language: "java", source_type: "github",
    source_reference: "https://github.com/sangram/summer-sale-service", branch: null, test_command: "mvn test" },
  { id: 2, name: "payments-api", language: "python", source_type: "local_path", source_reference: "/code/payments-api",
    branch: null, test_command: "pytest" },
];

function runDetail(id, overrides = {}) {
  return { id, project_id: 1, bug_description: "The summer sale shows 5% discount, it should be 10%.",
    status: "searching", language: "java", source_type: "github", stop_reason: null, reject_count: 0, hint_count: 0,
    final_summary: null, attempts: [], test_results: [], package: null, created_at: "2026-10-22T14:05:00Z", ...overrides };
}

const START = [
  status(1, "started"), sse(2, "tool_result", "Copied the code from GitHub (commit 3f9a1c2)"),
  sse(3, "tests_started", "Baseline tests started: mvn test"),
  sse(4, "tests_finished", "Baseline finished: failed (1 test failing)"), status(5, "searching"),
  sse(6, "tool_called", 'search_files("discount")'), sse(7, "tool_result", "4 files matched"),
  sse(8, "tool_called", 'search_in_file(DiscountService.java, "summer")'), sse(9, "tool_result", "3 lines found"),
  sse(10, "tool_called", "read_file_lines(DiscountService.java, 10-30)"),
].join("");

async function shot(page, name) {
  if (process.env.SCREENSHOT_DIR) {
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/${test.info().project.name}-${name}.png`, fullPage: true });
  }
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
  await installBrowserStream(page);
});

test("FE5: searching, then the stream drops: 'Reconnecting…' (design: runs id · live · searching)", async ({ page }) => {
  await mockApi(page, { user: USER, projects, runDetails: { 42: runDetail(42) } });
  await page.goto("/runs/42");
  await expect(page.getByRole("heading", { name: "Searching the code for the bug" })).toBeVisible();
  await pushEvents(page, START);
  await expect(page.locator(".log-line")).toHaveCount(10);
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  await expect(page.getByText("agent is working")).toBeVisible();
  await expect(page.getByRole("button", { name: "cancel run" })).toBeVisible();
  const before = await connectionCount(page);
  await page.evaluate(() => window.__sse.drop());
  await expect(page.getByText("Reconnecting…")).toBeVisible();
  await shot(page, "searching-reconnecting");
  await expect.poll(() => connectionCount(page), { timeout: 5000 }).toBe(before + 1); // after 2 s
  await expect(page.getByText("live", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__sse.connections.at(-1).lastEventId)).toBe("10");
});

test("FE5: waiting_for_approval (design: runs id · live · waiting_for_approval), keyboard use (U01)", async ({ page }) => {
  const attempt = { id: 302, attempt_number: 2, reason_started: "after_reject", status: "waiting_for_approval",
    explanation: "The summer sale rate is set to 0.05 in DiscountService and 5 in application.properties. Changing both to 10% so the code and the config agree.",
    edits: [
      { id: 1, edit_order: 1, file_path: "src/main/java/com/shop/sale/DiscountService.java", start_line: 18, end_line: 18,
        old_code: "    private static final double SUMMER_SALE_DISCOUNT = 0.05;",
        new_code: "    private static final double SUMMER_SALE_DISCOUNT = 0.10;", explanation: "discount constant was 5% instead of 10%." },
      { id: 2, edit_order: 2, file_path: "src/main/resources/application.properties", start_line: 7, end_line: 7,
        old_code: "sale.summer.discount=5", new_code: "sale.summer.discount=10", explanation: "config value must match the code." },
    ] };
  const api = await mockApi(page, { user: USER, projects,
    runDetails: { 42: runDetail(42, { status: "waiting_for_approval", reject_count: 1, attempts: [attempt] }) } });
  await page.goto("/runs/42");
  await expect(page.getByRole("heading", { name: "Review the proposed fix" })).toBeVisible();
  await pushEvents(page, START + [sse(11, "approval_requested", "Approval requested for fix #1"),
    sse(12, "approval_decided", 'Fix #1 rejected: "also change the config file"', { decision: "rejected" }),
    status(13, "searching"), sse(14, "tool_called", 'search_files("sale.summer")'), sse(15, "tool_result", "1 file matched"),
    status(16, "waiting_for_approval"), sse(17, "approval_requested", "Approval requested for fix #2")].join(""));
  await expect(page.locator(".log-line")).toHaveCount(17);
  await expect(page.getByText("waiting for you")).toBeVisible();
  await expect(page.locator(".info-bar")).toContainText("rejected 1 of 3");
  await shot(page, "waiting-for-approval");
  await page.getByLabel(/Feedback/).focus();
  await page.keyboard.type("Keep the config file as it is.");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "accept & test" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "reject" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/Sent. Waiting for the agent/)).toBeVisible();
  expect(api.posts).toEqual([{ action: "approval",
    body: { attempt_id: 302, decision: "rejected", feedback: "Keep the config file as it is." } }]);
});

test("FE5: waiting_for_hint (design: runs id · live · waiting_for_hint)", async ({ page }) => {
  const api = await mockApi(page, { user: USER, projects, runDetails: { 43: runDetail(43, { project_id: 2,
    language: "python", source_type: "local_path", status: "waiting_for_hint", hint_count: 2,
    bug_description: "Refund amount is wrong for partial refunds." }) } });
  await page.goto("/runs/43");
  await expect(page.getByRole("heading", { name: "The agent needs a hint" })).toBeVisible();
  await pushEvents(page, [status(1, "started"), sse(2, "tool_result", "Copied the local folder"),
    status(3, "searching"), sse(4, "tool_called", 'search_files("refund")'), sse(5, "tool_result", "0 files matched"),
    status(6, "waiting_for_hint"), sse(7, "hint_requested", "Hint requested (1 of 3)")].join(""));
  await expect(page.locator(".log-line")).toHaveCount(7);
  await expect(page.getByText("payments-api")).toBeVisible();
  await expect(page.locator(".info-bar")).toContainText("hint 2 of 3");
  await shot(page, "waiting-for-hint");
  await page.getByLabel("Your hint").fill("look at refund_calculator.py");
  await page.getByRole("button", { name: "send hint" }).click();
  await expect(page.getByText(/Sent. The agent is searching again/)).toBeVisible();
  expect(api.posts).toEqual([{ action: "hint", body: { text: "look at refund_calculator.py" } }]);
});

test("FE5-L01: 2,000 events: the first render in under 1 s, and scrolling is smooth", async ({ page }) => {
  await mockApi(page, { user: USER, projects, runDetails: { 42: runDetail(42) } });
  await page.goto("/runs/42");
  await expect(page.getByRole("heading", { name: "Searching the code for the bug" })).toBeVisible();
  const events = Array.from({ length: 2000 }, (_, i) => sse(i + 1, i % 2 ? "tool_result" : "tool_called",
    `read_file_lines(DiscountService.java, ${i}-${i + 20})`)).join("");
  const took = await page.evaluate(async (text) => {
    const started = performance.now();
    window.__sse.push(text);
    await new Promise((resolve) => {
      const check = () => (document.querySelectorAll(".log-line").length === 2000 ? resolve() : requestAnimationFrame(check));
      check();
    });
    return performance.now() - started;
  }, events);
  // Scroll from the bottom to the top in 60 steps, one per frame, and measure the frames.
  const frames = await page.evaluate(async () => {
    const box = document.querySelector(".log-scroll");
    const gaps = [];
    let last = performance.now();
    for (let step = 60; step >= 0; step -= 1) {
      box.scrollTop = (box.scrollHeight * step) / 60;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const now = performance.now();
      gaps.push(now - last);
      last = now;
    }
    gaps.sort((a, b) => a - b);
    return { median: gaps[Math.floor(gaps.length / 2)], worst: gaps[gaps.length - 1] };
  });
  console.log(`FE5-L01 [${test.info().project.name}]: 2,000 events rendered in ${took.toFixed(0)} ms; scrolling ` +
    `frames median ${frames.median.toFixed(1)} ms, worst ${frames.worst.toFixed(1)} ms`);
  expect(took).toBeLessThan(1000);
  expect(frames.median).toBeLessThan(50);
});

test("FE5-X05: leaving the page stops the stream and does not cancel the run", async ({ page }) => {
  const api = await mockApi(page, { user: USER, projects, runDetails: { 42: runDetail(42) } });
  await page.goto("/runs/42");
  await pushEvents(page, START);
  await expect(page.locator(".log-line")).toHaveCount(10);
  await page.locator(".breadcrumb").getByRole("link", { name: "history" }).click();
  await expect(page).toHaveURL(/\/runs$/);
  await expect.poll(() => page.evaluate(() => window.__sse.connections.every((c) => c.aborted))).toBe(true);
  expect(api.posts).toEqual([]);
});
