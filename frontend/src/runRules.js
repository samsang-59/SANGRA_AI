// Rules and texts shared by the run pages (start run, history).
import { ALL_STATUSES } from "./runStatus.js";

export const VERIFY_REASON = "Verify your email to start runs";
export const ACTIVE_RUN_REASON = "A run is already in progress";

export const BUG_MIN = 10;
export const BUG_MAX = 2000;
export const ZIP_MAX_BYTES = 50 * 1024 * 1024;

export const megabytes = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

/** The zip rules (F5): the name ends with .zip, at most 50 MB. Checked before any upload starts (FE4-X02). */
export function checkZip(file) {
  if (!file) return "Choose the .zip file of your code.";
  if (!file.name.toLowerCase().endsWith(".zip")) return "Only .zip files can be uploaded.";
  if (file.size > ZIP_MAX_BYTES) return `The file is ${megabytes(file.size)}; the limit is 50 MB.`;
  if (file.size === 0) return "The file is empty.";
  return "";
}

/** Filters and page live in the URL (FE4-F03). Odd values (page=abc, page=-1, status=foo) fall back to the default. */
export function readFilters(params) {
  const page = Number(params.get("page"));
  const project = Number(params.get("project"));
  const status = params.get("status");
  return {
    page: Number.isInteger(page) && page >= 1 && page <= 1_000_000 ? page : 1,
    projectId: Number.isInteger(project) && project > 0 ? project : null,
    status: status === "active" || ALL_STATUSES.includes(status) ? status : null,
  };
}

