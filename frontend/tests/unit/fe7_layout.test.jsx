// FE7: the banners of the layout (F01) and the error screen for a crashing page (F02). Two pages are replaced by
// ones that throw, to show the error screen.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearToken } from "../../src/api/token.js";
import { GOOD_TOKEN, UNVERIFIED, VERIFIED, currentPath, fakeBackend, makeProject, makeRun, navigateTo, renderApp,
  saveToken } from "./app_helpers.jsx";
import { event, installRunStream, makeRunDetail } from "./run_helpers.js";

vi.mock("../../src/pages/AccountPage.jsx", () => ({ default: () => { throw new Error("the account page broke"); } }));
vi.mock("../../src/pages/ForgotPasswordPage.jsx", () => ({ default: () => { throw new Error("this page broke"); } }));

const { server, seen, state } = fakeBackend();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
  state.user = VERIFIED;
  state.projects = [makeProject(1)];
  state.activeRun = null;
  state.runs = null;
  saveToken(GOOD_TOKEN);
});
afterEach(() => {
  server.resetHandlers();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const activeRunBanner = () => screen.queryByRole("region", { name: "Run in progress" });
const verifyBanner = () => screen.queryByRole("region", { name: "Email not verified" });

describe("FE7-F01: the banners", () => {
  it.each([
    [VERIFIED, null, false, false],
    [UNVERIFIED, null, true, false],
    [VERIFIED, makeRun(42, { status: "searching" }), false, true],
    [UNVERIFIED, makeRun(42, { status: "waiting_for_hint" }), true, true],
  ])("user %#: verify banner %s / run banner %s", async (user, active, verify, running) => {
    state.user = user;
    state.activeRun = active;
    renderApp("/projects");
    await screen.findByRole("heading", { name: "Projects" });
    await waitFor(() => expect(Boolean(activeRunBanner())).toBe(running));
    expect(Boolean(verifyBanner())).toBe(verify);
    if (running) {
      expect(activeRunBanner()).toHaveTextContent("A run is in progress");
      expect(screen.getAllByRole("link", { name: /open it/ })[0]).toHaveAttribute("href", "/runs/42");
    }
  });

  it("the run banner is on every logged-in page and follows the data, which refreshes every 30 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderApp("/projects");
    await screen.findByRole("heading", { name: "Projects" });
    expect(activeRunBanner()).toBeNull();
    state.activeRun = makeRun(42, { status: "testing" });
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    await waitFor(() => expect(activeRunBanner()).not.toBeNull());
    act(() => navigateTo("/runs"));
    await screen.findByRole("heading", { name: "History" });
    expect(activeRunBanner()).not.toBeNull();
    state.activeRun = null; // the run ended
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    await waitFor(() => expect(activeRunBanner()).toBeNull());
  });

  it("the run ends and the page learns it from the run record before the final event: the banner goes at once", async () => {
    const stream = installRunStream();
    try {
      state.runDetails = { 5: makeRunDetail(5, { status: "testing" }) };
      state.activeRun = makeRun(5, { status: "testing" });
      renderApp("/runs/5");
      await screen.findByRole("heading", { name: "Running the tests in Docker" });
      await waitFor(() => expect(activeRunBanner()).not.toBeNull());
      await waitFor(() => expect(stream.connections.length).toBeGreaterThan(0));
      state.runDetails[5] = makeRunDetail(5, { status: "succeeded" });
      state.activeRun = null;
      act(() => stream.push(event(9, "tests_finished", "Tests passed"))); // reloads the run; no final event yet
      expect(await screen.findByRole("heading", { name: "Tests passed" })).toBeInTheDocument();
      await waitFor(() => expect(activeRunBanner()).toBeNull(), { timeout: 2000 }); // not after 30 seconds
    } finally {
      stream.restore();
    }
  });

  it("'open it' opens the run", async () => {
    state.activeRun = makeRun(5, { status: "searching" });
    const user = userEvent.setup();
    renderApp("/projects");
    await waitFor(() => expect(activeRunBanner()).not.toBeNull());
    await user.click(screen.getAllByRole("link", { name: /open it/ })[0]);
    expect(currentPath()).toBe("/runs/5");
  });
});

describe("FE7-F02: a page that crashes", () => {
  it("a logged-in page: the error screen with reload, the top bar stays, another page works", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const user = userEvent.setup();
    renderApp("/account");
    expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "reload" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "projects" }));
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("a page outside the layout: the error screen, never a white page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    clearToken();
    window.localStorage.clear();
    renderApp("/forgot-password");
    expect(await screen.findByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, reload });
    screen.getByRole("button", { name: "reload" }).click();
    expect(reload).toHaveBeenCalled();
  });
});
