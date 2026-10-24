// The live stream helper (05_frontend_design.md F2.3): GET /runs/{id}/events read with fetch (so the JWT header
// can be sent), split into events, handed over one by one. Reconnects with Last-Event-ID after a drop.
import { ApiError, apiUrl, authHeaders, errorFromResponse, handleUnauthorized } from "./client.js";

export const FINAL_STATUSES = ["succeeded", "failed", "stopped"];
export const MAX_RECONNECTS = 5;
export const FIRST_RECONNECT_DELAY_MS = 2000;

/** Splits SSE text into events. Feed it chunks (strings); it calls onMessage({ id, event, data }) per event. Works
 * when an event is cut anywhere (also between "\r" and "\n"). The pieces of an unfinished line are kept in a list and
 * joined once when the line ends, so even a 1 MB event in 1-byte chunks costs linear time (FE1-L02). */
export function createSseParser(onMessage) {
  let pieces = [];
  let fields = { data: [] };

  function line(text) {
    if (text === "") {
      if (fields.data.length) onMessage({ id: fields.id, event: fields.event, data: fields.data.join("\n") });
      fields = { data: [] };
      return;
    }
    if (text.startsWith(":")) return; // a ping or comment
    const colon = text.indexOf(":");
    const name = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? "" : text.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (name === "data") fields.data.push(value);
    else if (name === "id") fields.id = value;
    else if (name === "event") fields.event = value;
    // other fields (retry, unknown ones) are ignored
  }

  return function feed(chunk) {
    let start = 0;
    let newline = chunk.indexOf("\n");
    while (newline !== -1) {
      pieces.push(chunk.slice(start, newline));
      let text = pieces.length === 1 ? pieces[0] : pieces.join("");
      pieces = [];
      if (text.endsWith("\r")) text = text.slice(0, -1);
      line(text);
      start = newline + 1;
      newline = chunk.indexOf("\n", start);
    }
    if (start < chunk.length) pieces.push(start === 0 ? chunk : chunk.slice(start));
  };
}

/**
 * Opens the live stream of a run. onEvent(event) gets { sequence_number, event_type, message, details, attempt_id,
 * created_at } in order, never twice. Callbacks: onOpen() each time a connection is open (also after a reconnect);
 * onEnd() after the final status_changed event; onReconnecting(try, delayMs); onError(ApiError) when it gives up
 * (5 failed reconnects, or an answer like 404); onBadEvent(text) for an event that is not valid JSON (skipped).
 * Returns stop(); after stop() no callback is called again.
 */
export function openRunStream(runId, onEvent, callbacks = {}) {
  const { onOpen, onEnd, onError, onReconnecting, onBadEvent } = callbacks;
  let lastSequence = 0;
  let failedTries = 0;
  let stopped = false;
  let controller = null;
  let retryTimer = null;

  function stop() {
    if (stopped) return;
    stopped = true;
    clearTimeout(retryTimer);
    retryTimer = null;
    if (controller) controller.abort();
  }

  function giveUp(error) {
    if (stopped) return;
    stop();
    if (onError) onError(error);
  }

  function retryLater() {
    if (stopped) return;
    failedTries += 1;
    if (failedTries > MAX_RECONNECTS) {
      giveUp(new ApiError(0, "NETWORK_ERROR", "Lost connection to the server."));
      return;
    }
    const delay = FIRST_RECONNECT_DELAY_MS * 2 ** (failedTries - 1);
    if (onReconnecting) onReconnecting(failedTries, delay);
    retryTimer = setTimeout(connect, delay);
  }

  function handleMessage(message) {
    if (stopped) return;
    let event;
    try {
      event = JSON.parse(message.data);
    } catch {
      if (onBadEvent) onBadEvent(message.data);
      else console.warn(`Run ${runId}: an event could not be read and was skipped.`);
      return;
    }
    const sequence = Number(event?.sequence_number ?? message.id);
    if (!Number.isFinite(sequence) || sequence <= lastSequence) return; // already handled (after a reconnect)
    lastSequence = sequence;
    failedTries = 0; // the connection works again
    onEvent(event);
    if (stopped) return; // onEvent may have stopped the stream
    if (event.event_type === "status_changed" && FINAL_STATUSES.includes(event.details?.status)) {
      stop();
      if (onEnd) onEnd();
    }
  }

  async function connect() {
    if (stopped) return;
    controller = new AbortController();
    const [headers, sentToken] = authHeaders(false);
    if (lastSequence > 0) headers["Last-Event-ID"] = String(lastSequence);
    let response;
    try {
      response = await fetch(apiUrl(`/runs/${encodeURIComponent(String(runId))}/events`), {
        headers,
        signal: controller.signal,
      });
    } catch {
      retryLater();
      return;
    }
    if (stopped) return;
    if (!response.ok) {
      if (response.status === 401) handleUnauthorized(sentToken);
      if (response.status >= 500) {
        retryLater();
        return;
      }
      giveUp(await errorFromResponse(response));
      return;
    }
    if (onOpen) onOpen();
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const feed = createSseParser(handleMessage);
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (stopped) return;
        if (done) break;
        feed(decoder.decode(value, { stream: true }));
        if (stopped) return;
      }
      feed(decoder.decode());
    } catch {
      if (stopped) return;
    } finally {
      if (stopped) reader.cancel().catch(() => {});
    }
    retryLater(); // the server closed (or the line dropped) before the final event
  }

  connect();
  return stop;
}
