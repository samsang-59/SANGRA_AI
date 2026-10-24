// Run statuses (03_database_design.md runs.status) and how they look (foundations: StatusBadge).
export const ACTIVE_STATUSES = ["started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing"];
export const FINAL_STATUSES = ["succeeded", "failed", "stopped"];
export const ALL_STATUSES = [...ACTIVE_STATUSES, ...FINAL_STATUSES];

/** cyan = agent working, amber = waiting for you, green = succeeded, red = failed, grey = stopped. */
export function statusTone(status) {
  if (status === "succeeded") return "success";
  if (status === "failed") return "danger";
  if (status === "stopped") return "muted";
  if (status === "waiting_for_hint" || status === "waiting_for_approval") return "accent";
  return "info";
}

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", hour12: false });

/** "22 Oct 2026, 14:05" in the browser's time zone (the screens' format). */
export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : DATE_FORMAT.format(date);
}

// F6 "Stop reason -> plain words".
const STOP_REASONS = {
  cancelled_by_user: "You cancelled the run",
  timed_out: "Stopped: waited over 1 hour for you",
  reject_limit_reached: "Stopped: 3 fixes were rejected",
  tests_failed_3_times: "Failed: 3 fixes did not pass the tests",
  proposal_invalid: "Failed: the agent could not make a valid fix",
  no_match_after_3_hints: "Failed: the bug was not found even after 3 hints",
  clone_failed: "Failed: the code could not be copied (check the project source)",
};

export function stopReasonText(stopReason) {
  if (!stopReason) return "";
  return STOP_REASONS[stopReason] ?? "Failed: a technical problem happened (details in the timeline)";
}

// "What is shown by status": the label of what is happening (the screens' titles).
export const STATUS_LABELS = {
  started: "Preparing the workspace",
  searching: "Searching the code for the bug",
  applying: "Applying the approved edits",
  testing: "Running the tests in Docker",
  waiting_for_approval: "Review the proposed fix",
  waiting_for_hint: "The agent needs a hint",
};
