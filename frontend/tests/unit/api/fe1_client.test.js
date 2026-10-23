// @vitest-environment node
// FE1: the HTTP client (F01-F06, X01-X07, S01, S02) against a fake backend (MSW).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, REQUEST_TIMEOUT_MS, UPLOAD_TIMEOUT_MS, apiUrl, request, setUnauthorizedHandler } from "../../../src/api/client.js";
import { clearToken, getToken, setToken } from "../../../src/api/token.js";
import * as auth from "../../../src/api/auth.js";
import { HttpResponse, api, http, installStorage, recordingServer } from "./helpers.js";

const { server, seen } = recordingServer();
const TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI3In0.c2lnbmF0dXJl";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  seen.length = 0;
  setUnauthorizedHandler(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());
beforeEach(() => {
  installStorage();
  clearToken();
});

const ok = (data) => () => HttpResponse.json({ data });

describe("headers and bodies", () => {
  it("FE1-F01: a saved token is sent as a Bearer header", async () => {
    server.use(http.get(api("/projects"), ok([])));
    setToken(TOKEN);
    await request("GET", "/projects");
    expect(seen[0].headers.get("authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("FE1-F02: no token, no Authorization header", async () => {
    server.use(http.get(api("/projects"), ok([])));
    await request("GET", "/projects");
    expect(seen[0].headers.has("authorization")).toBe(false);
  });

  it("FE1-F03: an object is sent as JSON", async () => {
    server.use(http.post(api("/projects"), ok({ id: 1 })));
    await request("POST", "/projects", { body: { name: "Shop", language: "java" } });
    expect(seen[0].headers.get("content-type")).toBe("application/json");
    expect(await seen[0].json()).toEqual({ name: "Shop", language: "java" });
  });

  it("FE1-F04: FormData is sent as it is, the content type carries the boundary", async () => {
    server.use(http.post(api("/projects/3/runs"), ok({ id: 9 })));
    const form = new FormData();
    form.append("bug_description", "The summer sale shows 5% discount.");
    form.append("zip_file", new Blob(["PK\u0003\u0004"], { type: "application/zip" }), "shop.zip");
    await request("POST", "/projects/3/runs", { body: form });
    expect(seen[0].headers.get("content-type")).toMatch(/^multipart\/form-data; boundary=/);
    const received = await seen[0].formData();
    expect(received.get("bug_description")).toBe("The summer sale shows 5% discount.");
    expect(received.get("zip_file").name).toBe("shop.zip");
  });

  it("FE1-F05: { data: X } returns X", async () => {
    server.use(http.get(api("/auth/me"), ok({ id: 7, name: "Ann" })));
    expect(await request("GET", "/auth/me")).toEqual({ id: 7, name: "Ann" });
  });

  it("FE1-F06: { error } throws ApiError with status, code, message", async () => {
    server.use(http.post(api("/projects"), () =>
      HttpResponse.json({ error: { code: "PROJECT_LIMIT_REACHED", message: "You can have at most 20 projects." } },
        { status: 409 })));
    const error = await request("POST", "/projects", { body: {} }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.code, error.message]).toEqual([409, "PROJECT_LIMIT_REACHED",
      "You can have at most 20 projects."]);
  });
});

describe("failures", () => {
  it("FE1-X01: the network fails -> NETWORK_ERROR (status 0)", async () => {
    server.use(http.get(api("/projects"), () => HttpResponse.error()));
    const error = await request("GET", "/projects").catch((e) => e);
    expect([error.status, error.code]).toEqual([0, "NETWORK_ERROR"]);
  });

  it.each([
    ["an HTML 502 page", () => new HttpResponse("<html><body>Bad Gateway</body></html>", { status: 502,
      headers: { "Content-Type": "text/html" } })],
    ["an empty error body", () => new HttpResponse(null, { status: 500 })],
    ["broken JSON", () => new HttpResponse('{"error": {"code"', { status: 500 })],
    ["JSON of another shape", () => HttpResponse.json({ detail: "Not Found" }, { status: 404 })],
    ["broken JSON on a 200", () => new HttpResponse("{oops", { status: 200 })],
    ["a 200 without data", () => HttpResponse.json({ items: [] })],
  ])("FE1-X02: %s -> UNKNOWN_ERROR, no crash", async (_name, reply) => {
    server.use(http.get(api("/projects"), reply));
    const error = await request("GET", "/projects").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("UNKNOWN_ERROR");
  });

  it("FE1-X03: 401 on a protected route clears the token and calls the 401 function once", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken(TOKEN);
    server.use(http.get(api("/projects"), () =>
      HttpResponse.json({ error: { code: "UNAUTHORIZED", message: "Login required." } }, { status: 401 })));
    const error = await request("GET", "/projects").catch((e) => e);
    expect(error.code).toBe("UNAUTHORIZED");
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("FE1-X04: 5 requests get 401 at the same moment -> the 401 function is called once", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken(TOKEN);
    server.use(http.get(api("/runs"), async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return HttpResponse.json({ error: { code: "UNAUTHORIZED", message: "Login required." } }, { status: 401 });
    }));
    const results = await Promise.allSettled(Array.from({ length: 5 }, () => request("GET", "/runs")));
    expect(results.every((r) => r.status === "rejected" && r.reason.code === "UNAUTHORIZED")).toBe(true);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["login", () => auth.login("a@example.com", "Wrong-pass1"), "/auth/login"],
    ["register", () => auth.register("A", "a@example.com", "Str0ng-pass"), "/auth/register"],
    ["verify", () => auth.verifyEmail("x".repeat(43)), "/auth/verify-email"],
    ["forgot", () => auth.forgotPassword("a@example.com"), "/auth/forgot-password"],
    ["reset", () => auth.resetPassword("x".repeat(43), "Str0ng-pass"), "/auth/reset-password"],
  ])("FE1-X05: 401 on %s does not call the 401 function; the error reaches the page", async (_name, call, path) => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken(TOKEN);
    server.use(http.post(api(path), () => HttpResponse.json(
      { error: { code: "INVALID_CREDENTIALS", message: "Wrong email or password." } }, { status: 401 })));
    const error = await call().catch((e) => e);
    expect([error.status, error.code, error.message]).toEqual([401, "INVALID_CREDENTIALS", "Wrong email or password."]);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getToken()).toBe(TOKEN);
    expect(seen[0].headers.has("authorization")).toBe(false); // public routes never need the token
  });

  it.each([
    [422, "VALIDATION_ERROR", "Invalid request. name: String should have at most 100 characters"],
    [429, "RATE_LIMITED", "Too many attempts. Try again in 840 seconds."],
    [429, "TOO_SOON", "Please wait 60 seconds before asking for another email."],
    [409, "ACTIVE_RUN_EXISTS", "You already have a run in progress."],
    [410, "PACKAGE_EXPIRED", "The package has expired (packages are kept 7 days)."],
  ])("FE1-X06: %i %s becomes an ApiError with its own code and message", async (status, code, message) => {
    server.use(http.post(api("/projects"), () => HttpResponse.json({ error: { code, message } }, { status })));
    const error = await request("POST", "/projects", { body: {} }).catch((e) => e);
    expect([error.status, error.code, error.message]).toEqual([status, code, message]);
  });

  it("FE1-X07: a request that hangs fails with TIMEOUT after the set time; uploads get longer", async () => {
    expect(REQUEST_TIMEOUT_MS).toBe(30_000);
    expect(UPLOAD_TIMEOUT_MS).toBeGreaterThan(REQUEST_TIMEOUT_MS);
    server.use(http.get(api("/projects"), async ({ request: incoming }) => {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 10_000);
        incoming.signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new Error("aborted"));
        });
      });
      return HttpResponse.json({ data: [] });
    }));
    const started = Date.now();
    const error = await request("GET", "/projects", { timeout: 200 }).catch((e) => e);
    expect([error.status, error.code]).toEqual([0, "TIMEOUT"]);
    expect(Date.now() - started).toBeLessThan(2000);
  });
});

describe("stopping from outside", () => {
  it("a request stopped by the page fails with ABORTED (not a timeout or network error)", async () => {
    server.use(http.post(api("/projects/3/runs"), async ({ request: incoming }) => {
      await new Promise((resolve) => incoming.signal.addEventListener("abort", resolve));
      return HttpResponse.json({ data: {} });
    }));
    const controller = new AbortController();
    const pending = request("POST", "/projects/3/runs", { body: new FormData(), signal: controller.signal });
    setTimeout(() => controller.abort(), 20);
    const error = await pending.catch((e) => e);
    expect([error.status, error.code]).toEqual([0, "ABORTED"]);
  });
});

describe("security", () => {
  it("FE1-S01: the token never appears in a URL, a console log or an error message", async () => {
    const logs = [];
    for (const level of ["log", "info", "warn", "error", "debug"]) {
      vi.spyOn(console, level).mockImplementation((...args) => logs.push(args.join(" ")));
    }
    setToken(TOKEN);
    server.use(
      http.get(api("/projects"), () => HttpResponse.json({ error: { code: "NOT_FOUND", message: "Not found." } },
        { status: 404 })),
      http.get(api("/runs"), () => HttpResponse.error()),
    );
    const errors = await Promise.all([request("GET", "/projects").catch((e) => e),
      request("GET", "/runs", { query: { page: 2 } }).catch((e) => e)]);
    for (const req of seen) expect(req.url).not.toContain(TOKEN);
    for (const error of errors) expect(`${error.message} ${error.code} ${error.stack}`).not.toContain(TOKEN);
    expect(logs.join("\n")).not.toContain(TOKEN);
  });

  it.each(["https://evil.example.com/steal", "//evil.example.com/x", "projects", "http://localhost:8000/api/v1/x"])(
    "FE1-S02: a request to %s is refused before anything is sent", async (path) => {
      setToken(TOKEN);
      const error = await request("GET", path).catch((e) => e);
      expect(error.code).toBe("BAD_PATH");
      expect(seen).toHaveLength(0);
    });

  it("FE1-S02: every request goes to the API address", () => {
    expect(apiUrl("/runs/5")).toBe("http://localhost:8000/api/v1/runs/5");
  });
});

describe("token storage", () => {
  it("works in memory when localStorage is blocked", () => {
    installStorage({ blocked: true });
    setToken(TOKEN);
    expect(getToken()).toBe(TOKEN);
    clearToken();
    expect(getToken()).toBeNull();
  });
});
