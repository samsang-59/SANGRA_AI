// Phase FE0: project setup.
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "../../src/App.jsx";
import { DEFAULT_API_URL, apiUrlFrom } from "../../src/config.js";
import { createQueryClient } from "../../src/queryClient.js";
import { clearToken } from "../../src/api/token.js";
import { GOOD_TOKEN, fakeBackend, renderApp, saveToken } from "./app_helpers.jsx";

function renderAt(path) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("FE0-F03 / FE0-X01: the API address comes from the env file", () => {
  it("uses VITE_API_URL when it is set", () => {
    expect(apiUrlFrom({ VITE_API_URL: "https://api.example.com/api/v1" })).toBe("https://api.example.com/api/v1");
  });

  it("drops a trailing slash and spaces", () => {
    expect(apiUrlFrom({ VITE_API_URL: "  http://10.0.0.5:9000/api/v1/ " })).toBe("http://10.0.0.5:9000/api/v1");
  });

  it.each([[{}], [{ VITE_API_URL: "" }], [{ VITE_API_URL: "   " }], [undefined]])(
    "falls back to the documented default when missing (%j)",
    (env) => {
      expect(apiUrlFrom(env)).toBe(DEFAULT_API_URL);
      expect(DEFAULT_API_URL).toBe("http://localhost:8000/api/v1");
    },
  );
});

describe("FE0-X02: unknown URLs", () => {
  it.each(["/does-not-exist", "/projects/5/unknown", "/runs/1/2/3"])("%s shows the page-not-found page", (path) => {
    renderAt(path);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "go to projects" })).toHaveAttribute("href", "/projects");
  });
});

describe("FE0-F01: the route table of F1 (with the FE2 guards)", () => {
  const { server } = fakeBackend();
  beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
  afterAll(() => server.close());
  beforeEach(() => {
    window.localStorage.clear();
    clearToken();
  });

  it("/ goes to the projects page (logged in)", async () => {
    saveToken(GOOD_TOKEN);
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
  });

  it.each([
    ["/login", "log in"], ["/register", "create account"], ["/forgot-password", "send reset link"],
    ["/reset-password?token=x", "save new password"],
  ])("%s has its page (logged out)", async (path, button) => {
    renderApp(path);
    expect(await screen.findByRole("button", { name: button })).toBeInTheDocument();
    expect(screen.queryByText("Page not found")).not.toBeInTheDocument();
  });

  it.each([
    ["/account", "Account"], ["/projects", "Projects"], ["/projects/new", "New project"],
    ["/projects/3/edit", "Edit project"], ["/projects/3/run", "Start a run"], ["/runs", "History"], ["/runs/42", "Tests passed"],
  ])("%s has its page (logged in)", async (path, title) => {
    saveToken(GOOD_TOKEN);
    renderApp(path);
    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.queryByText("Page not found")).not.toBeInTheDocument();
  });
});
