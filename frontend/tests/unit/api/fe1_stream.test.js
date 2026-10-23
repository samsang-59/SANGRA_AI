// @vitest-environment node
// FE1: the live stream helper (F20-F28, X20-X25, S20, L01-L03).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "../../../src/config.js";
import { setUnauthorizedHandler } from "../../../src/api/client.js";
import { clearToken, getToken, setToken } from "../../../src/api/token.js";
import { createSseParser, openRunStream } from "../../../src/api/stream.js";
import { installStorage } from "./helpers.js";
import { finalEvent, installFakeStreamBackend, settle, sseEvent } from "./fake_stream.js";

const realFetch = globalThis.fetch;
let backend;
let calls;

function open(runId = 42) {
  calls = { events: [], end: 0, errors: [], reconnecting: [], bad: [] };
  const stop = openRunStream(runId, (event) => calls.events.push(event), {
    onEnd: () => (calls.end += 1),
    onError: (error) => calls.errors.push(error),
    onReconnecting: (attempt, delay) => calls.reconnecting.push([attempt, delay]),
    onBadEvent: (text) => calls.bad.push(text),
  });
  return stop;
}

const sequences = () => calls.events.map((e) => e.sequence_number);

beforeEach(() => {
  installStorage();
  clearToken();
  setToken("header.payload.signature");
  backend = installFakeStreamBackend();
});
afterEach(() => {
  vi.useRealTimers();
  globalThis.fetch = realFetch;
  setUnauthorizedHandler(null);
});

describe("reading events", () => {
  it("FE1-F20: one complete event", async () => {
    open();
    await settle();
    backend.push(sseEvent(1, "status_changed", { message: "Run started.", details: { status: "started" } }));
    await settle();
    expect(calls.events).toEqual([{ sequence_number: 1, event_type: "status_changed", message: "Run started.",
      details: { status: "started" }, attempt_id: null, created_at: "2026-10-22T14:05:00Z" }]);
  });

  it("FE1-F21: an event cut in the middle of a word, of a multi-byte character, and between the two newlines", async () => {
    open();
    await settle();
    const text = sseEvent(1, "tool_result", { message: "Gefunden: Größe 🐛 naïve" });
    const bytes = new TextEncoder().encode(text);
    const emoji = bytes.findIndex((b, i) => b === 0xf0 && i > 10); // the first byte of 🐛
    const cuts = [10, emoji + 2, bytes.length - 1]; // a word, inside the 4-byte emoji, between "\n" and "\n"
    let from = 0;
    for (const cut of [...cuts, bytes.length]) {
      backend.push(bytes.slice(from, cut));
      from = cut;
      await settle();
    }
    expect(calls.events).toHaveLength(1);
    expect(calls.events[0].message).toBe("Gefunden: Größe 🐛 naïve");
  });

  it("FE1-F22: many events in one chunk, all in order", async () => {
    open();
    await settle();
    backend.push(Array.from({ length: 50 }, (_, i) => sseEvent(i + 1)).join(""));
    await settle();
    expect(sequences()).toEqual(Array.from({ length: 50 }, (_, i) => i + 1));
  });

  it("FE1-F23: CRLF endings, data in several lines, unknown fields", async () => {
    open();
    await settle();
    const json = JSON.stringify({ sequence_number: 1, event_type: "tool_called", message: "m", details: {} });
    const half = Math.floor(json.length / 2);
    backend.push(`retry: 3000\r\nfoo: bar\r\nid: 1\r\nevent: tool_called\r\ndata: ${json.slice(0, half)}\r`);
    await settle();
    backend.push(`\ndata: ${json.slice(half)}\r\n\r\n`);
    await settle();
    // Two data lines are joined with "\n" (SSE rule); JSON allows that whitespace only between tokens, so check
    // the parser directly for the joined text, and that the event above is reported as bad, not mixed up.
    const seen = [];
    const feed = createSseParser((m) => seen.push(m));
    feed("id: 7\r\nevent: x\r\ndata: line one\r\ndata: line two\r\nunknown: y\r\n\r\n");
    expect(seen).toEqual([{ id: "7", event: "x", data: "line one\nline two" }]);
    expect(calls.events.length + calls.bad.length).toBe(1);
  });

  it("FE1-F23: a multi-line data field that is valid JSON", async () => {
    open();
    await settle();
    backend.push('id: 1\ndata: {"sequence_number": 1,\ndata: "event_type": "tool_called", "message": "two lines"}\n\n');
    await settle();
    expect(calls.events.map((e) => e.message)).toEqual(["two lines"]);
  });

  it("FE1-F24: ping lines are ignored", async () => {
    open();
    await settle();
    backend.push(": ping\n\n: ping\n\n");
    backend.push(sseEvent(1));
    backend.push(": ping\n\n");
    await settle();
    expect(sequences()).toEqual([1]);
    expect(calls.bad).toEqual([]);
  });

  it("FE1-F25: the final status_changed event ends the stream: onEnd once, no reconnect", async () => {
    vi.useFakeTimers();
    open();
    await settle();
    backend.push(sseEvent(1) + finalEvent(2, "failed") + sseEvent(3));
    await settle();
    expect(sequences()).toEqual([1, 2]);
    expect(calls.end).toBe(1);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
    expect(backend.last().aborted || backend.last().cancelled).toBe(true);
  });

  it.each(["searching", "waiting_for_approval", "testing"])("a status_changed to %s does not end it", async (status) => {
    open();
    await settle();
    backend.push(sseEvent(1, "status_changed", { details: { status } }));
    await settle();
    expect(calls.end).toBe(0);
  });
});

describe("reconnecting", () => {
  it("FE1-F26 / F27: a drop -> onReconnecting, then Last-Event-ID = the last number; repeats are ignored", async () => {
    vi.useFakeTimers();
    open();
    await settle();
    backend.push(sseEvent(1) + sseEvent(2) + sseEvent(3));
    await settle();
    backend.drop();
    await settle();
    expect(calls.reconnecting).toEqual([[1, 2000]]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(backend.fetch).toHaveBeenCalledTimes(2);
    expect(backend.last().headers["Last-Event-ID"]).toBe("3");
    expect(backend.connections[0].headers["Last-Event-ID"]).toBeUndefined();
    backend.push(sseEvent(2) + sseEvent(3) + sseEvent(4)); // the server sends some again
    await settle();
    expect(sequences()).toEqual([1, 2, 3, 4]);
  });

  it("FE1-F28: the waits between reconnects are 2 s, 4 s, 8 s, 16 s, 32 s", async () => {
    vi.useFakeTimers();
    backend.replies.push("network", "network", "network", "network", "network");
    open();
    await settle();
    const times = [];
    for (const wait of [2000, 4000, 8000, 16000]) {
      const before = backend.fetch.mock.calls.length;
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(backend.fetch.mock.calls.length).toBe(before); // not one millisecond early
      await vi.advanceTimersByTimeAsync(1);
      expect(backend.fetch.mock.calls.length).toBe(before + 1);
      times.push(wait);
    }
    expect(calls.reconnecting.map(([, delay]) => delay)).toEqual([2000, 4000, 8000, 16000, 32000]);
  });

  it("FE1-X20: after 5 failed reconnects onError is called once and it stops", async () => {
    vi.useFakeTimers();
    backend.replies.push(...Array(10).fill("network"));
    open();
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(backend.fetch).toHaveBeenCalledTimes(6); // the first try and 5 reconnects
    expect(calls.errors).toHaveLength(1);
    expect(calls.errors[0].code).toBe("NETWORK_ERROR");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("a reconnect that works again resets the count", async () => {
    vi.useFakeTimers();
    open();
    await settle();
    for (let round = 0; round < 8; round += 1) {
      backend.push(sseEvent(round + 1));
      await settle();
      backend.drop();
      await settle();
      await vi.advanceTimersByTimeAsync(2000);
    }
    expect(calls.errors).toEqual([]);
    expect(calls.reconnecting.every(([attempt]) => attempt === 1)).toBe(true);
    expect(sequences()).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it("FE1-X24: the server closes cleanly without the final event -> treated as a drop", async () => {
    vi.useFakeTimers();
    open();
    await settle();
    backend.push(sseEvent(1));
    backend.close();
    await settle();
    expect(calls.reconnecting).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(backend.last().headers["Last-Event-ID"]).toBe("1");
  });

  it("a 5xx answer is retried like a drop", async () => {
    vi.useFakeTimers();
    backend.replies.push({ status: 503, body: { error: { code: "SERVICE_UNAVAILABLE", message: "down" } } });
    open();
    await settle();
    expect(calls.reconnecting).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(backend.fetch).toHaveBeenCalledTimes(2);
  });
});

describe("failures", () => {
  it("FE1-X21: data that is not valid JSON is skipped and reported; the stream goes on", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    open();
    await settle();
    backend.push(sseEvent(1) + "id: 2\nevent: tool_called\ndata: {not json\n\n" + sseEvent(3));
    await settle();
    expect(sequences()).toEqual([1, 3]);
    expect(calls.bad).toEqual(["{not json"]);
    // Without an onBadEvent callback it goes to the console instead.
    const events = [];
    openRunStream(43, (e) => events.push(e));
    await settle();
    backend.push("data: {broken\n\n" + sseEvent(1));
    await settle();
    expect(events).toHaveLength(1);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it("FE1-X22: 401 on the stream follows the 401 rule, no reconnect", async () => {
    vi.useFakeTimers();
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    backend.replies.push({ status: 401, body: { error: { code: "UNAUTHORIZED", message: "Login required." } } });
    open();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
    expect(calls.errors.map((e) => e.code)).toEqual(["UNAUTHORIZED"]);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
  });

  it("FE1-X23: 404 (not your run) -> onError NOT_FOUND, no reconnect", async () => {
    vi.useFakeTimers();
    backend.replies.push({ status: 404, body: { error: { code: "NOT_FOUND", message: "Not found." } } });
    open();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.errors.map((e) => [e.status, e.code])).toEqual([[404, "NOT_FOUND"]]);
    expect(calls.reconnecting).toEqual([]);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
  });

  it("FE1-X25: stop() before the response arrives", async () => {
    backend.replies.push("hang");
    const stop = open();
    await settle();
    stop();
    await settle();
    expect(backend.last().aborted).toBe(true);
    expect(calls).toEqual({ events: [], end: 0, errors: [], reconnecting: [], bad: [] });
  });

  it("FE1-X25: stop() in the middle of a chunk, and twice", async () => {
    vi.useFakeTimers();
    const stop = open();
    await settle();
    const text = sseEvent(1) + sseEvent(2);
    backend.push(text.slice(0, text.length - 30)); // event 2 is half there
    await settle();
    stop();
    stop();
    expect(() => backend.push(text.slice(-30))).toThrow(); // the connection is closed
    await vi.advanceTimersByTimeAsync(120_000);
    expect(sequences()).toEqual([1]);
    expect(calls.reconnecting).toEqual([]);
    expect(calls.errors).toEqual([]);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
  });

  it("FE1-X25: stop() while waiting to reconnect cancels the reconnect", async () => {
    vi.useFakeTimers();
    backend.replies.push("network");
    const stop = open();
    await settle();
    expect(calls.reconnecting).toHaveLength(1);
    stop();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("no callback runs after onEvent calls stop()", async () => {
    let stop = null;
    const seen = [];
    stop = openRunStream(42, (event) => {
      seen.push(event.sequence_number);
      stop();
    }, { onEnd: () => seen.push("end") });
    await settle();
    backend.push(sseEvent(1) + finalEvent(2));
    await settle();
    expect(seen).toEqual([1]);
  });
});

describe("security", () => {
  it("FE1-S20: the stream goes only to our API address, with the token in the header (never the URL)", async () => {
    open(42);
    await settle();
    expect(backend.last().url).toBe(`${API_URL}/runs/42/events`);
    expect(backend.last().headers.Authorization).toBe("Bearer header.payload.signature");
    expect(backend.last().url).not.toContain("signature");
  });

  it("a strange run id cannot change the address", async () => {
    open("../../auth/me?x=");
    await settle();
    expect(backend.last().url).toBe(`${API_URL}/runs/..%2F..%2Fauth%2Fme%3Fx%3D/events`);
  });
});

describe("big data", () => {
  it("FE1-L01: 10,000 events are processed in under 2 seconds", async () => {
    open();
    await settle();
    const started = performance.now();
    let text = "";
    for (let i = 1; i <= 10_000; i += 1) {
      text += sseEvent(i, "tool_result", { message: `step ${i}: found ${i % 7} places` });
      if (i % 500 === 0) {
        backend.push(text);
        text = "";
      }
    }
    await settle(50);
    await vi.waitFor(() => expect(calls.events).toHaveLength(10_000));
    const ms = performance.now() - started;
    console.log(`FE1-L01: 10,000 events in ${ms.toFixed(0)} ms`);
    expect(ms).toBeLessThan(2000);
    expect(sequences()[9999]).toBe(10_000);
  });

  it("FE1-L02: one 1 MB event arriving in chunks of 1 byte", async () => {
    const message = "x".repeat(1024 * 1024 - 300) + "€✓";
    const bytes = new TextEncoder().encode(sseEvent(1, "tool_result", { message }));
    backend.replies.push({ oneBytePerRead: bytes });
    const started = performance.now();
    open();
    await vi.waitFor(() => expect(calls.events).toHaveLength(1), { timeout: 30_000 });
    console.log(`FE1-L02: ${bytes.length} one-byte chunks in ${(performance.now() - started).toFixed(0)} ms`);
    expect(calls.events[0].message).toBe(message);
  }, 60_000);

  it("FE1-L03: 100 open/close cycles leave no open connection, reader or timer behind", async () => {
    vi.useFakeTimers();
    for (let i = 0; i < 100; i += 1) {
      const stop = open(i + 1);
      await settle(3);
      if (i % 3 === 0) backend.push(sseEvent(1));
      if (i % 3 === 1) backend.drop(); // also while a reconnect is waiting
      await settle(3);
      stop();
    }
    await settle();
    expect(backend.connections).toHaveLength(100);
    expect(backend.connections.every((c) => c.aborted)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});
