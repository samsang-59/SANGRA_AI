// FE2 in a real browser: FE2-U01 (register and login with the keyboard only) and the look of the auth pages.
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock_api.js";

test("FE2-U01: register, log out and log in again using only the keyboard", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/register");
  await expect(page.getByText("Create your account")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Name")).toBeFocused();
  await page.keyboard.type("Sangram");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.type("sangram@example.com");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
  await page.keyboard.type("Str0ng-pass");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByText("Verify your email to start runs")).toBeVisible();

  await page.goto("/account");
  const logout = page.getByRole("button", { name: "log out" });
  await logout.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Log in to your account")).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.type("sangram@example.com");
  await page.keyboard.press("Tab"); // "Forgot password?" sits next to the Password label
  await expect(page.getByRole("link", { name: "Forgot password?" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
  await page.keyboard.type("Str0ng-pass");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  // the reload of /account loads the user once (GET /auth/me)
  expect(api.calls).toEqual(["POST /auth/register", "GET /auth/me", "POST /auth/login"]);
});

test("a wrong password shows the message and keeps the user on the page", async ({ page }) => {
  await mockApi(page, { user: { id: 1, name: "S", email: "sangram@example.com", is_email_verified: true } });
  await page.goto("/login");
  await page.getByLabel("Email").fill("sangram@example.com");
  await page.getByLabel("Password", { exact: true }).fill("Wrong-pass1");
  await page.getByLabel("Password", { exact: true }).press("Enter");
  await expect(page.getByRole("alert")).toHaveText("!Wrong email or password");
  await expect(page).toHaveURL(/\/login$/);
});

test("FE0-F03: the built app sends its requests to the address from the env file", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name.startsWith("dev"), "checked on the built app");
  const seen = [];
  page.on("request", (request) => seen.push(request.url()));
  await mockApi(page);
  await page.goto("/login");
  await page.getByLabel("Email").fill("a@example.com");
  await page.getByLabel("Password", { exact: true }).fill("Wrong-pass1");
  await page.getByLabel("Password", { exact: true }).press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
  expect(seen.filter((url) => url.includes("/auth/login"))).toEqual(["http://localhost:8000/api/v1/auth/login"]);
});
