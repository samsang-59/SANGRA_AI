// The saved JWT (F3: localStorage key "sangra_token"). Every access is wrapped in try/catch: localStorage can be
// blocked (private windows); then the token lives in memory for this session only.
export const TOKEN_KEY = "sangra_token";

let memoryToken = null;

export function getToken() {
  try {
    const saved = globalThis.localStorage.getItem(TOKEN_KEY);
    if (saved !== null) return saved;
  } catch {
    // blocked: fall back to memory
  }
  return memoryToken;
}

export function setToken(token) {
  memoryToken = token;
  try {
    globalThis.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // blocked: kept in memory only
  }
}

export function clearToken() {
  memoryToken = null;
  try {
    globalThis.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // blocked: nothing saved
  }
}
