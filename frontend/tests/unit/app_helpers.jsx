// Helpers for the page tests (FE2 on): the whole app (router, auth state, query client) with a fake backend (MSW).
import { StrictMode, useEffect } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import App from "../../src/App.jsx";
import { AuthProvider } from "../../src/auth/AuthProvider.jsx";
import { createQueryClient } from "../../src/queryClient.js";
import { API_URL } from "../../src/config.js";
import { TOKEN_KEY } from "../../src/api/token.js";
import { makeRunDetail } from "./run_helpers.js";

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
  // Runs 5 and 42 exist (ended) for the route and deep-link tests; the FE5 tests set their own.
  const state = { user: VERIFIED, tokens: new Set([GOOD_TOKEN]),
    runDetails: { 5: makeRunDetail(5, { status: "succeeded" }), 42: makeRunDetail(42, { status: "succeeded" }) } };
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
    ...projectRoutes(state), // base routes: they stay after server.resetHandlers()
  );
  server.events.on("request:start", ({ request }) => seen.push(request.clone()));
  return { server, seen, state };
}

const probe = { navigate: null };
/** Moves the app to another address the way a link would (the page component stays mounted if the route is the same). */
export const navigateTo = (path) => probe.navigate(path);

export function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    probe.navigate = navigate;
  }, [navigate]);
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

export const currentPath = () => screen.getByTestId("location").textContent;

export function saveToken(token) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function renderApp(path, { strict = false, queryClient = createQueryClient() } = {}) {
  const tree = (
    <QueryClientProvider client={queryClient}>
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

/** Projects and runs for the project pages (FE3 on): a small stateful fake of the project routes. */
export function projectRoutes(state) {
  state.tokens ??= new Set();
  state.projects ??= [];
  state.activeRun ??= null;
  state.nextId ??= 100;
  state.runDetails ??= {};
  state.runReplies ??= {};
  const authed = (request) => state.tokens.has((request.headers.get("authorization") || "").replace("Bearer ", ""));
  const notFound = () => errorReply(404, "NOT_FOUND", "Not found.");
  const guard = (handler) => async (info) => (authed(info.request) ? handler(info) : unauthorized());
  return [
    http.get(api("/projects"), guard(() => HttpResponse.json({ data: { items: state.projects,
      total: state.projects.length } }))),
    http.post(api("/projects"), guard(async ({ request }) => {
      const body = await request.json();
      const project = { id: state.nextId++, branch: null, source_reference: null, ...body,
        created_at: "2026-10-23T10:00:00Z", updated_at: "2026-10-23T10:00:00Z" };
      state.projects = [project, ...state.projects];
      return HttpResponse.json({ data: project }, { status: 201 });
    })),
    http.get(api("/projects/:id"), guard(({ params }) => {
      const project = state.projects.find((p) => String(p.id) === params.id);
      return project ? HttpResponse.json({ data: project }) : notFound();
    })),
    http.patch(api("/projects/:id"), guard(async ({ params, request }) => {
      const project = state.projects.find((p) => String(p.id) === params.id);
      if (!project) return notFound();
      Object.assign(project, await request.json());
      return HttpResponse.json({ data: project });
    })),
    http.delete(api("/projects/:id"), guard(({ params }) => {
      const before = state.projects.length;
      state.projects = state.projects.filter((p) => String(p.id) !== params.id);
      return before === state.projects.length ? notFound() : HttpResponse.json({ data: { message: "Deleted." } });
    })),
    http.get(api("/runs"), guard(({ request }) => {
      const query = new URL(request.url).searchParams;
      const status = query.get("status");
      if (status === "active" && !state.runs) {
        const items = state.activeRun ? [state.activeRun] : [];
        return HttpResponse.json({ data: { items, total: items.length, page: 1, limit: 20 } });
      }
      const active = ["started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing"];
      let rows = state.runs ?? [];
      if (state.activeRun && status === "active") rows = [state.activeRun, ...rows];
      if (query.get("project_id")) rows = rows.filter((r) => String(r.project_id) === query.get("project_id"));
      if (status === "active") rows = rows.filter((r) => active.includes(r.status));
      else if (status) rows = rows.filter((r) => r.status === status);
      const page = Number(query.get("page") || 1);
      const limit = Number(query.get("limit") || 20);
      const items = rows.slice((page - 1) * limit, page * limit);
      return HttpResponse.json({ data: { items, total: rows.length, page, limit } });
    })),
    // One run (FE5 on): state.runDetails[id] is the record; state.runReplies.{get,approval,hint,cancel} may answer
    // instead (a function of the request body). The live stream (/runs/:id/events) is faked in run_helpers.js.
    http.get(api("/runs/:id"), guard(({ params }) => {
      if (state.runReplies.get) return state.runReplies.get(params.id);
      const run = state.runDetails[params.id];
      return run ? HttpResponse.json({ data: run }) : notFound();
    })),
    ...["approval", "hint", "cancel"].map((action) => http.post(api(`/runs/:id/${action}`), guard(async ({ params, request }) => {
      const body = action === "cancel" ? null : await request.json();
      if (state.runReplies[action]) return state.runReplies[action](body, params.id);
      return state.runDetails[params.id] ? HttpResponse.json({ data: { message: "OK" } }) : notFound();
    }))),
    http.post(api("/projects/:id/runs"), guard(async ({ params, request }) => {
      if (state.startReply) return state.startReply(request);
      if (!state.projects.find((p) => String(p.id) === params.id)) return notFound();
      return HttpResponse.json({ data: { id: 77, project_id: Number(params.id), status: "started" } }, { status: 201 });
    })),
  ];
}

export function makeProject(id, overrides = {}) {
  return { id, name: `project-${id}`, language: "java", source_type: "github",
    source_reference: `https://github.com/sangram/project-${id}`, branch: null, test_command: "mvn test",
    created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:00Z", ...overrides };
}

export function makeRun(id, overrides = {}) {
  return { id, project_id: 1, project_name: "summer-sale-service", bug_description: "Discount shows 5% not 10%",
    status: "succeeded", stop_reason: null, created_at: "2026-10-22T14:05:00Z", ...overrides };
}
