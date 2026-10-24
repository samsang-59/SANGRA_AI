// A fake backend for the live stream tests: every fetch to /runs/{id}/events becomes a "connection" the test drives
// chunk by chunk (push, drop, close), or answers with an error status or a network failure.
import { vi } from "vitest";

const encoder = new TextEncoder();

export function sseEvent(sequence, eventType = "tool_called", extra = {}) {
  const data = { sequence_number: sequence, event_type: eventType, message: `event ${sequence}`, details: {},
    attempt_id: null, created_at: "2026-10-22T14:05:00Z", ...extra };
  return `id: ${sequence}\nevent: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

export const finalEvent = (sequence, status = "succeeded") =>
  sseEvent(sequence, "status_changed", { details: { status } });

export function installFakeStreamBackend() {
  const connections = [];
  const replies = []; // queued answers for the next connections: { status, body } or "network"

  const fetchMock = vi.fn((url, init = {}) => {
    const connection = { url: String(url), headers: { ...(init.headers || {}) }, aborted: false, cancelled: false,
      controller: null };
    connections.push(connection);
    const reply = replies.shift();
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        connection.aborted = true;
        const error = new DOMException("The operation was aborted.", "AbortError");
        try {
          connection.controller?.error(error);
        } catch {
          // already closed
        }
        reject(error);
      };
      if (init.signal?.aborted) return onAbort();
      init.signal?.addEventListener("abort", onAbort);
      if (reply === "network") return reject(new TypeError("fetch failed"));
      if (reply === "hang") return undefined; // never answers (until aborted)
      if (reply?.oneBytePerRead) {
        // Hands out the bytes one at a time, each when the reader asks (as a slow network would). Queuing a
        // million chunks up front instead makes Node's stream queue itself slow.
        const bytes = reply.oneBytePerRead;
        let at = 0;
        const body = new ReadableStream({
          pull(controller) {
            connection.controller = controller;
            if (at < bytes.length) controller.enqueue(bytes.subarray(at, ++at));
          },
          cancel() {
            connection.cancelled = true;
          },
        }, { highWaterMark: 0 });
        return resolve(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
      }
      if (reply) {
        return resolve(new Response(JSON.stringify(reply.body ?? {}), { status: reply.status,
          headers: { "Content-Type": "application/json" } }));
      }
      const body = new ReadableStream({
        start(controller) {
          connection.controller = controller;
        },
        cancel() {
          connection.cancelled = true;
        },
      });
      resolve(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    });
  });

  const backend = {
    fetch: fetchMock,
    connections,
    replies,
    last: () => connections[connections.length - 1],
    push(text, connection = backend.last()) {
      connection.controller.enqueue(typeof text === "string" ? encoder.encode(text) : text);
    },
    drop(connection = backend.last()) {
      connection.controller.error(new TypeError("network connection lost"));
    },
    close(connection = backend.last()) {
      connection.controller.close();
    },
    open: () => connections.filter((c) => !c.aborted && !c.cancelled && !c.closed),
  };
  globalThis.fetch = fetchMock;
  return backend;
}

/** Lets pending promises and stream reads run (works with real and fake timers). */
export async function settle(times = 20) {
  for (let i = 0; i < times; i += 1) await new Promise((resolve) => queueMicrotask(resolve));
}
