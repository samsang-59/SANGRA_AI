// FE2: the auth pages (F10-F17, X10-X17, S10-S12, U02).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TOKEN_KEY, clearToken } from "../../src/api/token.js";
import {
  GOOD_TOKEN, UNVERIFIED, VERIFIED, HttpResponse, api, currentPath, errorReply, fakeBackend, http, renderApp, saveToken,
} from "./app_helpers.jsx";

const { server, seen, state } = fakeBackend();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
  state.user = VERIFIED;
});
afterEach(() => server.resetHandlers());

const sent = (path) => seen.filter((r) => new URL(r.url).pathname === `/api/v1${path}`);
const LINK_TOKEN = "t".repeat(43);

async function fillRegister(user, { name = "Sangram", email = "sangram@example.com", password = "Str0ng-pass" } = {}) {
  if (name) await user.type(screen.getByLabelText("Name"), name);
  if (email) await user.type(screen.getByLabelText("Email"), email);
  if (password) await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "create account" }));
}

describe("register", () => {
  it.each([
    [{ name: "" }, "Name", "Enter your name."],
    [{ name: "x".repeat(101) }, "Name", "Name must be 1-100 characters."],
    [{ name: "    " }, "Name", "Enter your name."],
    [{ email: "no-at-sign" }, "Email", "Enter a valid email address."],
    [{ email: "a@b@c.com" }, "Email", "Enter a valid email address."],
    [{ password: "Short1" }, "Password", "Password must be at least 8 characters."],
    [{ password: "longpassword" }, "Password", "Password must contain at least one digit."],
    [{ password: "12345678" }, "Password", "Password must contain at least one letter."],
    [{ password: "Ä1".repeat(25) }, "Password", "Password must be at most 72 bytes."], // 25 x 3 bytes = 75
  ])("FE2-F10 / U02: %j is refused before sending, focus on the field", async (values, label, message) => {
    const user = userEvent.setup();
    renderApp("/register");
    await fillRegister(user, values);
    expect(await screen.findByText(message)).toBeInTheDocument();
    const field = screen.getByLabelText(label);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription(expect.stringContaining(message));
    expect(field).toHaveFocus();
    expect(sent("/auth/register")).toHaveLength(0);
  });

  it("FE2-X10: a duplicate email shows the backend message on the form", async () => {
    server.use(http.post(api("/auth/register"), () =>
      errorReply(409, "EMAIL_ALREADY_REGISTERED", "This email is already registered.")));
    const user = userEvent.setup();
    renderApp("/register");
    await fillRegister(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("This email is already registered.");
    expect(currentPath()).toBe("/register");
  });

  it("FE2-X17: spaces around the name and the email are trimmed", async () => {
    server.use(http.post(api("/auth/register"), () =>
      HttpResponse.json({ data: { user: VERIFIED, token: GOOD_TOKEN } }, { status: 201 })));
    const user = userEvent.setup();
    renderApp("/register");
    await fillRegister(user, { name: "  Sangram  ", email: "  sangram@example.com " });
    await screen.findByRole("heading", { name: "Projects" });
    expect(await sent("/auth/register")[0].json()).toEqual({ name: "Sangram", email: "sangram@example.com",
      password: "Str0ng-pass" });
  });

  it("FE2-X16: a double click sends one request", async () => {
    let release;
    server.use(http.post(api("/auth/register"), async () => {
      await new Promise((resolve) => { release = resolve; });
      return HttpResponse.json({ data: { user: VERIFIED, token: GOOD_TOKEN } }, { status: 201 });
    }));
    const user = userEvent.setup();
    renderApp("/register");
    await fillRegister(user);
    const button = screen.getByRole("button", { name: /sending/ });
    expect(button).toBeDisabled();
    await user.click(button);
    await user.keyboard("{Enter}");
    expect(sent("/auth/register")).toHaveLength(1);
    release();
    await screen.findByRole("heading", { name: "Projects" });
  });
});

describe("login", () => {
  it("FE2-F11: a wrong password shows 'Wrong email or password' without a redirect; Enter submits", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await user.type(screen.getByLabelText("Email"), "sangram@example.com");
    await user.type(screen.getByLabelText("Password"), "Wrong-pass1{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password");
    expect(currentPath()).toBe("/login");
    expect(sent("/auth/login")).toHaveLength(1);
  });

  it("FE2-X11: 429 shows the message with the waiting time", async () => {
    server.use(http.post(api("/auth/login"), () =>
      errorReply(429, "RATE_LIMITED", "Too many attempts. Try again in 840 seconds.")));
    const user = userEvent.setup();
    renderApp("/login");
    await user.type(screen.getByLabelText("Email"), "sangram@example.com");
    await user.type(screen.getByLabelText("Password"), "Str0ng-pass{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again in 840 seconds.");
  });

  it("FE2-S12: password fields are hidden, have show/hide and the right autocomplete", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    const password = await screen.findByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "email");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("FE2-S12: new-password fields use autocomplete=new-password", async () => {
    renderApp("/register");
    expect(await screen.findByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
  });

  it("FE2-S11: a backend message with HTML is shown as text, never as HTML", async () => {
    server.use(http.post(api("/auth/login"), () =>
      errorReply(400, "VALIDATION_ERROR", '<img src=x onerror="window.hacked=1"><b>bold</b>')));
    const user = userEvent.setup();
    const view = renderApp("/login");
    await user.type(screen.getByLabelText("Email"), "sangram@example.com");
    await user.type(screen.getByLabelText("Password"), "Str0ng-pass{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent('<img src=x onerror="window.hacked=1"><b>bold</b>');
    expect(view.container.querySelector("img, b")).toBeNull();
    expect(window.hacked).toBeUndefined();
  });
});

describe("unverified users", () => {
  it.each(["/projects", "/account", "/runs", "/runs/5"])("FE2-F12: the banner with Resend is on %s", async (path) => {
    state.user = UNVERIFIED;
    saveToken(GOOD_TOKEN);
    renderApp(path);
    const banner = await screen.findByRole("region", { name: "Email not verified" });
    expect(banner).toHaveTextContent("Verify your email to start runs");
    expect(within(banner).getByRole("button", { name: "resend" })).toBeEnabled();
  });

  it("FE2-F12: Resend in the banner sends the email and says so", async () => {
    state.user = UNVERIFIED;
    saveToken(GOOD_TOKEN);
    const user = userEvent.setup();
    renderApp("/projects");
    const banner = await screen.findByRole("region", { name: "Email not verified" });
    await user.click(within(banner).getByRole("button", { name: "resend" }));
    expect(await within(banner).findByText("We sent a new verification link.")).toBeInTheDocument();
    expect(sent("/auth/resend-verification")).toHaveLength(1);
  });

  it("FE2-X11: TOO_SOON on resend shows the waiting time", async () => {
    state.user = UNVERIFIED;
    saveToken(GOOD_TOKEN);
    server.use(http.post(api("/auth/resend-verification"), () =>
      errorReply(429, "TOO_SOON", "A link was sent a moment ago. Try again in 42 seconds.")));
    const user = userEvent.setup();
    renderApp("/projects");
    const banner = await screen.findByRole("region", { name: "Email not verified" });
    await user.click(within(banner).getByRole("button", { name: "resend" }));
    expect(await within(banner).findByText("A link was sent a moment ago. Try again in 42 seconds.")).toBeInTheDocument();
  });

  it("a verified user sees no banner", async () => {
    saveToken(GOOD_TOKEN);
    renderApp("/projects");
    await screen.findByRole("heading", { name: "Projects" });
    expect(screen.queryByText("Verify your email to start runs")).not.toBeInTheDocument();
  });
});

describe("verify email", () => {
  it("FE2-F13: a good token -> success; refreshUser when logged in; the token leaves the address bar", async () => {
    state.user = UNVERIFIED;
    saveToken(GOOD_TOKEN);
    server.use(http.post(api("/auth/verify-email"), async ({ request }) => {
      expect(await request.json()).toEqual({ token: LINK_TOKEN });
      state.user = VERIFIED;
      return HttpResponse.json({ data: { message: "Your email is verified." } });
    }));
    renderApp(`/verify-email?token=${LINK_TOKEN}`);
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeInTheDocument();
    expect(currentPath()).toBe("/verify-email");
    expect(screen.getByRole("link", { name: "go to projects" })).toBeInTheDocument();
    expect(sent("/auth/me")).toHaveLength(2); // the start, then refreshUser
    expect(screen.queryByText("Verify your email to start runs")).not.toBeInTheDocument();
  });

  it("FE2-F13: logged out -> success, and the button reads 'log in'", async () => {
    server.use(http.post(api("/auth/verify-email"), () => HttpResponse.json({ data: { message: "ok" } })));
    renderApp(`/verify-email?token=${LINK_TOKEN}`);
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "log in" })).toHaveAttribute("href", "/login");
  });

  it.each([
    ["expired or used", () => errorReply(400, "TOKEN_INVALID_OR_EXPIRED", "This link is invalid or has expired.")],
    ["wrong length", () => errorReply(422, "VALIDATION_ERROR", "Invalid request. token: String should have 43 characters")],
  ])("FE2-X12: a %s token -> a clear error, 'go to account' when logged in", async (_name, reply) => {
    saveToken(GOOD_TOKEN);
    state.user = UNVERIFIED;
    server.use(http.post(api("/auth/verify-email"), reply));
    renderApp(`/verify-email?token=${LINK_TOKEN}`);
    expect(await screen.findByRole("heading", { name: "This link is invalid or expired" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "go to account" })).toHaveAttribute("href", "/account");
  });

  it("FE2-X12: no token at all -> the same error, no request", async () => {
    renderApp("/verify-email");
    expect(await screen.findByRole("heading", { name: "This link is invalid or expired" })).toBeInTheDocument();
    expect(sent("/auth/verify-email")).toHaveLength(0);
  });

  it("FE2-X13: in development mode (the page renders twice) the call is sent exactly once", async () => {
    server.use(http.post(api("/auth/verify-email"), () => HttpResponse.json({ data: { message: "ok" } })));
    renderApp(`/verify-email?token=${LINK_TOKEN}`, { strict: true });
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeInTheDocument();
    expect(sent("/auth/verify-email")).toHaveLength(1);
  });
});

describe("forgot and reset password", () => {
  it.each(["sangram@example.com", "nobody@example.com"])("FE2-F14: the same message for %s", async (email) => {
    server.use(http.post(api("/auth/forgot-password"), () =>
      HttpResponse.json({ data: { message: "If this email is registered, we sent a link." } })));
    const user = userEvent.setup();
    renderApp("/forgot-password");
    await user.type(screen.getByLabelText("Email"), `${email}{Enter}`);
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(screen.getByText("If this email exists, we sent a link")).toBeInTheDocument();
  });

  it("FE2-X11: 429 on forgot shows the waiting time", async () => {
    server.use(http.post(api("/auth/forgot-password"), () =>
      errorReply(429, "RATE_LIMITED", "Too many attempts. Try again in 3500 seconds.")));
    const user = userEvent.setup();
    renderApp("/forgot-password");
    await user.type(screen.getByLabelText("Email"), "sangram@example.com{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 3500 seconds.");
  });

  it("FE2-F15: reset with rules and a confirm field; success goes to the login page", async () => {
    server.use(http.post(api("/auth/reset-password"), async ({ request }) => {
      expect(await request.json()).toEqual({ token: LINK_TOKEN, new_password: "N3w-pass-word" });
      return HttpResponse.json({ data: { message: "Your password was changed." } });
    }));
    const user = userEvent.setup();
    renderApp(`/reset-password?token=${LINK_TOKEN}`);
    await screen.findByLabelText("New password");
    expect(currentPath()).toBe("/reset-password"); // the token left the address bar
    expect(screen.getByText("at least one letter and one digit")).toBeInTheDocument();
    await user.type(screen.getByLabelText("New password"), "N3w-pass-word");
    await user.type(screen.getByLabelText("Confirm new password"), "N3w-pass-word{Enter}");
    expect(await screen.findByText("Password changed, please log in")).toBeInTheDocument();
    expect(currentPath()).toBe("/login");
  });

  it.each([
    ["N3w-pass-word", "N3w-pass-wordX", "Confirm new password", "The two passwords are not the same."],
    ["weak", "weak", "New password", "Password must be at least 8 characters."],
  ])("FE2-X14: %s / %s -> error, nothing sent", async (password, confirm, label, message) => {
    const user = userEvent.setup();
    renderApp(`/reset-password?token=${LINK_TOKEN}`);
    await user.type(await screen.findByLabelText("New password"), password);
    await user.type(screen.getByLabelText("Confirm new password"), `${confirm}{Enter}`);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText(label)).toHaveFocus();
    expect(sent("/auth/reset-password")).toHaveLength(0);
  });

  it("FE2-X14: an expired token -> 'This link is invalid or expired' with a link for a new one", async () => {
    server.use(http.post(api("/auth/reset-password"), () =>
      errorReply(400, "TOKEN_INVALID_OR_EXPIRED", "This link is invalid or has expired.")));
    const user = userEvent.setup();
    renderApp(`/reset-password?token=${LINK_TOKEN}`);
    await user.type(await screen.findByLabelText("New password"), "N3w-pass-word");
    await user.type(screen.getByLabelText("Confirm new password"), "N3w-pass-word{Enter}");
    expect(await screen.findByText("This link is invalid or expired")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /get a new link/ })).toHaveAttribute("href", "/forgot-password");
  });

  it("FE2-X14: opened without a token -> the error at once, the button is disabled", async () => {
    renderApp("/reset-password");
    expect(await screen.findByText("This link is invalid or expired")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "save new password" })).toBeDisabled();
  });
});

describe("account", () => {
  it("FE2-F16: shows the email and the state; Resend only when not verified", async () => {
    saveToken(GOOD_TOKEN);
    const view = renderApp("/account");
    expect(await screen.findByDisplayValue("sangram@example.com")).toHaveAttribute("readonly");
    expect(screen.getByText("verified")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "resend verification email" })).not.toBeInTheDocument();
    view.unmount();
    state.user = UNVERIFIED;
    renderApp("/account");
    expect(await screen.findByText("not verified")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "resend verification email" })).toBeInTheDocument();
  });

  async function changePassword(user, old, next, confirm = next) {
    await user.type(await screen.findByLabelText("Old password"), old);
    await user.type(screen.getByLabelText("New password"), next);
    await user.type(screen.getByLabelText("Confirm new password"), confirm);
    await user.click(screen.getByRole("button", { name: "change password" }));
  }

  it("FE2-F17: a change logs the user out with 'Password changed, please log in again'", async () => {
    saveToken(GOOD_TOKEN);
    server.use(http.post(api("/auth/change-password"), () => HttpResponse.json({ data: { message: "ok" } })));
    const user = userEvent.setup();
    renderApp("/account");
    await changePassword(user, "Str0ng-pass", "N3w-pass-word");
    expect(await screen.findByText("Password changed, please log in again")).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("FE2-X15: a wrong old password -> an error under the form, the user stays logged in", async () => {
    saveToken(GOOD_TOKEN);
    server.use(http.post(api("/auth/change-password"), () =>
      errorReply(400, "WRONG_OLD_PASSWORD", "The old password is not correct.")));
    const user = userEvent.setup();
    renderApp("/account");
    await changePassword(user, "Wrong-pass1", "N3w-pass-word");
    expect(await screen.findByRole("alert")).toHaveTextContent("The old password is not correct.");
    expect(screen.getByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(GOOD_TOKEN);
  });

  it("FE2-X14 (account): a mismatching confirm is refused before sending", async () => {
    saveToken(GOOD_TOKEN);
    const user = userEvent.setup();
    renderApp("/account");
    await changePassword(user, "Str0ng-pass", "N3w-pass-word", "different-1");
    expect(await screen.findByText("The two passwords are not the same.")).toBeInTheDocument();
    expect(sent("/auth/change-password")).toHaveLength(0);
  });
});

describe("plain text and labels", () => {
  it("FE2-S10: a name with script text never becomes HTML", async () => {
    state.user = { ...UNVERIFIED, name: "<script>window.hacked=2</script>" };
    saveToken(GOOD_TOKEN);
    const view = renderApp("/account");
    await screen.findByRole("heading", { name: "Account" });
    expect(view.container.querySelector("script")).toBeNull();
    expect(window.hacked).toBeUndefined();
  });

  it.each(["/login", "/register", "/forgot-password", "/reset-password?token=x"])(
    "FE2-U02: every input on %s has a label", async (path) => {
      const view = renderApp(path);
      await waitFor(() => expect(view.container.querySelectorAll("input").length).toBeGreaterThan(0));
      for (const input of view.container.querySelectorAll("input")) {
        expect(input.labels.length, input.outerHTML).toBe(1);
      }
    });
});
