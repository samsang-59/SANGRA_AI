// The HTTP client (05_frontend_design.md F2.1): the only place that talks to the backend.
// Base URL from the env file, the JWT header, JSON or FormData bodies, { data } unwrapped, one error shape.
import { API_URL } from "../config.js";
import { clearToken, getToken } from "./token.js";

export const REQUEST_TIMEOUT_MS = 30_000; // Gaps #11
export const UPLOAD_TIMEOUT_MS = 10 * 60_000; // a 50 MB zip on a slow line

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status; // 0 when no HTTP answer arrived
    this.code = code;
  }
}

// The 401 rule (F2.4): the auth state registers a function; the client calls it when a protected request gets 401.
let unauthorizedHandler = null;

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

/** Called after a protected request answered 401. `sentToken` is the token that request carried. When several
 * requests fail at the same moment, only the first one (whose token is still the saved one) clears it and calls the
 * handler (FE1-X04). A request sent with no token at all (another tab logged out, FE2-X06) also calls the handler;
 * the auth state ignores the call when it is already logged out. */
export function handleUnauthorized(sentToken) {
  if (sentToken !== null && getToken() !== sentToken) return;
  clearToken();
  if (unauthorizedHandler) unauthorizedHandler();
}

/** Only paths inside our API ("/projects/5"): the token is never sent to any other address (FE1-S02). */
export function apiUrl(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
    throw new ApiError(0, "BAD_PATH", "Requests go only to the API address.");
  }
  return API_URL + path;
}

/** Headers for a request: the JWT for protected routes. Returns [headers, the token that was sent]. */
export function authHeaders(isPublic) {
  const token = isPublic ? null : getToken();
  return [token ? { Authorization: `Bearer ${token}` } : {}, token];
}

const NETWORK_MESSAGE = "Cannot reach the server.";

/** Reads our error shape from a failed response, or UNKNOWN_ERROR when it is not our JSON (a proxy page, empty). */
export async function errorFromResponse(response) {
  let body;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const error = body && typeof body === "object" ? body.error : null;
  if (error && typeof error.code === "string" && typeof error.message === "string") {
    return new ApiError(response.status, error.code, error.message);
  }
  return new ApiError(response.status, "UNKNOWN_ERROR", "Something went wrong.");
}

/**
 * One request. options: body (object -> JSON, FormData -> as it is), query (object), public (no JWT, no 401 rule),
 * timeout (ms), signal (to abort from outside), raw (return the Response itself, for downloads).
 */
export async function request(method, path, options = {}) {
  const { body, query, timeout, signal } = options;
  const isPublic = Boolean(options.public);
  let url = apiUrl(path);
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    }
    const text = params.toString();
    if (text) url += `?${text}`;
  }
  const [headers, sentToken] = authHeaders(isPublic);
  let payload;
  if (body instanceof FormData) {
    payload = body; // the browser sets multipart/form-data with the boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const controller = new AbortController();
  let timedOut = false;
  const limit = timeout ?? (body instanceof FormData ? UPLOAD_TIMEOUT_MS : REQUEST_TIMEOUT_MS);
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, limit);
  const stopFromOutside = () => controller.abort();
  signal?.addEventListener("abort", stopFromOutside);

  let response;
  try {
    response = await fetch(url, { method, headers, body: payload, signal: controller.signal });
    if (response.status === 401 && !isPublic) handleUnauthorized(sentToken);
    if (!response.ok) throw await errorFromResponse(response);
    if (options.raw) return response;
    const text = await response.text();
    if (!text) return null;
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new ApiError(response.status, "UNKNOWN_ERROR", "Something went wrong.");
    }
    if (!parsed || typeof parsed !== "object" || !("data" in parsed)) {
      throw new ApiError(response.status, "UNKNOWN_ERROR", "Something went wrong.");
    }
    return parsed.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) throw new ApiError(0, "TIMEOUT", "The server did not answer in time.");
    if (signal?.aborted) throw new ApiError(0, "ABORTED", "The request was stopped.");
    throw new ApiError(0, "NETWORK_ERROR", NETWORK_MESSAGE);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", stopFromOutside);
  }
}
