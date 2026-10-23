// A fake backend inside the browser (Playwright routes) for the page tests that do not need the real one.
export const API = "http://localhost:8000/api/v1";

export async function mockApi(page, { user, token = "aaa.bbb.ccc" } = {}) {
  const state = { user, token, calls: [] };
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
    return json(404, { error: { code: "NOT_FOUND", message: "Not found." } });
  });
  return state;
}
