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
