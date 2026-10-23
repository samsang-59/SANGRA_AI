// @vitest-environment node
// FE1: the route functions (F10, F12, F13, X10, X11).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "../../../src/config.js";
import { clearToken, setToken } from "../../../src/api/token.js";
import * as runs from "../../../src/api/runs.js";
import { HttpResponse, api, http, installStorage, recordingServer } from "./helpers.js";
import { ROUTE_TABLE } from "./routes_table.js";

const { server, seen } = recordingServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  seen.length = 0;
});
afterAll(() => server.close());
beforeEach(() => {
  installStorage();
  clearToken();
  setToken("header.payload.signature");
});

const PUBLIC = ["register", "login", "verifyEmail", "forgotPassword", "resetPassword"];

describe("FE1-F10: every route function makes the right request", () => {
  it("there are 22 functions", () => {
    expect(ROUTE_TABLE).toHaveLength(22);
  });

  it.each(ROUTE_TABLE)("%s", async (name, call, method, path, body) => {
    server.use(http.all(`${API_URL}/*`, () =>
      name === "downloadPackage"
        ? new HttpResponse(new Uint8Array([80, 75, 3, 4]), { headers: {
          "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="sangra-run-5.zip"' } })
        : HttpResponse.json({ data: { ok: true } })));
    await call();
    expect(seen).toHaveLength(1);
    const req = seen[0];
    expect(req.method).toBe(method);
    expect(req.url).toBe(API_URL + path);
    if (body === undefined) {
      expect(await req.text()).toBe("");
    } else {
      expect(req.headers.get("content-type")).toBe("application/json");
      expect(await req.json()).toEqual(body);
    }
    expect(req.headers.has("authorization")).toBe(!PUBLIC.includes(name));
  });

  it("listRuns leaves out filters that are not set", async () => {
    server.use(http.get(api("/runs"), () => HttpResponse.json({ data: { items: [], total: 0, page: 1, limit: 20 } })));
    await runs.listRuns({});
    await runs.listRuns({ status: "", projectId: null, page: 1 });
    expect(seen.map((r) => r.url)).toEqual([api("/runs"), api("/runs?page=1")]);
  });

  it("submitApproval leaves out an empty feedback", async () => {
    server.use(http.post(api("/runs/5/approval"), () => HttpResponse.json({ data: {} })));
    await runs.submitApproval(5, 11, "accepted");
    expect(await seen[0].json()).toEqual({ attempt_id: 11, decision: "accepted" });
  });
});

describe("FE1-F12: startRun with and without a zip", () => {
  it("with a zip: FormData with bug_description and the file", async () => {
    server.use(http.post(api("/projects/3/runs"), () => HttpResponse.json({ data: { id: 9 } }, { status: 201 })));
    const zip = new File([new Uint8Array([80, 75, 5, 6])], "inventory-tool.zip", { type: "application/zip" });
    expect(await runs.startRun(3, "Stock count goes negative.", zip)).toEqual({ id: 9 });
    expect(seen[0].headers.get("content-type")).toMatch(/^multipart\/form-data; boundary=/);
    const form = await seen[0].formData();
    expect(form.get("bug_description")).toBe("Stock count goes negative.");
    expect(form.get("zip_file").name).toBe("inventory-tool.zip");
    expect(new Uint8Array(await form.get("zip_file").arrayBuffer())).toEqual(new Uint8Array([80, 75, 5, 6]));
  });

  it("without a zip: JSON", async () => {
    server.use(http.post(api("/projects/3/runs"), () => HttpResponse.json({ data: { id: 9 } }, { status: 201 })));
    await runs.startRun(3, "Stock count goes negative.");
    expect(await seen[0].json()).toEqual({ bug_description: "Stock count goes negative." });
  });
});

describe("FE1-F13 / X10: downloadPackage", () => {
  it("fetches with the JWT and saves the file with the server's name", async () => {
    const bytes = new Uint8Array(70_000).map((_, i) => i % 251);
    server.use(http.get(api("/runs/5/package"), () => new HttpResponse(bytes, { headers: {
      "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="sangra-run-5.zip"' } })));
    const save = vi.fn();
    expect(await runs.downloadPackage(5, { save })).toEqual({ name: "sangra-run-5.zip", size: 70_000 });
    expect(seen[0].headers.get("authorization")).toBe("Bearer header.payload.signature");
    const [blob, name] = save.mock.calls[0];
    expect(name).toBe("sangra-run-5.zip");
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
  });

  it.each([[null], ['attachment; filename="../../etc/passwd"'], ["attachment; filename=\"a b.zip\""]])(
    "a missing or odd file name becomes sangra-run-<id>.zip (%s)", (disposition) => {
      expect(runs.fileNameFrom(disposition, 5)).toBe("sangra-run-5.zip");
    });

  it("410 -> ApiError PACKAGE_EXPIRED and nothing is saved", async () => {
    server.use(http.get(api("/runs/5/package"), () => HttpResponse.json(
      { error: { code: "PACKAGE_EXPIRED", message: "The package has expired (packages are kept 7 days)." } },
      { status: 410 })));
    const save = vi.fn();
    const error = await runs.downloadPackage(5, { save }).catch((e) => e);
    expect([error.status, error.code]).toEqual([410, "PACKAGE_EXPIRED"]);
    expect(save).not.toHaveBeenCalled();
  });
});

describe("FE1-X11: every function with a failing backend throws ApiError, never half data", () => {
  it.each(ROUTE_TABLE)("%s", async (name, call) => {
    server.use(http.all(`${API_URL}/*`, () => HttpResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "Something went wrong on the server." } }, { status: 500 })));
    const result = await call().then((value) => ({ value }), (error) => ({ error }));
    expect(result.value).toBeUndefined();
    expect([result.error.name, result.error.status, result.error.code]).toEqual(["ApiError", 500, "INTERNAL_ERROR"]);
  });

  it.each(ROUTE_TABLE)("%s when the network fails", async (name, call) => {
    server.use(http.all(`${API_URL}/*`, () => HttpResponse.error()));
    const error = await call().catch((e) => e);
    expect([error.name, error.code]).toEqual(["ApiError", "NETWORK_ERROR"]);
  });
});
