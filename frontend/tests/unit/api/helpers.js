// Shared helpers for the API layer tests (FE1).
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { API_URL } from "../../../src/config.js";

export { http, HttpResponse };
export const api = (path) => API_URL + path;

/** A localStorage stand-in (Node has none). blocked=true makes every call throw, as in a private window. */
export function installStorage({ blocked = false } = {}) {
  const items = new Map();
  const fail = () => {
    throw new DOMException("blocked", "SecurityError");
  };
  const storage = blocked
    ? { getItem: fail, setItem: fail, removeItem: fail }
    : {
        getItem: (key) => (items.has(key) ? items.get(key) : null),
        setItem: (key, value) => items.set(key, String(value)),
        removeItem: (key) => items.delete(key),
      };
  // jsdom has a read-only localStorage getter, so the property is redefined rather than assigned.
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
  return items;
}

/** An MSW server that records every request it sees. */
export function recordingServer() {
  const seen = [];
  const server = setupServer();
  server.events.on("request:start", ({ request }) => {
    seen.push(request.clone());
  });
  return { server, seen };
}
