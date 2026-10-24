// FE4 in a real browser: the zip upload (with its file name), 500 runs in the history (L01), a 2,000-character
// bug text (L02).
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";

const USER = { id: 1, name: "S", email: "sangram@example.com", is_email_verified: true };
const projects = [
  { id: 1, name: "summer-sale-service", language: "java", source_type: "github",
    source_reference: "https://github.com/sangram/summer-sale-service", branch: null, test_command: "mvn test" },
  { id: 3, name: "inventory-tool", language: "python", source_type: "zip", source_reference: null, branch: null,
    test_command: "pytest -q" },
];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
});

test("FE4-F02: the zip is uploaded with its name, then the run page opens", async ({ page }) => {
  const api = await mockApi(page, { user: USER, projects });
  await page.goto("/projects/3/run");
  await page.getByLabel("Describe the bug in plain English").fill("Stock count goes negative when an order is cancelled twice.");
  await page.getByLabel("Code (.zip)").setInputFiles({ name: "inventory-tool.zip", mimeType: "application/zip",
    buffer: Buffer.from([80, 75, 5, 6, ...Array(18).fill(0)]) });
  await expect(page.getByText("inventory-tool.zip")).toBeVisible();
  await page.getByRole("button", { name: /start run/ }).click();
  await expect(page).toHaveURL(/\/runs\/77$/);
  const [upload] = api.uploads;
  expect(upload.contentType).toMatch(/^multipart\/form-data; boundary=/);
  const body = upload.body.toString("latin1");
  expect(body).toContain('name="zip_file"; filename="inventory-tool.zip"');
  expect(body).toContain('name="bug_description"');
});

test("FE4-L01: 500 runs: a page renders in under 1 s, a filter change in under 500 ms", async ({ page }) => {
  const runs = Array.from({ length: 500 }, (_, i) => ({ id: 500 - i, project_id: i % 2 ? 3 : 1,
    project_name: i % 2 ? "inventory-tool" : "summer-sale-service", status: ["succeeded", "failed", "stopped"][i % 3],
    created_at: new Date(Date.UTC(2026, 9, 22) - i * 3_600_000).toISOString() }));
  await mockApi(page, { user: USER, projects, runs });
  let started = Date.now();
  await page.goto("/runs");
  await expect(page.getByText("#481")).toBeVisible();
  const firstPage = Date.now() - started;
  await expect(page.getByText("20 per page · page 1 of 25")).toBeVisible();
  started = Date.now();
  await page.getByLabel("status").selectOption("failed");
  await expect(page.getByRole("row").nth(1)).toContainText("failed");
  await expect(page.getByText("20 per page · page 1 of 9")).toBeVisible();
  const filterChange = Date.now() - started;
  console.log(`FE4-L01: first page ${firstPage} ms (whole app start), filter change ${filterChange} ms`);
  expect(firstPage).toBeLessThan(1000);
  expect(filterChange).toBeLessThan(500);
});

test("FE4-L02: a 2,000-character bug text: it works and the layout holds", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const api = await mockApi(page, { user: USER, projects });
  await page.goto("/projects/1/run");
  const words = "The summer sale shows a five percent discount where it should be ten percent. ";
  const text = words.repeat(30).slice(0, 2000);
  await page.getByLabel("Describe the bug in plain English").fill(text);
  await expect(page.getByText("2000 / 2000 · min 10")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: /start run/ }).click();
  await expect(page).toHaveURL(/\/runs\/77$/);
  expect(JSON.parse(api.uploads[0].body.toString()).bug_description).toBe(text.trim());
});
