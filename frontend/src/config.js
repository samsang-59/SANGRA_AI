// Settings read at build time from the env file (F2.1). Only public values: the frontend never holds a secret.
export const DEFAULT_API_URL = "http://localhost:8000/api/v1";

export function apiUrlFrom(env) {
  const value = (env?.VITE_API_URL ?? "").trim();
  return (value || DEFAULT_API_URL).replace(/\/+$/, "");
}

export const API_URL = apiUrlFrom(import.meta.env);
