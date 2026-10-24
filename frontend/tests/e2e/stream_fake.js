// A scripted live stream inside the browser: requests to /runs/{id}/events get a body the test writes to step by step
// (Playwright's page.route can only answer with a whole body at once, which would end the stream). The real stream
// over the network is covered by the real-backend test (tests/real).
export async function installBrowserStream(page) {
  await page.addInitScript(() => {
    const encoder = new TextEncoder();
    const connections = [];
    const realFetch = window.fetch.bind(window);
    window.__sse = {
      connections,
      push(text, index = connections.length - 1) {
        connections[index].controller.enqueue(encoder.encode(text));
      },
      drop(index = connections.length - 1) {
        connections[index].controller.error(new TypeError("network connection lost"));
      },
    };
    window.fetch = (input, init = {}) => {
      const url = String(input instanceof Request ? input.url : input);
      if (!/\/runs\/[^/]+\/events$/.test(url)) return realFetch(input, init);
      const connection = { url, lastEventId: init.headers?.["Last-Event-ID"] ?? null, aborted: false, controller: null };
      connections.push(connection);
      init.signal?.addEventListener("abort", () => {
        connection.aborted = true;
      });
      const body = new ReadableStream({
        start(controller) {
          connection.controller = controller;
        },
      });
      return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    };
  });
}

/** Writes to the newest connection (in development mode React opens, closes and opens again: the last one counts). */
export async function pushEvents(page, text) {
  await page.waitForFunction(() => window.__sse.connections.some((c) => !c.aborted));
  await page.evaluate((t) => window.__sse.push(t), text);
}
export const connectionCount = (page) => page.evaluate(() => window.__sse.connections.length);

/** One SSE event as the backend writes it. */
export function sse(sequence, eventType, message, details = {}) {
  const data = { sequence_number: sequence, event_type: eventType, message, details, attempt_id: null,
    created_at: "2026-10-22T14:05:00Z" };
  return `id: ${sequence}\nevent: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

export const status = (sequence, value) => sse(sequence, "status_changed", `Status: ${value}`, { status: value });
