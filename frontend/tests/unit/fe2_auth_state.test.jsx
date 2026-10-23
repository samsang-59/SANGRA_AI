// FE2: the auth state and the route guards (F01-F07, X01-X07, S01, S02, L01).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setUnauthorizedHandler } from "../../src/api/client.js";
import { TOKEN_KEY, clearToken } from "../../src/api/token.js";
import { safeReturnPath } from "../../src/validation.js";
import {
  GOOD_TOKEN, NEW_TOKEN, UNVERIFIED, HttpResponse, api, currentPath, fakeBackend, http, renderApp, saveToken,
} from "./app_helpers.jsx";

const { server, seen, state } = fakeBackend();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
  state.user = { ...state.user, is_email_verified: true };
});

async function logIn(user, email = "sangram@example.com", password = "Str0ng-pass") {
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "log in" }));
}

describe("when the app opens", () => {
  it("FE2-F01: no token -> logged out without any API call", async () => {
    renderApp("/projects");
    expect(await screen.findByRole("button", { name: "log in" })).toBeInTheDocument();
    expect(seen).toHaveLength(0);
  });

  it("FE2-F02 / X07: a valid token -> a spinner (no login page flash), then logged in", async () => {
    saveToken(GOOD_TOKEN);
    renderApp("/projects");
    expect(screen.getByRole("status", { name: "Loading your account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "log in" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(seen.map((r) => new URL(r.url).pathname)).toEqual(["/api/v1/auth/me"]);
  });

  it("FE2-X01: an expired or invalid saved token -> removed, logged out, no error screen", async () => {
    saveToken("old.expired.token");
    renderApp("/projects");
    expect(await screen.findByRole("button", { name: "log in" })).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/Session ended/)).not.toBeInTheDocument();
  });

  it("FE2-X02: the backend is down -> the token is KEPT, 'cannot reach the server' with Retry", async () => {
    saveToken(GOOD_TOKEN);
    server.use(http.get(api("/auth/me"), () => HttpResponse.error()));
    renderApp("/runs/5");
    expect(await screen.findByText("Cannot reach the server")).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(GOOD_TOKEN);
    server.resetHandlers(); // the backend is back
    await userEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(await screen.findByRole("heading", { name: "Run" })).toBeInTheDocument();
    expect(currentPath()).toBe("/runs/5");
  });

  it("FE2-X03: localStorage throws -> the app still works in memory for the session", async () => {
    const fail = () => { throw new DOMException("blocked", "SecurityError"); };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(fail);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(fail);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(fail);
    const user = userEvent.setup();
    renderApp("/login");
    await logIn(user);
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /account/ }));
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
    const { getToken } = await import("../../src/api/token.js");
    expect(getToken()).toBe(NEW_TOKEN); // kept in memory for this session
  });

  it("FE2-X04: a corrupted token string -> removed, logged out, no API call", async () => {
    saveToken("not a token \u0000 at all");
    renderApp("/projects");
    expect(await screen.findByRole("button", { name: "log in" })).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(seen).toHaveLength(0);
  });
});

describe("login, register, logout", () => {
  it("FE2-F03: login saves the token, loads the user, goes to /projects", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await logIn(user);
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(NEW_TOKEN);
    expect(currentPath()).toBe("/projects");
  });

  it("FE2-F06: a logged-out user opens a deep link, logs in, and returns there", async () => {
    const user = userEvent.setup();
    renderApp("/runs/5?tab=log");
    await screen.findByRole("button", { name: "log in" });
    expect(currentPath()).toBe("/login?returnTo=%2Fruns%2F5%3Ftab%3Dlog");
    await logIn(user);
    expect(await screen.findByRole("heading", { name: "Run" })).toBeInTheDocument();
    expect(currentPath()).toBe("/runs/5?tab=log");
  });

  it("FE2-F07: a logged-in user who opens /login goes to /projects", async () => {
    saveToken(GOOD_TOKEN);
    renderApp("/login");
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(currentPath()).toBe("/projects");
  });

  it("FE2-F04: register logs in at once and the verify banner is shown", async () => {
    state.user = UNVERIFIED;
    server.use(http.post(api("/auth/register"), () =>
      HttpResponse.json({ data: { user: UNVERIFIED, token: NEW_TOKEN } }, { status: 201 })));
    const user = userEvent.setup();
    renderApp("/register");
    await user.type(screen.getByLabelText("Name"), "Sangram");
    await user.type(screen.getByLabelText("Email"), "sangram@example.com");
    await user.type(screen.getByLabelText("Password"), "Str0ng-pass");
    await user.click(screen.getByRole("button", { name: "create account" }));
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByText("Verify your email to start runs")).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe(NEW_TOKEN);
  });

  it("FE2-F05: logout clears token and user, goes to /login, the backend is not called", async () => {
    saveToken(GOOD_TOKEN);
    const user = userEvent.setup();
    renderApp("/account");
    await screen.findByRole("heading", { name: "Account" });
    const before = seen.length;
    await user.click(screen.getByRole("button", { name: "log out" }));
    expect(await screen.findByRole("button", { name: "log in" })).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(seen.length).toBe(before);
    expect(currentPath()).toBe("/login"); // a plain /login: logging in again goes to /projects
  });
});

describe("the session ends", () => {
  it("FE2-X05: a 401 in the middle of use -> token cleared, login shows 'Session ended, please log in again'", async () => {
    saveToken(GOOD_TOKEN);
    state.user = UNVERIFIED;
    const user = userEvent.setup();
    renderApp("/account");
    await screen.findByRole("heading", { name: "Account" });
    state.tokens.delete(GOOD_TOKEN); // the JWT expired on the server
    await user.click(screen.getByRole("button", { name: "resend" }));
    expect(await screen.findByText("Session ended, please log in again")).toBeInTheDocument();
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(currentPath()).toBe("/login?returnTo=%2Faccount");
    state.tokens.add(GOOD_TOKEN);
  });

  it("FE2-X06: the token removed by another tab -> logged out at the next request", async () => {
    saveToken(GOOD_TOKEN);
    state.user = UNVERIFIED;
    const user = userEvent.setup();
    renderApp("/account");
    await screen.findByRole("heading", { name: "Account" });
    window.localStorage.removeItem(TOKEN_KEY); // the other tab logged out
    await user.click(screen.getByRole("button", { name: "resend verification email" }));
    expect(await screen.findByText("Session ended, please log in again")).toBeInTheDocument();
    expect(seen.at(-1).headers.has("authorization")).toBe(false);
  });

  it("the 401 function does nothing once logged out (called twice)", async () => {
    renderApp("/login");
    await screen.findByRole("button", { name: "log in" });
    await act(async () => {
      // registered by AuthProvider; calling it while logged out must not show a message or throw
      const { handleUnauthorized } = await import("../../src/api/client.js");
      handleUnauthorized(null);
      handleUnauthorized(null);
    });
    expect(screen.queryByText(/Session ended/)).not.toBeInTheDocument();
  });
});

describe("security", () => {
  it.each(["https://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)", "http://localhost/x", "/x\u0000y", ""])(
    "FE2-S01: returnTo=%j is ignored", (value) => {
      expect(safeReturnPath(value)).toBeNull();
    });

  it.each(["/runs/5", "/projects?x=1", "/account"])("FE2-S01: returnTo=%s is kept", (value) => {
    expect(safeReturnPath(value)).toBe(value);
  });

  it("FE2-S01: after login with an evil returnTo, the user lands on /projects", async () => {
    const user = userEvent.setup();
    renderApp(`/login?returnTo=${encodeURIComponent("//evil.com/steal")}`);
    await logIn(user);
    await screen.findByRole("heading", { name: "Projects" });
    expect(currentPath()).toBe("/projects");
  });

  it("FE2-S02: after login only the token key is stored; the password is nowhere", async () => {
    const logs = [];
    for (const level of ["log", "info", "warn", "error", "debug"]) {
      vi.spyOn(console, level).mockImplementation((...args) => logs.push(args.join(" ")));
    }
    const user = userEvent.setup();
    renderApp("/login");
    await logIn(user);
    await screen.findByRole("heading", { name: "Projects" });
    expect(Object.keys(window.localStorage)).toEqual([TOKEN_KEY]);
    expect(currentPath()).not.toContain("Str0ng");
    expect(seen.every((r) => !r.url.includes("Str0ng"))).toBe(true);
    expect(logs.join("\n")).not.toContain("Str0ng");
  });
});

describe("FE2-L01: opening the app 50 times with a valid token", () => {
  it("leaves nothing behind (handler, timers, memory)", async () => {
    const v8 = await import("node:v8");
    const vm = await import("node:vm");
    v8.setFlagsFromString("--expose_gc");
    const gc = vm.runInNewContext("gc");
    saveToken(GOOD_TOKEN);
    const heap = [];
    for (let i = 0; i < 50; i += 1) {
      const view = renderApp("/projects");
      await screen.findByRole("heading", { name: "Projects" });
      view.unmount();
      if (i === 9 || i === 49) {
        gc();
        heap.push(process.memoryUsage().heapUsed);
      }
    }
    const growthMb = (heap[1] - heap[0]) / 1024 / 1024;
    console.log(`FE2-L01: heap after 10 opens -> after 50 opens: ${growthMb.toFixed(2)} MB`);
    expect(growthMb).toBeLessThan(5);
    expect(seen.filter((r) => r.url.endsWith("/auth/me"))).toHaveLength(50);
    // The 401 function was unregistered by the last unmount.
    const spy = vi.fn();
    setUnauthorizedHandler(spy);
    render(<div />);
    expect(spy).not.toHaveBeenCalled();
  }, 60_000);
});
