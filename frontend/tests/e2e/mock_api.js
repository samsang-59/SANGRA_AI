// A fake backend inside the browser (Playwright routes) for the page tests that do not need the real one.
export const API = "http://localhost:8000/api/v1";

export async function mockApi(page, { user, token = "aaa.bbb.ccc", projects = [], activeRun = null, runs = [],
  runDetails = {} } = {}) {
  const state = { user, token, calls: [], projects, activeRun, runs, uploads: [], runDetails, posts: [] };
  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    state.calls.push(`${request.method()} ${path}`);
    const json = (status, body) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body),
      headers: { "Access-Control-Allow-Origin": "*" } });
    const authed = request.headers().authorization === `Bearer ${state.token}`;
    if (request.method() === "OPTIONS") {
      return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "*" } });
    }
    if (path === "/auth/register") {
      const body = request.postDataJSON();
      state.user = { id: 1, name: body.name, email: body.email, is_email_verified: false };
      return json(201, { data: { user: state.user, token: state.token } });
    }
    if (path === "/auth/login") {
      const body = request.postDataJSON();
      if (state.user && body.email === state.user.email && body.password === "Str0ng-pass") {
        return json(200, { data: { user: state.user, token: state.token } });
      }
      return json(401, { error: { code: "INVALID_CREDENTIALS", message: "Wrong email or password." } });
    }
    if (!authed) return json(401, { error: { code: "UNAUTHORIZED", message: "Login required." } });
    if (path === "/auth/me") return json(200, { data: state.user });
    if (path === "/auth/resend-verification") return json(200, { data: { message: "We sent a new verification link." } });
    if (path === "/projects" && request.method() === "GET") {
      return json(200, { data: { items: state.projects, total: state.projects.length } });
    }
    const one = /^\/projects\/(\d+)$/.exec(path);
    if (one && request.method() === "GET") {
      const project = state.projects.find((p) => String(p.id) === one[1]);
      return project ? json(200, { data: project }) : json(404, { error: { code: "NOT_FOUND", message: "Not found." } });
    }
    if (one && request.method() === "DELETE") {
      state.projects = state.projects.filter((p) => String(p.id) !== one[1]);
      return json(200, { data: { message: "Deleted." } });
    }
    if (path === "/runs") {
      const query = new URL(request.url()).searchParams;
      if (query.get("status") === "active") {
        const items = state.activeRun ? [state.activeRun] : [];
        return json(200, { data: { items, total: items.length, page: 1, limit: 20 } });
      }
      let rows = state.runs;
      if (query.get("project_id")) rows = rows.filter((r) => String(r.project_id) === query.get("project_id"));
      if (query.get("status")) rows = rows.filter((r) => r.status === query.get("status"));
      const pageNo = Number(query.get("page") || 1);
      const limit = Number(query.get("limit") || 20);
      return json(200, { data: { items: rows.slice((pageNo - 1) * limit, pageNo * limit), total: rows.length,
        page: pageNo, limit } });
    }
    const run = /^\/runs\/(\d+)$/.exec(path);
    if (run && request.method() === "GET") {
      const detail = state.runDetails[run[1]];
      return detail ? json(200, { data: detail }) : json(404, { error: { code: "NOT_FOUND", message: "Not found." } });
    }
    const action = /^\/runs\/(\d+)\/(approval|hint|cancel)$/.exec(path);
    if (action && request.method() === "POST") {
      state.posts.push({ action: action[2], body: request.postDataJSON() });
      return json(200, { data: { message: "OK" } });
    }
    const start = /^\/projects\/(\d+)\/runs$/.exec(path);
    if (start && request.method() === "POST") {
      state.uploads.push({ contentType: request.headers()["content-type"], body: request.postDataBuffer() });
      return json(201, { data: { id: 77, status: "started" } });
    }
    return json(404, { error: { code: "NOT_FOUND", message: "Not found." } });
  });
  return state;
}
