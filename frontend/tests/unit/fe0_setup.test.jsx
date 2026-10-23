// Phase FE0: project setup.
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "../../src/App.jsx";
import { DEFAULT_API_URL, apiUrlFrom } from "../../src/config.js";
import { createQueryClient } from "../../src/queryClient.js";

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

describe("FE0-F01: the route table of F1", () => {
  it("/ goes to the projects page", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { name: "Projects" })).toBeInTheDocument();
  });

  it.each([
    ["/login", "Log in"], ["/register", "Create your account"], ["/verify-email?token=x", "Verify email"],
    ["/forgot-password", "Forgot password"], ["/reset-password?token=x", "Set a new password"],
    ["/account", "Account"], ["/projects", "Projects"], ["/projects/new", "New project"],
    ["/projects/3/edit", "Edit project"], ["/projects/3/run", "Start a run"], ["/runs", "History"],
    ["/runs/42", "Run"],
  ])("%s has its page", (path, title) => {
    renderAt(path);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.queryByText("Page not found")).not.toBeInTheDocument();
  });
});
