// Helpers for the Run page tests (FE5 on). The run record and the actions go through the MSW fake backend
// (app_helpers.jsx); the live stream is the scripted fake of tests/unit/api/fake_stream.js, because a mocked MSW
// response here does not hand out a streamed body chunk by chunk. Only /runs/{id}/events goes to the fake.
import { installFakeStreamBackend } from "./api/fake_stream.js";

/** Call after server.listen(). Returns the stream backend (connections, push, drop, close) and restore(). */
export function installRunStream() {
  const passOn = globalThis.fetch; // MSW's fetch
  const stream = installFakeStreamBackend();
  globalThis.fetch = (url, init) => (/\/runs\/[^/]+\/events$/.test(String(url)) ? stream.fetch(url, init)
    : passOn(url, init));
  stream.restore = () => {
    globalThis.fetch = passOn;
  };
  return stream;
}

/** One SSE event as the backend writes it. */
export function event(sequence, eventType, message, details = {}, extra = {}) {
  const data = { sequence_number: sequence, event_type: eventType, message, details, attempt_id: null,
    created_at: "2026-10-22T14:05:00Z", ...extra };
  return `id: ${sequence}\nevent: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

export const statusEvent = (sequence, status, extra = {}) =>
  event(sequence, "status_changed", `Status: ${status}`, { status, ...extra });

export function makeEdit(order, overrides = {}) {
  return { id: 500 + order, edit_order: order, file_path: "src/main/java/com/shop/sale/DiscountService.java",
    start_line: 18, end_line: 18, old_code: "    private static final double SUMMER_SALE_DISCOUNT = 0.05;",
    new_code: "    private static final double SUMMER_SALE_DISCOUNT = 0.10;", explanation: "discount constant was 5% instead of 10%.",
    ...overrides };
}

export function makeAttempt(number, overrides = {}) {
  return { id: 300 + number, attempt_number: number, reason_started: number === 1 ? "first" : "after_reject",
    explanation: "The summer sale rate is set to 0.05. Changing it to 0.10.", status: "waiting_for_approval",
    decision: null, decision_feedback: null, edits: [makeEdit(1)], ...overrides };
}

export function makeRunDetail(id, overrides = {}) {
  return { id, project_id: 1, bug_description: "The summer sale shows 5% discount, it should be 10%.",
    status: "searching", language: "java", test_command: "mvn test", branch: null, source_type: "github",
    source_reference: "https://github.com/sangram/summer-sale-service", source_commit: null, llm_model: "fake",
    stop_reason: null, reject_count: 0, failed_test_count: 0, hint_count: 0, final_summary: null, total_tokens: 0,
    applied_to_source_at: null, started_at: "2026-10-22T14:05:00Z", finished_at: null,
    created_at: "2026-10-22T14:05:00Z", updated_at: "2026-10-22T14:05:00Z", attempts: [], test_results: [],
    package: null, ...overrides };
}

/** The first events of the design's searching screen. */
export const START_EVENTS = [
  statusEvent(1, "started"),
  event(2, "tool_result", "Copied the code from GitHub (commit 3f9a1c2)"),
  event(3, "tests_started", "Baseline tests started: mvn test"),
  event(4, "tests_finished", "Baseline finished: failed (1 test failing)"),
  statusEvent(5, "searching"),
  event(6, "tool_called", 'search_files("discount")'),
];
