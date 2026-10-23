// Helpers for the page tests (FE2 on): the whole app (router, auth state, query client) with a fake backend (MSW).
import { StrictMode } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import App from "../../src/App.jsx";
import { AuthProvider } from "../../src/auth/AuthProvider.jsx";
import { createQueryClient } from "../../src/queryClient.js";
import { API_URL } from "../../src/config.js";
import { TOKEN_KEY } from "../../src/api/token.js";

export { http, HttpResponse };
export const api = (path) => API_URL + path;
export const GOOD_TOKEN = "aaa.bbb.ccc";
export const NEW_TOKEN = "ddd.eee.fff";

export const VERIFIED = { id: 7, name: "Sangram", email: "sangram@example.com", is_email_verified: true,
  created_at: "2026-10-20T10:00:00Z" };
export const UNVERIFIED = { ...VERIFIED, is_email_verified: false };

export const errorReply = (status, code, message) => HttpResponse.json({ error: { code, message } }, { status });
const unauthorized = () => errorReply(401, "UNAUTHORIZED", "Login required, or the session has expired.");

/** A fake backend that knows one user. Returns { server, seen, state }; state.user can be changed by a test. */
export function fakeBackend() {
  const seen = [];
  const state = { user: VERIFIED, tokens: new Set([GOOD_TOKEN]) };
  const authed = (request) => state.tokens.has((request.headers.get("authorization") || "").replace("Bearer ", ""));
  const server = setupServer(
    http.get(api("/auth/me"), ({ request }) => (authed(request) ? HttpResponse.json({ data: state.user }) : unauthorized())),
    http.post(api("/auth/login"), async ({ request }) => {
      const body = await request.json();
      if (body.email === state.user.email && body.password === "Str0ng-pass") {
        state.tokens.add(NEW_TOKEN);
        return HttpResponse.json({ data: { user: state.user, token: NEW_TOKEN } });
      }
      return errorReply(401, "INVALID_CREDENTIALS", "Wrong email or password.");
    }),
    http.post(api("/auth/resend-verification"), ({ request }) =>
      authed(request) ? HttpResponse.json({ data: { message: "We sent a new verification link." } }) : unauthorized()),
  );
  server.events.on("request:start", ({ request }) => seen.push(request.clone()));
  return { server, seen, state };
}

export function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

export const currentPath = () => screen.getByTestId("location").textContent;

export function saveToken(token) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function renderApp(path, { strict = false } = {}) {
  const tree = (
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
          <LocationProbe />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}
