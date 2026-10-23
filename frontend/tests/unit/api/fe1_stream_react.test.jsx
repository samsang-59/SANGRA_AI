// FE1-X26: React development mode (StrictMode) starts, stops and starts a screen's effects quickly; in the end there
// must be exactly ONE open connection.
import { StrictMode, useEffect } from "react";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { openRunStream } from "../../../src/api/stream.js";
import { clearToken, setToken } from "../../../src/api/token.js";
import { installStorage } from "./helpers.js";
import { installFakeStreamBackend, settle } from "./fake_stream.js";

const realFetch = globalThis.fetch;
let backend;

function RunViewer({ runId }) {
  useEffect(() => openRunStream(runId, () => {}), [runId]);
  return null;
}

beforeEach(() => {
  installStorage();
  clearToken();
  setToken("header.payload.signature");
  backend = installFakeStreamBackend();
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

const openConnections = () => backend.connections.filter((c) => !c.aborted && !c.cancelled);

it("StrictMode mounts twice; one connection stays open", async () => {
  const view = render(<StrictMode><RunViewer runId={5} /></StrictMode>);
  await settle();
  expect(backend.connections.length).toBe(2); // the double start of development mode
  expect(openConnections()).toHaveLength(1);
  expect(openConnections()[0].url).toMatch(/\/runs\/5\/events$/);

  view.rerender(<StrictMode><RunViewer runId={6} /></StrictMode>); // FE5-X06 relies on this: run 5 -> run 6
  await settle();
  expect(openConnections()).toHaveLength(1);
  expect(openConnections()[0].url).toMatch(/\/runs\/6\/events$/);

  view.unmount();
  await settle();
  expect(openConnections()).toHaveLength(0);
});
