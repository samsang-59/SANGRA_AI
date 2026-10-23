// FE3 in a real browser: long values do not break the layout (U01), the delete dialog by keyboard (U02).
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";

const USER = { id: 1, name: "S", email: "sangram@example.com", is_email_verified: true };
const project = (id, extra = {}) => ({ id, name: `project-${id}`, language: "java", source_type: "github",
  source_reference: `https://github.com/sangram/project-${id}`, branch: null, test_command: "mvn test", ...extra });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("sangra_token", "aaa.bbb.ccc"));
});

for (const width of [1280, 768]) {
  test(`FE3-U01: 100-character names, long URLs and paths at ${width}px: no sideways scrolling`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { user: USER, projects: [
      project(1, { name: "n".repeat(100), source_reference: `https://github.com/${"o".repeat(39)}/${"r".repeat(100)}`,
        branch: `feature/${"b".repeat(200)}`, test_command: `mvn -q test ${"-Dx=1 ".repeat(80)}` }),
      project(2, { source_type: "local_path", source_reference: `C:\\${"folder\\".repeat(40)}payments-api` }),
    ] });
    await page.goto("/projects");
    await expect(page.getByRole("row")).toHaveCount(3);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page.getByText("n".repeat(100))).toHaveAttribute("title", "n".repeat(100));
  });
}

test("FE3-U02: the delete dialog by keyboard; the focus returns after closing", async ({ page }) => {
  await mockApi(page, { user: USER, projects: [project(1), project(2)] });
  await page.goto("/projects");
  const opener = page.getByRole("button", { name: "delete project-2" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog", { name: "Delete project?" });
  await expect(dialog.getByRole("button", { name: "cancel" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "delete" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter"); // "delete"
  await expect(page.getByRole("row")).toHaveCount(2);
  await expect(page.getByText("project-2", { exact: true })).toHaveCount(0);
});
