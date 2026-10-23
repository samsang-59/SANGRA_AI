// One function per run route (F2.2). The live stream is in stream.js.
import { ApiError, request } from "./client.js";

const id = (value) => encodeURIComponent(String(value));

/** A zip project sends multipart/form-data with the zip; the others send JSON. */
export function startRun(projectId, bugDescription, zipFile, { signal } = {}) {
  if (zipFile) {
    const form = new FormData();
    form.append("bug_description", bugDescription);
    form.append("zip_file", zipFile, zipFile.name);
    return request("POST", `/projects/${id(projectId)}/runs`, { body: form, signal });
  }
  return request("POST", `/projects/${id(projectId)}/runs`, { body: { bug_description: bugDescription }, signal });
}

/** filters: { projectId, status, page, limit }. Returns { items, total, page, limit }. */
export function listRuns({ projectId, status, page, limit } = {}) {
  return request("GET", "/runs", { query: { project_id: projectId, status, page, limit } });
}

export const getRun = (runId) => request("GET", `/runs/${id(runId)}`);

export function submitApproval(runId, attemptId, decision, feedback) {
  const body = { attempt_id: attemptId, decision };
  if (feedback) body.feedback = feedback;
  return request("POST", `/runs/${id(runId)}/approval`, { body });
}

export const sendHint = (runId, text) => request("POST", `/runs/${id(runId)}/hint`, { body: { text } });
export const cancelRun = (runId) => request("POST", `/runs/${id(runId)}/cancel`);
export const applyToSource = (runId) => request("POST", `/runs/${id(runId)}/apply-to-source`);
export const deleteRun = (runId) => request("DELETE", `/runs/${id(runId)}`);

/** The file name from Content-Disposition, or a safe default. */
export function fileNameFrom(disposition, runId) {
  const match = /filename="?([^";]+)"?/i.exec(disposition || "");
  const name = match ? match[1].trim() : "";
  return /^[A-Za-z0-9._-]+$/.test(name) ? name : `sangra-run-${runId}.zip`;
}

/** A plain link cannot carry the JWT, so the file is fetched as a blob and saved from here (F2.2). */
export async function downloadPackage(runId, { save = saveBlob } = {}) {
  const response = await request("GET", `/runs/${id(runId)}/package`, { raw: true, timeout: 10 * 60_000 });
  let blob;
  try {
    blob = await response.blob();
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "The download was interrupted.");
  }
  const name = fileNameFrom(response.headers.get("Content-Disposition"), runId);
  save(blob, name);
  return { name, size: blob.size };
}

export function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
