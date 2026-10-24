// FE6: the Run page, result view (F01-F07, X01-X06, S01, L01). L02 (a 50 MB download) runs in the browser.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearToken } from "../../src/api/token.js";
import { GOOD_TOKEN, VERIFIED, HttpResponse, currentPath, errorReply, fakeBackend, makeProject, renderApp, saveToken,
} from "./app_helpers.jsx";
import { installRunStream, makeAttempt, makeEdit, makeRunDetail } from "./run_helpers.js";

const { server, seen, state } = fakeBackend();
let stream;
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
  state.user = VERIFIED;
  state.projects = [makeProject(1, { name: "payments-api" })];
  state.activeRun = null;
  state.runs = null;
  state.runReplies = {};
  state.runDetails = {};
  saveToken(GOOD_TOKEN);
  stream = installRunStream();
});
afterEach(() => {
  stream.restore();
  server.resetHandlers();
  vi.restoreAllMocks();
});

const PACKAGE = { size_bytes: 2048, created_at: "2026-10-22T15:00:00Z", expires_at: "2026-10-29T15:00:00Z",
  deleted_at: null, available: true };
const baseline = (status = "failed", extra = {}) => ({ id: 1, attempt_id: null, test_type: "baseline", status,
  exit_code: status === "passed" ? 0 : 1, log_excerpt: "$ pytest\n1 failed, 47 passed in 14.2s", duration_ms: 14200,
  docker_image: "python:3.12", ...extra });
const afterFix = (attemptId, status = "passed", extra = {}) => ({ id: 2 + attemptId, attempt_id: attemptId,
  test_type: "after_fix", status, exit_code: status === "passed" ? 0 : 1, log_excerpt: "$ pytest\n48 passed in 13.8s",
  duration_ms: 13800, docker_image: "python:3.12", ...extra });

function endedRun(overrides = {}) {
  const attempts = [
    makeAttempt(1, { id: 301, reason_started: "first", status: "tests_failed", decision: "accepted",
      reverted_at: "2026-10-22T14:30:00Z", explanation: "The late fee is added again when the invoice total is saved.",
      edits: [makeEdit(1, { id: 1, file_path: "billing/invoice.py", start_line: 41, end_line: 41,
        old_code: "        invoice.total += late_fee(invoice)", new_code: "" })] }),
    makeAttempt(2, { id: 302, reason_started: "after_test_failure", status: "tests_passed", decision: "accepted",
      explanation: "late_fee() returns the fee plus the fee again. Return the fee only.",
      edits: [makeEdit(1, { id: 2, file_path: "billing/late_fee.py", start_line: 19, end_line: 19,
        old_code: "    return fee + base * rate", new_code: "    return fee" })] }),
  ];
  return makeRunDetail(41, { status: "succeeded", source_type: "local_path", language: "python",
    source_reference: "/code/payments-api", bug_description: "Late fee is added twice on overdue invoices.",
    final_summary: "The fix was applied and the tests passed.", attempts, package: PACKAGE,
    test_results: [baseline(), afterFix(301, "failed"), afterFix(302, "passed")], ...overrides });
}

async function openResult(run = endedRun()) {
  state.runDetails[run.id] = run;
  renderApp(`/runs/${run.id}`);
  await screen.findByRole("region", { name: "result" });
  return run;
}

const requests = (method, suffix) => seen.filter((r) => r.method === method && r.url.endsWith(suffix));
const button = (name) => screen.getByRole("button", { name });

describe("the result view", () => {
  it.each([
    ["succeeded", null, "Tests passed"],
    ["stopped", "cancelled_by_user", "You cancelled the run"],
    ["stopped", "timed_out", "Stopped: waited over 1 hour for you"],
    ["stopped", "reject_limit_reached", "Stopped: 3 fixes were rejected"],
    ["failed", "tests_failed_3_times", "Failed: 3 fixes did not pass the tests"],
    ["failed", "proposal_invalid", "Failed: the agent could not make a valid fix"],
    ["failed", "no_match_after_3_hints", "Failed: the bug was not found even after 3 hints"],
    ["failed", "clone_failed", "Failed: the code could not be copied (check the project source)"],
    ["failed", "test_runner_error", "Failed: a technical problem happened (details in the timeline)"],
    ["failed", "llm_error", "Failed: a technical problem happened (details in the timeline)"],
    ["failed", "something_new", "Failed: a technical problem happened (details in the timeline)"],
  ])("FE6-F01: %s / %s -> '%s', the badge and the summary", async (status, stopReason, text) => {
    await openResult(endedRun({ status, stop_reason: stopReason, final_summary: `The run ${status}. Summary text.` }));
    expect(screen.getByRole("heading", { level: 1, name: text })).toBeInTheDocument();
    const badge = within(screen.getByRole("region", { name: "result" })).getByText(status);
    expect(badge.closest(".badge")).toHaveClass(`badge-${{ succeeded: "success", failed: "danger", stopped: "muted" }[status]}`);
    expect(screen.getByText(`The run ${status}. Summary text.`)).toBeInTheDocument();
    expect(stream.connections).toHaveLength(0); // an ended run opens no stream
  });

  it("FE6-F01: the header: history / run #41 | project, language, source type and the date; no cancel button", async () => {
    await openResult();
    const crumb = document.querySelector(".breadcrumb");
    expect(crumb).toHaveTextContent("history / run #41");
    await waitFor(() => expect(crumb).toHaveTextContent("payments-api"));
    expect(crumb).toHaveTextContent("python");
    expect(crumb).toHaveTextContent("local_path");
    expect(crumb).toHaveTextContent("22 Oct 2026");
    expect(screen.getByText("Late fee is added twice on overdue invoices.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "cancel run" })).not.toBeInTheDocument();
  });

  it("FE6-F02: the baseline and the after-fix results side by side", async () => {
    await openResult();
    const base = screen.getByRole("region", { name: "baseline (before any change)" });
    const after = screen.getByRole("region", { name: "after fix (attempt #2)" });
    expect(base.querySelector(".tag")).toHaveTextContent("failed");
    expect(base).toHaveTextContent("exit code 1");
    expect(base).toHaveTextContent("duration 14.2s");
    expect(base).toHaveTextContent("image python:3.12");
    expect(base.querySelector("pre")).toHaveTextContent("1 failed, 47 passed in 14.2s");
    expect(after.querySelector(".tag")).toHaveTextContent("passed");
    expect(after).toHaveTextContent("exit code 0");
    expect(after.querySelector("pre")).toHaveTextContent("48 passed in 13.8s");
    expect(base.parentElement).toBe(after.parentElement);
    expect(screen.queryByText(/no test covers this bug/)).not.toBeInTheDocument();
  });

  it.each([
    ["the summary carries the label", { final_summary: "The fix was applied. Tests passed, but no test covers this bug, please verify the fix yourself." }, "The fix was applied."],
    ["the baseline had already passed", { test_results: [baseline("passed"), afterFix(302)] }, "The fix was applied and the tests passed."],
  ])("FE6-F02: the 'no test covers this bug' label when %s", async (_name, changes, summary) => {
    await openResult(endedRun(changes));
    expect(screen.getByRole("note")).toHaveTextContent("Tests passed, but no test covers this bug, please verify the fix yourself");
    expect(screen.getAllByText(/no test covers this bug/)).toHaveLength(1);
    expect(screen.getByText(summary)).toBeInTheDocument();
  });

  it("FE6-F03: every attempt (newest first) with its explanation, edits, decision and test status", async () => {
    await openResult();
    const cards = screen.getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["attempt #2", "attempt #1"]);
    expect(cards[0]).toHaveTextContent("attempt #2 after_test_failure · decision: accepted");
    expect(cards[0].querySelector(".tag")).toHaveTextContent("tests_passed");
    expect(cards[0]).toHaveTextContent("late_fee() returns the fee plus the fee again. Return the fee only.");
    expect(cards[0].querySelector(".diff-header")).toHaveTextContent("billing/late_fee.pylines 19–19 · edit 1");
    expect(cards[0].querySelector(".diff-old")).toHaveTextContent("return fee + base * rate");
    expect(cards[0].querySelector(".diff-new")).toHaveTextContent("return fee");
    expect(cards[1]).toHaveTextContent("attempt #1 first · decision: accepted · reverted");
    expect(cards[1].querySelector(".tag")).toHaveTextContent("tests_failed");
    expect(cards[1].querySelector(".diff-new")).toHaveTextContent("(line deleted)");
  });

  it("FE6-F03: a rejected attempt shows the decision and the feedback", async () => {
    await openResult(endedRun({ status: "stopped", stop_reason: "reject_limit_reached", attempts: [
      makeAttempt(1, { id: 301, status: "rejected", decision: "rejected", decision_feedback: "Not like this." })],
    test_results: [baseline()] }));
    const card = screen.getByRole("article", { name: "attempt #1" });
    expect(card).toHaveTextContent("decision: rejected");
    expect(card).toHaveTextContent("your feedback › Not like this.");
    expect(card.querySelector(".tag")).toHaveTextContent("rejected");
  });

  it("FE6-F04: download fetches with the token and saves the file with a good name", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:zip"), revokeObjectURL: vi.fn() }));
    const saved = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click() {
      saved.push([this.download, this.href]);
    });
    const user = userEvent.setup();
    await openResult();
    expect(screen.getByText("Package is kept for 7 days")).toBeInTheDocument();
    await user.click(button("download package"));
    await waitFor(() => expect(saved).toEqual([["sangra-run-41.zip", "blob:zip"]]));
    const [request] = requests("GET", "/runs/41/package");
    expect(request.headers.get("authorization")).toBe(`Bearer ${GOOD_TOKEN}`);
    expect(URL.createObjectURL.mock.calls[0][0].size).toBe(4);
    vi.unstubAllGlobals();
  });

  // FE6-F05: status x source type x applied.
  const CASES = [];
  for (const status of ["succeeded", "failed", "stopped"]) {
    for (const source of ["local_path", "github", "zip"]) {
      for (const applied of [false, true]) CASES.push([status, source, applied, status === "succeeded" && source === "local_path" && !applied]);
    }
  }
  it.each(CASES)("FE6-F05: %s, %s, applied=%s -> 'apply to my folder' shown: %s", async (status, source, applied, shown) => {
    await openResult(endedRun({ status, source_type: source, stop_reason: status === "succeeded" ? null : "timed_out",
      applied_to_source_at: applied ? "2026-10-22T15:00:00Z" : null }));
    expect(screen.queryByRole("button", { name: "apply to my folder" }) !== null).toBe(shown);
  });

  it("FE6-F06: apply succeeds -> the button changes to 'applied'", async () => {
    const user = userEvent.setup();
    await openResult();
    await user.click(button("apply to my folder"));
    expect(await screen.findByRole("button", { name: "applied" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "apply to my folder" })).not.toBeInTheDocument();
    expect(requests("POST", "/runs/41/apply-to-source")).toHaveLength(1);
  });

  it("FE6-F07: delete: a confirm box, then the call, then the history", async () => {
    state.runs = [];
    const user = userEvent.setup();
    await openResult();
    await user.click(button("delete run"));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this run?" });
    await user.click(within(dialog).getByRole("button", { name: "cancel" }));
    expect(requests("DELETE", "/runs/41")).toHaveLength(0);
    await user.click(button("delete run"));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "delete run" }));
    await waitFor(() => expect(currentPath()).toBe("/runs"));
    expect(requests("DELETE", "/runs/41")).toHaveLength(1);
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();
  });
});

describe("problems", () => {
  it("FE6-X01: PACKAGE_EXPIRED on download -> 'The package expired (kept 7 days)', the button is disabled", async () => {
    state.runReplies.package = () => errorReply(410, "PACKAGE_EXPIRED", "The package has expired (packages are kept 7 days).");
    const user = userEvent.setup();
    await openResult();
    await user.click(button("download package"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The package expired (kept 7 days)");
    expect(button("download package")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "retry" })).not.toBeInTheDocument();
  });

  it("FE6-X01: a package already expired or deleted when the page opens -> disabled, with the text", async () => {
    await openResult(endedRun({ package: { ...PACKAGE, deleted_at: "2026-10-29T15:00:00Z", available: false } }));
    expect(button("download package")).toBeDisabled();
    expect(screen.getByText("The package expired (kept 7 days)")).toBeInTheDocument();
  });

  it.each([
    ["SOURCE_CHANGED", 409, "Your folder changed since the run, so nothing was written. Download the patch instead."],
    ["LOCAL_PATH_NOT_ALLOWED", 400, "Apply to my folder works only for local folder projects when the backend runs on your own machine. Download the package instead."],
    ["RUN_STATE_INVALID", 409, "You cannot apply this run to your folder (only a succeeded run, once) in the run's current state."],
  ])("FE6-X02: %s on apply -> the message", async (code, status, message) => {
    state.runReplies.apply = () => errorReply(status, code, message);
    const user = userEvent.setup();
    await openResult();
    await user.click(button("apply to my folder"));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(button("apply to my folder")).toBeEnabled();
  });

  it("FE6-X03: an active run has no delete button", async () => {
    state.runDetails[41] = endedRun({ status: "testing", stop_reason: null });
    renderApp("/runs/41");
    await screen.findByRole("heading", { name: "Running the tests in Docker" });
    expect(screen.queryByRole("button", { name: "delete run" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "cancel run" })).toBeInTheDocument();
  });

  it("FE6-X04: no summary, no tokens, no after-fix result, no package -> 'not available' texts, no crash", async () => {
    await openResult(endedRun({ status: "failed", stop_reason: "test_runner_error", final_summary: null,
      total_tokens: null, package: null, test_results: [baseline()], attempts: [] }));
    expect(screen.getByText("No summary available.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "after fix" })).toHaveTextContent("not available");
    expect(screen.getByText("No package available")).toBeInTheDocument();
    expect(button("download package")).toBeDisabled();
    expect(screen.getByText("No fix was proposed in this run.")).toBeInTheDocument();
  });

  it("FE6-X04: no test results at all and an attempt without edits", async () => {
    await openResult(endedRun({ status: "stopped", stop_reason: "cancelled_by_user", test_results: [],
      attempts: [makeAttempt(1, { status: "proposed", edits: [], explanation: null })] }));
    expect(screen.getByRole("region", { name: "baseline (before any change)" })).toHaveTextContent("not available");
    expect(screen.getByRole("article", { name: "attempt #1" })).toHaveTextContent("no edits");
  });

  it("FE6-X05: a failed download (network) -> a message and a retry that works", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:zip"), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    let fail = true;
    state.runReplies.package = () => (fail ? HttpResponse.error() : new HttpResponse(new Uint8Array([1, 2]), {
      headers: { "Content-Disposition": 'attachment; filename="sangra-run-41.zip"' } }));
    const user = userEvent.setup();
    await openResult();
    await user.click(button("download package"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server");
    fail = false;
    await user.click(button("retry"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    vi.unstubAllGlobals();
  });

  it("FE6-X06: a double click on Apply or on the Delete confirm sends one request", async () => {
    let answer;
    state.runReplies.apply = () => new Promise((resolve) => {
      answer = () => resolve(HttpResponse.json({ data: { ...state.runDetails[41], applied_to_source_at: "x" } }));
    });
    await openResult();
    const apply = button("apply to my folder");
    fireEvent.click(apply);
    fireEvent.click(apply);
    await waitFor(() => expect(answer).toBeDefined());
    answer();
    await screen.findByRole("button", { name: "applied" });
    expect(requests("POST", "/runs/41/apply-to-source")).toHaveLength(1);

    state.runs = [];
    fireEvent.click(button("delete run"));
    const confirm = within(screen.getByRole("alertdialog")).getByRole("button", { name: "delete run" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(currentPath()).toBe("/runs"));
    expect(requests("DELETE", "/runs/41")).toHaveLength(1);
  });
});

describe("security and load", () => {
  it("FE6-S01: final_summary, file names, logs and explanations with HTML are plain text", async () => {
    const html = '<img src=x onerror="window.hacked=1">';
    await openResult(endedRun({ final_summary: `Done ${html}`, test_results: [baseline("failed", { log_excerpt: `<b>log</b>${html}` })],
      attempts: [makeAttempt(1, { id: 301, explanation: `<i>why</i>${html}`, status: "tests_passed",
        edits: [makeEdit(1, { file_path: `<script>window.hacked=2</script>.py` })] })] }));
    expect(screen.getByText(`Done ${html}`)).toBeInTheDocument();
    expect(screen.getByText("<script>window.hacked=2</script>.py")).toBeInTheDocument();
    expect(document.querySelector("main").querySelector("img, script, b, i")).toBeNull();
    expect(window.hacked).toBeUndefined();
  });

  it("FE6-L01: 3 attempts x 10 edits x 300 lines render in under 1 second", async () => {
    const code = (tag) => Array.from({ length: 15 }, (_, i) => `    value_${tag}_${i} = compute(${i})  # line ${i}`).join("\n");
    const attempts = [1, 2, 3].map((n) => makeAttempt(n, { id: 300 + n, status: "tests_failed", decision: "accepted",
      edits: Array.from({ length: 10 }, (_, i) => makeEdit(i + 1, { id: n * 100 + i, file_path: `src/file_${i}.py`,
        start_line: 10, end_line: 24, old_code: code("old"), new_code: code("new") })) }));
    const run = endedRun({ status: "failed", stop_reason: "tests_failed_3_times", attempts,
      test_results: [baseline(), afterFix(303, "failed")] });
    state.runDetails[41] = run;
    const started = performance.now();
    renderApp("/runs/41");
    await screen.findByRole("region", { name: "result" });
    const took = performance.now() - started;
    expect(document.querySelectorAll(".diff-line")).toHaveLength(900);
    console.log(`FE6-L01: 3 attempts x 10 edits (900 diff lines) rendered in ${took.toFixed(0)} ms (jsdom, whole app)`);
    expect(took).toBeLessThan(1000);
  });
});
