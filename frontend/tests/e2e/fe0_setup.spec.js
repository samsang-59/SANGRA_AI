// Phase FE0 in a real browser: FE0-F01 (dev server), FE0-F02 (built app), FE0-X02 (unknown URL), FE0-B01.
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }, testInfo) => {
  testInfo.consoleErrors = [];
  page.on("console", (message) => message.type() === "error" && testInfo.consoleErrors.push(message.text()));
  page.on("pageerror", (error) => testInfo.consoleErrors.push(String(error)));
});

test.afterEach(async ({}, testInfo) => {
  expect(testInfo.consoleErrors).toEqual([]);
});

test("the app opens and shows the first page", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page).toHaveTitle("SANGRA_AI");
});

test("an unknown URL shows the page-not-found page", async ({ page }) => {
  await page.goto("/this-page");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.getByRole("link", { name: "go to projects" }).click();
  await expect(page).toHaveURL(/\/projects$/);
});

test("the design fonts and colours are loaded", async ({ page }) => {
  await page.goto("/this-page");
  const body = await page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return { background: style.backgroundColor, color: style.color, font: style.fontFamily };
  });
  expect(body.background).toBe("rgb(10, 12, 16)");
  expect(body.color).toBe("rgb(231, 234, 240)");
  expect(body.font).toContain("Inter");
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('16px "JetBrains Mono"'))).toBe(true);
});
