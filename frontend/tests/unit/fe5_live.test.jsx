// FE5: the Run page, live view (F01-F10, X01-X12, S01-S02, U01-U02, L01-L04). F11 runs against the real backend
// (tests/real). The run record and the actions come from the MSW fake backend; the live stream is scripted.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearToken } from "../../src/api/token.js";
import ApprovalBox from "../../src/components/ApprovalBox.jsx";
import { FLUSH_MS } from "../../src/hooks/useRunStream.js";
import {
  GOOD_TOKEN, VERIFIED, HttpResponse, currentPath, errorReply, fakeBackend, makeProject, navigateTo, renderApp,
  saveToken,
} from "./app_helpers.jsx";
import {
  START_EVENTS, event, installRunStream, makeAttempt, makeEdit, makeRunDetail, statusEvent,
} from "./run_helpers.js";

const { server, seen, state } = fakeBackend();
let stream;
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
  state.user = VERIFIED;
  state.projects = [makeProject(1, { name: "summer-sale-service" }),
    makeProject(2, { name: "payments-api", language: "python", source_type: "local_path", test_command: "pytest" })];
  state.activeRun = null;
  state.runs = null;
  state.runDetails = { 5: makeRunDetail(5) };
  state.runReplies = {};
  saveToken(GOOD_TOKEN);
  stream = installRunStream();
});
afterEach(() => {
  stream.restore();
  server.resetHandlers();
  vi.restoreAllMocks();
});

const setRun = (id, changes) => {
  state.runDetails[id] = { ...state.runDetails[id], ...changes };
};
const runLoads = (id = 5) => seen.filter((r) => r.method === "GET" && r.url.endsWith(`/runs/${id}`)).length;
const posts = (action) => seen.filter((r) => r.method === "POST" && r.url.endsWith(`/${action}`));
const logLines = () => within(screen.getByRole("list")).queryAllByRole("listitem");
const logTexts = () => logLines().map((li) => li.querySelector(".log-text").textContent);
const openConnections = () => stream.connections.filter((c) => !c.aborted && !c.cancelled);
const push = (...texts) => act(() => stream.push(texts.join("")));

async function openRun(id = 5, { title } = {}) {
  renderApp(`/runs/${id}`);
  if (title) await screen.findByRole("heading", { name: title });
  await waitFor(() => expect(stream.connections.length).toBeGreaterThan(0));
}

/** Delays of the reconnect timer (2 s and up) become 1 ms, so 5 failed reconnects take no real time. */
function fastReconnects() {
  const realSetTimeout = globalThis.setTimeout;
  vi.spyOn(globalThis, "setTimeout").mockImplementation((fn, ms, ...rest) =>
    realSetTimeout(fn, ms >= 2000 ? 1 : ms, ...rest));
}

const waitingForApproval = (attempt = makeAttempt(1), changes = {}) =>
  setRun(5, { status: "waiting_for_approval", attempts: [attempt], ...changes });

describe("the live view", () => {
  it("FE5-F01: an active run: the stream is opened once; the replayed events fill the timeline in order", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await waitFor(() => expect(stream.connections).toHaveLength(1));
    expect(stream.last().url).toMatch(/\/runs\/5\/events$/);
    expect(stream.last().headers["Last-Event-ID"]).toBeUndefined(); // the first connection gets every event
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    expect(logTexts()).toEqual(["status → started", "Copied the code from GitHub (commit 3f9a1c2)",
      "Baseline tests started: mvn test", "Baseline finished: failed (1 test failing)", "status → searching",
      'search_files("discount")']);
    expect(logLines().map((li) => li.querySelector(".log-number").textContent)).toEqual(
      ["001", "002", "003", "004", "005", "006"]);
    expect(screen.getByText("summer-sale-service")).toBeInTheDocument();
    expect(screen.getByText("The summer sale shows 5% discount, it should be 10%.")).toBeInTheDocument();
    expect(screen.getByText("agent is working")).toBeInTheDocument();
    expect(screen.getByText(/Leaving this page does not stop the run/)).toBeInTheDocument();
    expect(stream.connections).toHaveLength(1);
  });

  it("FE5-F02: a new event is added at the end; status_changed changes the status badge", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    await push(event(7, "tool_result", "4 files matched"));
    await waitFor(() => expect(logTexts().at(-1)).toBe("4 files matched"));
    setRun(5, { status: "testing" });
    await push(statusEvent(8, "testing"));
    expect(await screen.findByRole("heading", { name: "Running the tests in Docker" })).toBeInTheDocument();
    expect(document.querySelector(".page-header .badge")).toHaveTextContent("testing");
    await waitFor(() => expect(logTexts().at(-1)).toBe("status → testing"));
  });

  it("FE5-F03: approval_requested: the run is reloaded and the box shows the explanation, every edit and 'rejected 1 of 3'", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    const before = runLoads();
    waitingForApproval(makeAttempt(2, {
      explanation: "The summer sale rate is set to 0.05 in DiscountService and 5 in application.properties.",
      edits: [makeEdit(2, { file_path: "src/main/resources/application.properties", start_line: 7, end_line: 7,
        old_code: "sale.summer.discount=5", new_code: "sale.summer.discount=10", explanation: "config value must match the code." }),
      makeEdit(1)] }), { reject_count: 1 });
    await push(statusEvent(7, "waiting_for_approval"), event(8, "approval_requested", "Approval requested for fix #2"));
    expect(await screen.findByRole("heading", { name: "Review the proposed fix" })).toBeInTheDocument();
    expect(runLoads()).toBeGreaterThan(before);
    expect(screen.getByText(/in DiscountService and 5 in application.properties/)).toBeInTheDocument();
    const diffs = document.querySelectorAll("figure.diff");
    expect(diffs).toHaveLength(2);
    expect(diffs[0]).toHaveTextContent("src/main/java/com/shop/sale/DiscountService.java"); // in edit order
    expect(diffs[0]).toHaveTextContent("lines 18–18 · edit 1");
    expect(diffs[0].querySelector(".diff-old")).toHaveTextContent("SUMMER_SALE_DISCOUNT = 0.05;");
    expect(diffs[0].querySelector(".diff-new")).toHaveTextContent("SUMMER_SALE_DISCOUNT = 0.10;");
    expect(diffs[1]).toHaveTextContent("src/main/resources/application.properties");
    expect(diffs[1]).toHaveTextContent("lines 7–7 · edit 2");
    expect(diffs[1]).toHaveTextContent("Reason: config value must match the code.");
    const info = document.querySelector(".info-bar");
    expect(info).toHaveTextContent("attempt #2 · after_reject");
    expect(info).toHaveTextContent("rejected 1 of 3");
    expect(info).toHaveTextContent("edits 2 in 2 files");
    expect(info).toHaveTextContent("This run stops automatically after 1 hour of waiting");
    expect(screen.getByText("Apply these 2 edits to the copy and run the tests?")).toBeInTheDocument();
    expect(screen.getByText("waiting for you")).toBeInTheDocument();
  });

  it("FE5-F04: Accept sends the attempt_id of the shown proposal; the buttons stay disabled until the next status", async () => {
    waitingForApproval(makeAttempt(2, { id: 302 }));
    const user = userEvent.setup();
    await openRun(5, { title: "Review the proposed fix" });
    await user.click(screen.getByRole("button", { name: "accept & test" }));
    await waitFor(() => expect(posts("approval")).toHaveLength(1));
    expect(await posts("approval")[0].json()).toEqual({ attempt_id: 302, decision: "accepted" });
    await screen.findByText(/Sent. Waiting for the agent/);
    expect(screen.getByRole("button", { name: "accept & test" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "reject" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "accept & test" }));
    expect(posts("approval")).toHaveLength(1);
    setRun(5, { status: "applying", attempts: [makeAttempt(2, { id: 302, status: "applied", decision: "accepted" })] });
    await push(statusEvent(9, "applying"));
    expect(await screen.findByRole("heading", { name: "Applying the approved edits" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "accept & test" })).not.toBeInTheDocument();
  });

  it("FE5-F05: Reject needs feedback of 3 to 2000 characters; it is sent; the reject counter updates", async () => {
    waitingForApproval(makeAttempt(1, { id: 301 }));
    const user = userEvent.setup();
    await openRun(5, { title: "Review the proposed fix" });
    const box = screen.getByLabelText(/Feedback/);
    await user.click(screen.getByRole("button", { name: "reject" }));
    expect(screen.getByRole("alert")).toHaveTextContent("To reject, tell the agent what is wrong (3-2000 characters).");
    await user.type(box, "ab");
    await user.click(screen.getByRole("button", { name: "reject" }));
    fireEvent.change(box, { target: { value: "x".repeat(2001) } });
    await user.click(screen.getByRole("button", { name: "reject" }));
    expect(posts("approval")).toHaveLength(0);
    fireEvent.change(box, { target: { value: "  also change the config file  " } });
    await user.click(screen.getByRole("button", { name: "reject" }));
    await waitFor(() => expect(posts("approval")).toHaveLength(1));
    expect(await posts("approval")[0].json()).toEqual({ attempt_id: 301, decision: "rejected",
      feedback: "also change the config file" });
    // The next proposal: a new attempt, "rejected 1 of 3", and an empty box again.
    setRun(5, { status: "searching", reject_count: 1 });
    await push(statusEvent(9, "searching"));
    await screen.findByRole("heading", { name: "Searching the code for the bug" });
    waitingForApproval(makeAttempt(2, { id: 302 }), { reject_count: 1 });
    await push(statusEvent(10, "waiting_for_approval"), event(11, "approval_requested", "Approval requested for fix #2"));
    await screen.findByRole("heading", { name: "Review the proposed fix" });
    await waitFor(() => expect(document.querySelector(".info-bar")).toHaveTextContent("rejected 1 of 3"));
    expect(document.querySelector(".info-bar")).toHaveTextContent("attempt #2");
    expect(screen.getByLabelText(/Feedback/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "reject" })).toBeEnabled();
  });

  it("FE5-F06: hint_requested: the hint box (3 to 2000 characters, 'hint 2 of 3') and the 1-hour note", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    setRun(5, { status: "waiting_for_hint", hint_count: 2 });
    await push(statusEvent(7, "waiting_for_hint"),
      event(8, "hint_requested", "I could not find the bug. Please give a hint (2 of 3).", { hint_number: 2, max_hints: 3 }));
    expect(await screen.findByRole("heading", { name: "The agent needs a hint" })).toBeInTheDocument();
    const info = document.querySelector(".info-bar");
    expect(info).toHaveTextContent("hint 2 of 3");
    expect(info).toHaveTextContent("This run stops automatically after 1 hour of waiting");
    const user = userEvent.setup();
    const box = screen.getByLabelText("Your hint");
    const send = screen.getByRole("button", { name: "send hint" });
    expect(screen.getByText("0 / 2000 · min 3")).toBeInTheDocument();
    await user.type(box, "ab");
    expect(send).toBeDisabled();
    fireEvent.change(box, { target: { value: "x".repeat(2001) } });
    expect(screen.getByText("2001 / 2000 · min 3")).toBeInTheDocument();
    expect(send).toBeDisabled();
    fireEvent.change(box, { target: { value: "look at refund_calculator.py" } });
    expect(send).toBeEnabled();
    await user.click(send);
    await waitFor(() => expect(posts("hint")).toHaveLength(1));
    expect(await posts("hint")[0].json()).toEqual({ text: "look at refund_calculator.py" });
    expect(await screen.findByText(/Sent. The agent is searching again/)).toBeInTheDocument();
    expect(send).toBeDisabled();
  });

  it("FE5-F07: Cancel: a confirm box, then the cancel call; the run ends and the view changes", async () => {
    const user = userEvent.setup();
    await openRun(5, { title: "Searching the code for the bug" });
    await user.click(screen.getByRole("button", { name: "cancel run" }));
    const dialog = screen.getByRole("alertdialog", { name: "Cancel this run?" });
    await user.click(within(dialog).getByRole("button", { name: "keep running" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(posts("cancel")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "cancel run" }));
    setRun(5, { status: "stopped", stop_reason: "cancelled_by_user", final_summary: "The run was cancelled." });
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "cancel run" }));
    await waitFor(() => expect(posts("cancel")).toHaveLength(1));
    expect(await screen.findByRole("heading", { name: "You cancelled the run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "cancel run" })).not.toBeInTheDocument();
    expect(openConnections()).toHaveLength(0); // the result view has no stream
  });

  it("FE5-F08: the final status event: the stream ends, the run is reloaded, the result view is shown", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    const before = runLoads();
    setRun(5, { status: "succeeded", final_summary: "Fixed in 1 attempt. 1 edit in DiscountService.java." });
    await push(statusEvent(7, "succeeded"));
    expect(await screen.findByRole("heading", { name: "Tests passed" })).toBeInTheDocument();
    expect(screen.getByText("Fixed in 1 attempt. 1 edit in DiscountService.java.")).toBeInTheDocument();
    expect(runLoads()).toBeGreaterThan(before);
    expect(openConnections()).toHaveLength(0);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(stream.connections).toHaveLength(1); // no reconnect after the end
  });

  it("FE5-F09: refresh in the middle of a run: the timeline comes back from the replay, the same box for the same status", async () => {
    setRun(5, { status: "waiting_for_hint", hint_count: 1 });
    const replay = [...START_EVENTS, statusEvent(7, "waiting_for_hint"),
      event(8, "hint_requested", "I could not find the bug. Please give a hint (1 of 3).")];
    const first = renderApp("/runs/5");
    await screen.findByRole("heading", { name: "The agent needs a hint" });
    await push(...replay);
    await waitFor(() => expect(logLines()).toHaveLength(8));
    first.unmount(); // the browser's refresh
    expect(openConnections()).toHaveLength(0);
    renderApp("/runs/5");
    await screen.findByRole("heading", { name: "The agent needs a hint" });
    await waitFor(() => expect(stream.connections).toHaveLength(2));
    expect(stream.last().headers["Last-Event-ID"]).toBeUndefined();
    await push(...replay);
    await waitFor(() => expect(logLines()).toHaveLength(8));
    expect(screen.getByLabelText("Your hint")).toBeInTheDocument();
    expect(document.querySelector(".info-bar")).toHaveTextContent("hint 1 of 3");
  });

  it("FE5-F10: a run waiting for approval that was started earlier shows the box at once", async () => {
    waitingForApproval();
    renderApp("/runs/5");
    expect(await screen.findByRole("button", { name: "accept & test" })).toBeInTheDocument();
    expect(screen.getByText("The summer sale rate is set to 0.05. Changing it to 0.10.")).toBeInTheDocument();
    expect(screen.getByText("Apply this edit to the copy and run the tests?")).toBeInTheDocument();
  });
});

describe("problems", () => {
  it("FE5-X01: the connection drops: 'Reconnecting…' appears and goes away; no duplicate events", async () => {
    fastReconnects();
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    expect(screen.getByText("live")).toBeInTheDocument();
    act(() => stream.drop());
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    await waitFor(() => expect(stream.connections).toHaveLength(2));
    expect(stream.last().headers["Last-Event-ID"]).toBe("6");
    await waitFor(() => expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument());
    await push(START_EVENTS[5], event(7, "tool_result", "4 files matched")); // 6 again, then a new one
    await waitFor(() => expect(logLines()).toHaveLength(7));
    expect(logLines().map((li) => li.querySelector(".log-number").textContent)).toEqual(
      ["001", "002", "003", "004", "005", "006", "007"]);
  });

  it("FE5-X02: 5 failed reconnects: 'Lost connection' with a Retry button that opens the stream again", async () => {
    fastReconnects();
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    stream.replies.push("network", "network", "network", "network", "network");
    act(() => stream.drop());
    expect(await screen.findByText("Lost connection")).toBeInTheDocument();
    expect(stream.connections).toHaveLength(6); // the first one and 5 reconnects
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "retry" }));
    await waitFor(() => expect(stream.connections).toHaveLength(7));
    await push(...START_EVENTS, event(7, "tool_result", "4 files matched")); // the full replay of a new stream
    await waitFor(() => expect(logLines()).toHaveLength(7));
    expect(screen.queryByText("Lost connection")).not.toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
  });

  it.each([
    ["STALE_ATTEMPT", 409, "This proposal was already answered."],
    ["RUN_STATE_INVALID", 409, "The run is not waiting for an answer."],
  ])("FE5-X03: Accept gets %s: the message is shown and the run is reloaded to show the real state", async (code, status, message) => {
    waitingForApproval(makeAttempt(1, { id: 301 }));
    state.runReplies.approval = () => {
      setRun(5, { status: "stopped", stop_reason: "cancelled_by_user" }); // cancelled from another tab
      return errorReply(status, code, message);
    };
    const user = userEvent.setup();
    await openRun(5, { title: "Review the proposed fix" });
    const before = runLoads();
    await user.click(screen.getByRole("button", { name: "accept & test" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "You cancelled the run" })).toBeInTheDocument();
    expect(runLoads()).toBeGreaterThan(before);
  });

  it("FE5-X04: the run is cancelled from another tab while the approval box is open: the box disappears", async () => {
    waitingForApproval();
    await openRun(5, { title: "Review the proposed fix" });
    setRun(5, { status: "stopped", stop_reason: "cancelled_by_user" });
    await push(statusEvent(9, "stopped", { stop_reason: "cancelled_by_user" }));
    expect(await screen.findByRole("heading", { name: "You cancelled the run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "accept & test" })).not.toBeInTheDocument();
  });

  it("FE5-X05: leaving the page stops the stream; no callbacks afterwards; the run is not cancelled", async () => {
    const errors = vi.spyOn(console, "error");
    const user = userEvent.setup();
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await user.click(within(document.querySelector(".breadcrumb")).getByRole("link", { name: "history" }));
    await waitFor(() => expect(currentPath()).toBe("/runs"));
    expect(openConnections()).toHaveLength(0);
    expect(stream.last().aborted).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, FLUSH_MS * 2));
    expect(posts("cancel")).toHaveLength(0);
    expect(errors).not.toHaveBeenCalled();
  });

  it("FE5-X06: from run 5 to run 6: the old stream is stopped and a new one opens", async () => {
    state.runDetails[6] = makeRunDetail(6, { status: "testing", bug_description: "Another bug in the code." });
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    act(() => navigateTo("/runs/6"));
    expect(await screen.findByRole("heading", { name: "Running the tests in Docker" })).toBeInTheDocument();
    await waitFor(() => expect(openConnections()).toHaveLength(1));
    expect(openConnections()[0].url).toMatch(/\/runs\/6\/events$/);
    expect(stream.connections[0].aborted).toBe(true);
    expect(screen.getByText("Waiting for the first event…")).toBeInTheDocument(); // run 5's events are gone
    await push(statusEvent(1, "started"));
    await waitFor(() => expect(logTexts()).toEqual(["status → started"]));
  });

  it("FE5-X07: an unknown event type is shown in a plain way", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(event(1, "something_new", "A new kind of event"));
    await waitFor(() => expect(logTexts()).toEqual(["A new kind of event"]));
    expect(logLines()[0].querySelector(".log-sign")).toHaveTextContent("·");
  });

  it("FE5-X08: a message of 10,000 characters is cut with 'show more'", async () => {
    const user = userEvent.setup();
    await openRun(5, { title: "Searching the code for the bug" });
    await push(event(1, "tool_result", "y".repeat(10_000)));
    await waitFor(() => expect(logLines()).toHaveLength(1));
    expect(logLines()[0].querySelector(".log-text").textContent).toHaveLength(401 + "show more".length);
    await user.click(screen.getByRole("button", { name: "show more" }));
    expect(logLines()[0].textContent).toContain("y".repeat(10_000));
    await user.click(screen.getByRole("button", { name: "show less" }));
    expect(logLines()[0].textContent).not.toContain("y".repeat(10_000));
  });

  it("FE5-X09: an error event is shown in red", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(event(1, "error", "The test runner could not start."));
    await waitFor(() => expect(logLines()).toHaveLength(1));
    expect(logLines()[0]).toHaveClass("log-error");
    expect(logLines()[0].querySelector(".log-sign")).toHaveClass("text-danger");
  });

  it.each([["does not exist", "999"], ["is not yours", "8"]])("FE5-X10: a run that %s: the not found page", async (_n, id) => {
    renderApp(`/runs/${id}`);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(stream.connections).toHaveLength(0);
  });

  it("FE5-X11: the tab comes back after a long sleep with a lost connection: the page reconnects and catches up", async () => {
    fastReconnects();
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS.slice(0, 3));
    await waitFor(() => expect(logLines()).toHaveLength(3));
    stream.replies.push("network", "network", "network", "network", "network"); // the computer sleeps
    act(() => stream.drop());
    await screen.findByText("Lost connection");
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(stream.connections).toHaveLength(7));
    await push(...START_EVENTS, event(7, "tool_result", "4 files matched"));
    await waitFor(() => expect(logLines()).toHaveLength(7));
    expect(screen.getByText("live")).toBeInTheDocument();
    visibility.mockRestore();
  });

  it.each(["hint", "approval"])("FE5-X12: the %s request fails with a network error: message, then a working retry", async (action) => {
    if (action === "hint") setRun(5, { status: "waiting_for_hint", hint_count: 1 });
    else waitingForApproval();
    let fail = true;
    state.runReplies[action] = () => (fail ? HttpResponse.error() : HttpResponse.json({ data: { message: "OK" } }));
    const user = userEvent.setup();
    await openRun(5, { title: action === "hint" ? "The agent needs a hint" : "Review the proposed fix" });
    const button = () => screen.getByRole("button", { name: action === "hint" ? "send hint" : "accept & test" });
    if (action === "hint") await user.type(screen.getByLabelText("Your hint"), "look at Discount.java");
    await user.click(button());
    expect(await screen.findByText("Cannot reach the server")).toBeInTheDocument();
    expect(button()).toBeEnabled();
    fail = false;
    await user.click(button());
    await waitFor(() => expect(posts(action)).toHaveLength(2));
    expect(await screen.findByText(/Sent\./)).toBeInTheDocument();
    expect(screen.queryByText("Cannot reach the server")).not.toBeInTheDocument();
  });
});

describe("security", () => {
  it("FE5-S01: events with HTML, script text or code are shown as plain text", async () => {
    const nasty = '<img src=x onerror="window.hacked=1"><script>window.hacked=2</script> if (a < b && c > d) {}';
    await openRun(5, { title: "Searching the code for the bug" });
    await push(event(1, "tool_result", nasty), event(2, "error", "<b>bold</b>"));
    await waitFor(() => expect(logLines()).toHaveLength(2));
    expect(logTexts()).toEqual([nasty, "<b>bold</b>"]);
    const log = screen.getByRole("list");
    expect(log.querySelector("img, script, b")).toBeNull();
    expect(window.hacked).toBeUndefined();
  });

  it("FE5-S02: code in the diff is shown as text, never rendered as HTML", async () => {
    waitingForApproval(makeAttempt(1, { explanation: "<i>why</i>", edits: [makeEdit(1, {
      old_code: '<script>window.hacked=3</script>', new_code: '<img src=x onerror="window.hacked=4">\n{{ 7*7 }}',
      explanation: "<u>reason</u>" })] }));
    await openRun(5, { title: "Review the proposed fix" });
    const diff = document.querySelector("figure.diff");
    expect(diff.querySelector(".diff-old .diff-code").textContent).toBe("<script>window.hacked=3</script>");
    expect([...diff.querySelectorAll(".diff-new .diff-code")].map((n) => n.textContent)).toEqual(
      ['<img src=x onerror="window.hacked=4">', "{{ 7*7 }}"]);
    expect(document.querySelector("main").querySelector("script, img, i, u")).toBeNull();
    expect(screen.getByText("<i>why</i>")).toBeInTheDocument();
    expect(window.hacked).toBeUndefined();
  });
});

describe("usability", () => {
  it("FE5-U01: the approval box works with the keyboard; the buttons are clear; a status change is announced", async () => {
    waitingForApproval(makeAttempt(1, { id: 301 }));
    const user = userEvent.setup();
    await openRun(5, { title: "Review the proposed fix" });
    const live = document.querySelector("[aria-live='polite']");
    expect(live).toHaveTextContent("Status: waiting_for_approval. Review the proposed fix");
    const feedback = screen.getByLabelText(/Feedback/);
    feedback.focus();
    await user.keyboard("wrong file");
    await user.tab();
    expect(screen.getByRole("button", { name: "accept & test" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "reject" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(posts("approval")).toHaveLength(1));
    expect(await posts("approval")[0].json()).toEqual({ attempt_id: 301, decision: "rejected", feedback: "wrong file" });
    setRun(5, { status: "searching", reject_count: 1 });
    await push(statusEvent(9, "searching"));
    await waitFor(() => expect(live).toHaveTextContent("Status: searching. Searching the code for the bug"));
  });

  it("FE5-U02: scrolled up in a long timeline, a new event does not move the view; 'jump to latest' appears", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    const box = document.querySelector(".log-scroll");
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => 2000 });
    Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 300 });
    box.scrollTop = 100; // the reader scrolled up
    fireEvent.scroll(box);
    await push(event(7, "tool_result", "4 files matched"));
    const jump = await screen.findByRole("button", { name: /jump to latest/ });
    expect(box.scrollTop).toBe(100);
    fireEvent.click(jump);
    expect(box.scrollTop).toBe(2000);
    expect(screen.queryByRole("button", { name: /jump to latest/ })).not.toBeInTheDocument();
    await push(event(8, "tool_result", "3 lines found")); // at the bottom again: it follows
    await waitFor(() => expect(logLines()).toHaveLength(8));
    expect(screen.queryByRole("button", { name: /jump to latest/ })).not.toBeInTheDocument();
  });
});

describe("load", () => {
  it("FE5-L01: 2,000 events: the first render in under 1 second", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    const texts = Array.from({ length: 2000 }, (_, i) => event(i + 1, i % 2 ? "tool_result" : "tool_called",
      `read_file_lines(DiscountService.java, ${i}-${i + 20})`));
    const started = performance.now();
    await push(texts.join(""));
    await waitFor(() => expect(document.querySelectorAll(".log-line")).toHaveLength(2000), { timeout: 5000 });
    const took = performance.now() - started;
    console.log(`FE5-L01: 2,000 events rendered in ${took.toFixed(0)} ms (jsdom)`);
    expect(took).toBeLessThan(1000);
  });

  it("FE5-L02: 50 events per second for 60 seconds: the updates are grouped; only the list grows", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    const list = () => document.querySelector(".log-scroll");
    let commits = 0;
    const observer = new MutationObserver(() => {
      commits += 1;
    });
    observer.observe(list(), { childList: true, subtree: true });
    let sequence = 0;
    const started = performance.now();
    for (let second = 0; second < 60; second += 1) { // 60 "seconds", 50 events each, as single events
      for (let i = 0; i < 50; i += 1) {
        sequence += 1;
        stream.push(event(sequence, "tool_called", `call ${sequence}`));
      }
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    }
    await waitFor(() => expect(document.querySelectorAll(".log-line")).toHaveLength(3000), { timeout: 10000 });
    observer.disconnect();
    const took = performance.now() - started;
    console.log(`FE5-L02: 3,000 events (60 x 50) drawn in ${commits} DOM updates, ${took.toFixed(0)} ms`);
    expect(commits).toBeLessThan(300); // grouped (every 100 ms), not one update per event
    expect(screen.getByRole("button", { name: "cancel run" })).toBeEnabled(); // the page still works
  });

  it("FE5-L03: a diff with 10 edits and 300 lines renders in under 500 ms", () => {
    const code = (tag) => Array.from({ length: 15 }, (_, i) => `    int ${tag}${i} = compute(${i}); // line ${i}`).join("\n");
    const edits = Array.from({ length: 10 }, (_, i) => makeEdit(i + 1, { id: 600 + i, file_path: `src/File${i}.java`,
      start_line: 10, end_line: 24, old_code: code("old"), new_code: code("new") }));
    const run = makeRunDetail(5, { status: "waiting_for_approval" });
    const started = performance.now();
    render(<ApprovalBox run={run} attempt={makeAttempt(1, { edits })} />);
    const took = performance.now() - started;
    expect(document.querySelectorAll(".diff-line")).toHaveLength(300);
    console.log(`FE5-L03: 10 edits, 300 lines rendered in ${took.toFixed(0)} ms (jsdom)`);
    expect(took).toBeLessThan(500);
  });

  it("FE5-L04: an hour of pings only: no new events, no timers pile up", async () => {
    await openRun(5, { title: "Searching the code for the bug" });
    await push(...START_EVENTS);
    await waitFor(() => expect(logLines()).toHaveLength(6));
    const timers = vi.spyOn(globalThis, "setTimeout");
    const intervals = vi.spyOn(globalThis, "setInterval");
    const html = screen.getByRole("list").innerHTML;
    for (let i = 0; i < 360; i += 1) stream.push(": ping\n\n"); // one every 10 seconds for an hour
    await act(() => new Promise((resolve) => setTimeout(resolve, FLUSH_MS * 2)));
    const ours = timers.mock.calls.filter(([, ms]) => ms === FLUSH_MS || ms >= 2000);
    expect(ours).toHaveLength(0); // no flush timer (nothing to draw) and no reconnect timer
    expect(intervals).not.toHaveBeenCalled();
    expect(screen.getByRole("list").innerHTML).toBe(html);
    expect(openConnections()).toHaveLength(1);
  });
});
