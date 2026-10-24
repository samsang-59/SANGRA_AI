// FE7-L02: clicking around the app for a long time: no console errors and no growth in memory. The full length of the
// plan (30 minutes) runs with SOAK_MINUTES=30; by default it runs 1 minute. Only on the built app (Chromium, where
// the heap can be measured).
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";
import { installBrowserStream, pushEvents, sse } from "./stream_fake.js";

const MINUTES = Number(process.env.SOAK_MINUTES || 1);
const USER = { id: 1, name: "Sangram", email: "sangram@example.com", is_email_verified: true };
const projects = [
  { id: 1, name: "summer-sale-service", language: "java", source_type: "github",
    source_reference: "https://github.com/sangram/summer-sale-service", branch: null, test_command: "mvn test" },
  { id: 2, name: "payments-api", language: "python", source_type: "local_path", source_reference: "/code/payments-api",
    branch: null, test_command: "pytest" },
];
const runs = Array.from({ length: 45 }, (_, i) => ({ id: 60 - i, project_id: (i % 2) + 1,
  project_name: projects[i % 2].name, status: ["succeeded", "failed", "stopped"][i % 3],
  created_at: new Date(Date.UTC(2026, 9, 22, 14) - i * 3_600_000).toISOString() }));
const edit = { id: 1, edit_order: 1, file_path: "src/main/java/shop/Discount.java", start_line: 5, end_line: 5,
  old_code: "        return 0.05;", new_code: "        return 0.10;", explanation: "the rate" };
const base = { project_id: 1, language: "java", source_type: "github", stop_reason: null, reject_count: 0,
  hint_count: 1, final_summary: "The fix was applied and the tests passed.", applied_to_source_at: null,
  bug_description: "The summer sale shows 5% discount, it should be 10%.", created_at: "2026-10-22T14:05:00Z",
  package: { size_bytes: 10, available: true, deleted_at: null }, test_results: [],
  attempts: [{ id: 1, attempt_number: 1, reason_started: "first", status: "waiting_for_approval", decision: null,
    reverted_at: null, explanation: "The summer rate must be 0.10.", edits: [edit] }] };
const runDetails = { 50: { ...base, id: 50, status: "waiting_for_approval" }, 51: { ...base, id: 51, status: "succeeded" } };

test("FE7-L02: clicking around for a long time: no console errors, no growth in memory", async ({ page }, info) => {
  test.skip(info.project.name !== "preview-chromium", "measured once, on the built app");
  test.setTimeout((MINUTES + 5) * 60_000);
  const problems = [];
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
  page.on("pageerror", (error) => problems.push(String(error)));
  await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
  await installBrowserStream(page);
  await mockApi(page, { user: USER, projects, runs, runDetails });
  const cdp = await page.context().newCDPSession(page);
  const heap = async () => {
    await cdp.send("HeapProfiler.collectGarbage");
    return (await cdp.send("Runtime.getHeapUsage")).usedSize / 1024 / 1024;
  };

  const nav = (name) => page.locator(".top-bar").getByRole("link", { name, exact: true });
  let rounds = 0;
  let sequence = 0;
  async function round() {
    await nav("projects").click();
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    await page.getByRole("link", { name: /new project/ }).click();
    await page.getByLabel("Name").fill(`project ${rounds}`);
    await nav("history").click();
    await expect(page.getByText("#60")).toBeVisible();
    await page.getByRole("button", { name: /next/ }).click();
    await expect(page.getByText("page 2 of 3")).toBeVisible();
    await page.getByLabel("status").selectOption("failed");
    await page.getByLabel("status").selectOption("");
    await expect(page.getByText("#51", { exact: true })).toBeVisible();
    await page.goto("/runs/50"); // a live run: a stream with events, the approval box
    await expect(page.getByRole("heading", { name: "Review the proposed fix" })).toBeVisible();
    for (let i = 0; i < 20; i += 1) {
      sequence += 1;
      await pushEvents(page, sse(sequence, i % 2 ? "tool_result" : "tool_called", `step ${sequence} of the agent`));
    }
    await page.getByLabel(/Feedback/).fill("Not like this, try again.");
    await page.locator(".breadcrumb").getByRole("link", { name: "history" }).click();
    await page.getByText("#51", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Tests passed" })).toBeVisible();
    await nav("account").click();
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    rounds += 1;
  }

  await page.goto("/projects");
  const warmUpEnd = Date.now() + Math.min(60_000, (MINUTES * 60_000) / 4);
  while (Date.now() < warmUpEnd) await round();
  const start = await heap();
  const startRounds = rounds;
  const end = Date.now() + MINUTES * 60_000;
  while (Date.now() < end) await round();
  const finish = await heap();
  console.log(`FE7-L02: ${MINUTES} min, ${rounds} rounds (${rounds - startRounds} measured), heap ${start.toFixed(1)} MB ` +
    `after the warm-up -> ${finish.toFixed(1)} MB at the end; console errors: ${problems.length}`);
  expect(problems).toEqual([]);
  expect(finish - start).toBeLessThan(Math.max(5, start * 0.2)); // no growth beyond noise
});
