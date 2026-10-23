// FE3: the projects pages (F01-F05, X01-X07, S01, S02, U01, U02, L01).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { AuthContext } from "../../src/auth/useAuth.js";
import ProjectsPage from "../../src/pages/ProjectsPage.jsx";
import userEvent from "@testing-library/user-event";
import { clearToken } from "../../src/api/token.js";
import { createQueryClient } from "../../src/queryClient.js";
import {
  GOOD_TOKEN, UNVERIFIED, VERIFIED, HttpResponse, api, currentPath, errorReply, fakeBackend, http, makeProject,
  projectRoutes, renderApp, saveToken,
} from "./app_helpers.jsx";

const { server, seen, state } = fakeBackend();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  window.localStorage.clear();
  clearToken();
  seen.length = 0;
  state.user = VERIFIED;
  state.projects = [makeProject(1, { name: "summer-sale-service" }),
    makeProject(2, { name: "payments-api", language: "python", source_type: "local_path",
      source_reference: "C:\\code\\payments-api", branch: null, test_command: "pytest" }),
    makeProject(3, { name: "inventory-tool", language: "python", source_type: "zip", source_reference: null,
      test_command: "pytest -q" })];
  state.activeRun = null;
  server.use(...projectRoutes(state));
  saveToken(GOOD_TOKEN);
});
afterEach(() => server.resetHandlers());

const requests = (method, path) => seen.filter((r) => r.method === method && new URL(r.url).pathname === `/api/v1${path}`);
const rows = () => screen.getAllByRole("row").slice(1);
const rowOf = (name) => rows().find((row) => within(row).queryByText(name));

async function openForm(user, path = "/projects/new") {
  renderApp(path);
  await screen.findByRole("heading", { name: path.endsWith("edit") ? "Edit project" : "New project" });
  await screen.findByLabelText("Name"); // the edit form appears once the project is loaded
  return user;
}

async function fill(user, values) {
  for (const [label, value] of Object.entries(values)) {
    const field = screen.getByLabelText(label, { exact: false });
    await user.clear(field);
    if (value) await user.type(field, value);
  }
}

describe("list", () => {
  it("FE3-F01: loading, then the projects with their columns", async () => {
    renderApp("/projects");
    expect(await screen.findByRole("status", { name: "Loading projects" })).toBeInTheDocument();
    await screen.findByText("summer-sale-service");
    expect(rows()).toHaveLength(3);
    const zip = rowOf("inventory-tool");
    expect(within(zip).getByText(/zip uploaded per run/)).toBeInTheDocument();
    expect(within(rowOf("payments-api")).getByText("C:\\code\\payments-api")).toBeInTheDocument();
    expect(within(rowOf("summer-sale-service")).getByText("default")).toBeInTheDocument(); // no branch
  });

  it("FE3-F01: the empty state", async () => {
    state.projects = [];
    const user = userEvent.setup();
    renderApp("/projects");
    expect(await screen.findByText("No projects yet - create one")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "new project" }));
    expect(await screen.findByRole("heading", { name: "New project" })).toBeInTheDocument();
  });

  it("FE3-F05: the Run button opens the start page of that project", async () => {
    const user = userEvent.setup();
    renderApp("/projects");
    await user.click(await screen.findByRole("link", { name: "run payments-api" }));
    expect(currentPath()).toBe("/projects/2/run");
  });

  it("FE3-X05: the list request fails -> an error with Retry", async () => {
    server.use(http.get(api("/projects"), () => HttpResponse.error()));
    const user = userEvent.setup();
    renderApp("/projects");
    expect(await screen.findByText("Cannot reach the server")).toBeInTheDocument();
    server.use(...projectRoutes(state));
    await user.click(screen.getByRole("button", { name: "retry" }));
    expect(await screen.findByText("summer-sale-service")).toBeInTheDocument();
  });

  it("FE3-X05: a 500 also shows the error with Retry", async () => {
    server.use(http.get(api("/projects"), () => errorReply(500, "INTERNAL_ERROR", "Something went wrong on the server.")));
    renderApp("/projects");
    expect(await screen.findByText("Something went wrong on the server.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "retry" })).toBeInTheDocument();
  });

  it("FE3-X07: an unverified user -> Run is disabled with the reason", async () => {
    state.user = UNVERIFIED;
    renderApp("/projects");
    await screen.findByText("summer-sale-service");
    const run = within(rowOf("summer-sale-service")).getByRole("button", { name: "run summer-sale-service" });
    expect(run).toBeDisabled();
    expect(within(rowOf("summer-sale-service")).getByText("Verify your email to start runs")).toBeInTheDocument();
  });

  it("FE3-X07: an active run -> Run is disabled with the reason", async () => {
    state.activeRun = { id: 42, status: "searching", project_id: 1 };
    renderApp("/projects");
    await waitFor(() => expect(screen.getAllByText("A run is already in progress")).toHaveLength(3));
    expect(screen.getByRole("button", { name: "run inventory-tool" })).toBeDisabled();
  });

  it("FE3-S01: a project name with HTML or script text is plain text", async () => {
    state.projects = [makeProject(9, { name: "<img src=x onerror=\"window.hacked=3\"><script>window.hacked=4</script>" })];
    const view = renderApp("/projects");
    expect(await screen.findByText("<img src=x onerror=\"window.hacked=3\"><script>window.hacked=4</script>")).toBeInTheDocument();
    expect(view.container.querySelector("tbody img, tbody script")).toBeNull();
    expect(window.hacked).toBeUndefined();
  });

  it("FE3-U01: long names, URLs and commands are cut with the full text as a tooltip", async () => {
    const name = "n".repeat(100);
    const url = `https://github.com/${"o".repeat(39)}/${"r".repeat(100)}`;
    state.projects = [makeProject(9, { name, source_reference: url, test_command: "c".repeat(500), branch: "b".repeat(255) })];
    renderApp("/projects");
    const cell = await screen.findByText(name);
    expect(cell).toHaveAttribute("title", name);
    expect(cell).toHaveClass("clip");
    expect(screen.getByText(url)).toHaveAttribute("title", url);
    expect(screen.getByText("c".repeat(500))).toHaveAttribute("title", "c".repeat(500));
  });

  it("FE3-L01: 20 projects render in under 200 ms", async () => {
    // Only the render is measured: the data is already loaded and the user known (no network in the time).
    const projects = Array.from({ length: 20 }, (_, i) => makeProject(i + 1, { name: `project-number-${i + 1}` }));
    const queryClient = createQueryClient();
    queryClient.setQueryData(["projects"], { items: projects, total: 20 });
    queryClient.setQueryData(["activeRun"], null);
    const auth = { status: "loggedIn", user: VERIFIED };
    const page = (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><AuthContext.Provider value={auth}><ProjectsPage /></AuthContext.Provider></MemoryRouter>
      </QueryClientProvider>
    );
    render(page); // warm-up (module and style setup)
    cleanup();
    const started = performance.now();
    render(page);
    const ms = performance.now() - started;
    console.log(`FE3-L01: 20 projects rendered in ${ms.toFixed(1)} ms`);
    expect(rows()).toHaveLength(20);
    expect(ms).toBeLessThan(200);
  });
});

describe("create", () => {
  it("FE3-F02: a github project: sent, the list updates", async () => {
    const user = await openForm(userEvent.setup());
    await fill(user, { Name: "summer-sale-v2", "GitHub URL": "https://github.com/sangram/summer-sale-service",
      Branch: "feature/x-1", "Test command": "mvn test" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    expect(await screen.findByText("summer-sale-v2")).toBeInTheDocument();
    expect(currentPath()).toBe("/projects");
    expect(await requests("POST", "/projects")[0].json()).toEqual({ name: "summer-sale-v2", language: "java",
      source_type: "github", source_reference: "https://github.com/sangram/summer-sale-service", branch: "feature/x-1",
      test_command: "mvn test" });
  });

  it("FE3-F02: a local folder project", async () => {
    const user = await openForm(userEvent.setup());
    await user.click(screen.getByRole("radio", { name: "python" }));
    await user.click(screen.getByRole("radio", { name: "local_path" }));
    expect(screen.queryByLabelText("Branch", { exact: false })).not.toBeInTheDocument();
    await fill(user, { Name: "payments", "Folder path": "C:\\code\\payments-api", "Test command": "pytest" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    await screen.findByText("payments");
    expect(await requests("POST", "/projects")[0].json()).toEqual({ name: "payments", language: "python",
      source_type: "local_path", source_reference: "C:\\code\\payments-api", test_command: "pytest" });
  });

  it("FE3-F02: a zip project hides the reference field", async () => {
    const user = await openForm(userEvent.setup());
    await user.click(screen.getByRole("radio", { name: "zip" }));
    expect(screen.queryByLabelText("GitHub URL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Folder path")).not.toBeInTheDocument();
    expect(screen.getByText("You upload a .zip of the code with each run.")).toBeInTheDocument();
    await fill(user, { Name: "inventory", "Test command": "pytest -q" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    await screen.findByText("inventory");
    expect(await requests("POST", "/projects")[0].json()).toEqual({ name: "inventory", language: "java",
      source_type: "zip", test_command: "pytest -q" });
  });

  it.each([
    ["Name", "", "Name must be 1-100 characters."],
    ["Name", "x".repeat(101), "Name must be 1-100 characters."],
    ["Test command", "", "Test command must be 1-500 characters."],
    ["Test command", "c".repeat(501), "Test command must be 1-500 characters."],
    ["GitHub URL", "https://gitlab.com/a/b", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "http://github.com/a/b", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "https://github.com/a", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "https://github.com/a/b; rm -rf /", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "ext::sh -c touch", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "-https://github.com/a/b", "Must look like https://github.com/owner/repo"],
    ["GitHub URL", "https://github.com/a/-x", "Must look like https://github.com/owner/repo"],
    ["Branch", "my branch", "Not a valid branch name"],
    ["Branch", "-x", "Not a valid branch name"],
    ["Branch", "a;b", "Not a valid branch name"],
    ["Branch", "$(id)", "Not a valid branch name"],
    ["Branch", "a..b", "Not a valid branch name"],
  ])("FE3-X01: %s = %j is refused before sending", async (label, value, message) => {
    const user = await openForm(userEvent.setup());
    await fill(user, { Name: "ok", "GitHub URL": "https://github.com/a/b", "Test command": "mvn test", [label]: value });
    await user.click(screen.getByRole("button", { name: "create project" }));
    expect(await screen.findByText(new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
    expect(screen.getByLabelText(label, { exact: false })).toHaveFocus();
    expect(requests("POST", "/projects")).toHaveLength(0);
  });

  it.each([
    ["code/shop", "Must be an absolute folder path"],
    ["", "Enter the folder path."],
    ["/home/me/../etc", "Must not contain '..'."],
    ["C:\\code\\..\\x", "Must not contain '..'."],
  ])("FE3-X01: local path %j is refused before sending", async (value, message) => {
    const user = await openForm(userEvent.setup());
    await user.click(screen.getByRole("radio", { name: "local_path" }));
    await fill(user, { Name: "ok", "Folder path": value, "Test command": "pytest" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    expect(await screen.findByText(new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument();
    expect(requests("POST", "/projects")).toHaveLength(0);
  });

  it("FE3-X01: a valid branch like feature/x-1 is accepted", async () => {
    const user = await openForm(userEvent.setup());
    await fill(user, { Name: "ok", "GitHub URL": "https://github.com/a/b", Branch: "release-2.0", "Test command": "t" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    await screen.findByRole("heading", { name: "Projects" });
    expect(requests("POST", "/projects")).toHaveLength(1);
  });

  it.each([
    [422, "VALIDATION_ERROR", "Invalid request. test_command: String should have at most 500 characters"],
    [409, "PROJECT_LIMIT_REACHED", "You can have at most 20 projects. Delete one first."],
    [400, "LOCAL_PATH_NOT_ALLOWED", "Local folders work only when the backend runs on your own machine."],
    [400, "PATH_NOT_FOUND", "The folder does not exist or cannot be read."],
  ])("FE3-X02 / X03: %i %s is shown on the form", async (status, code, message) => {
    server.use(http.post(api("/projects"), () => errorReply(status, code, message)));
    const user = await openForm(userEvent.setup());
    await fill(user, { Name: "ok", "GitHub URL": "https://github.com/a/b", "Test command": "mvn test" });
    await user.click(screen.getByRole("button", { name: "create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(currentPath()).toBe("/projects/new");
  });

  it("FE3-X06: a double click on Create makes one project", async () => {
    let release;
    server.use(http.post(api("/projects"), async () => {
      await new Promise((resolve) => { release = resolve; });
      return HttpResponse.json({ data: makeProject(50) }, { status: 201 });
    }));
    const user = await openForm(userEvent.setup());
    await fill(user, { Name: "ok", "GitHub URL": "https://github.com/a/b", "Test command": "mvn test" });
    const button = screen.getByRole("button", { name: "create project" });
    await user.dblClick(button);
    await user.click(button);
    expect(requests("POST", "/projects")).toHaveLength(1);
    release();
    await screen.findByRole("heading", { name: "Projects" });
  });
});

describe("edit", () => {
  it("FE3-F03: the form is filled; only changed fields are sent", async () => {
    const user = await openForm(userEvent.setup(), "/projects/1/edit");
    expect(screen.getByLabelText("Name")).toHaveValue("summer-sale-service");
    expect(screen.getByLabelText("GitHub URL")).toHaveValue("https://github.com/sangram/project-1");
    expect(screen.getByRole("radio", { name: "java" })).toBeChecked();
    expect(screen.getByRole("button", { name: "save changes" })).toBeInTheDocument();
    await fill(user, { Name: "summer-sale-renamed" });
    await user.click(screen.getByRole("button", { name: "save changes" }));
    expect(await screen.findByText("summer-sale-renamed")).toBeInTheDocument();
    expect(await requests("PATCH", "/projects/1")[0].json()).toEqual({ name: "summer-sale-renamed" });
  });

  it("FE3-F03: clearing the branch sends null; switching to zip clears the reference", async () => {
    state.projects[0].branch = "develop";
    const user = await openForm(userEvent.setup(), "/projects/1/edit");
    await fill(user, { Branch: "" });
    await user.click(screen.getByRole("radio", { name: "zip" }));
    await user.click(screen.getByRole("button", { name: "save changes" }));
    await screen.findByRole("heading", { name: "Projects" });
    expect(await requests("PATCH", "/projects/1")[0].json()).toEqual({ source_type: "zip", source_reference: null,
      branch: null });
  });

  it("nothing changed -> no request, back to the list", async () => {
    const user = await openForm(userEvent.setup(), "/projects/2/edit");
    await user.click(screen.getByRole("button", { name: "save changes" }));
    await screen.findByRole("heading", { name: "Projects" });
    expect(requests("PATCH", "/projects/2")).toHaveLength(0);
  });

  it("FE3-X03: INVALID_PROJECT_CONFIG on edit is shown on the form", async () => {
    server.use(http.patch(api("/projects/1"), () =>
      errorReply(400, "INVALID_PROJECT_CONFIG", "The project would not be valid: source_reference is required.")));
    const user = await openForm(userEvent.setup(), "/projects/1/edit");
    await fill(user, { Name: "x" });
    await user.click(screen.getByRole("button", { name: "save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The project would not be valid");
  });

  it("FE3-X04: the project was deleted in another tab -> a message and back to the list", async () => {
    const user = await openForm(userEvent.setup(), "/projects/1/edit");
    state.projects = state.projects.filter((p) => p.id !== 1); // the other tab
    await fill(user, { Name: "too late" });
    await user.click(screen.getByRole("button", { name: "save changes" }));
    expect(await screen.findByText("This project no longer exists (it was deleted).")).toBeInTheDocument();
    expect(currentPath()).toBe("/projects");
    expect(screen.queryByText("summer-sale-service")).not.toBeInTheDocument();
  });

  it("FE3-S02: /projects/5/edit of another user's (or a missing) project -> the not-found page", async () => {
    renderApp("/projects/5/edit");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

describe("delete", () => {
  it("FE3-F04: a confirm box; Cancel does nothing; Confirm deletes and the list updates", async () => {
    const user = userEvent.setup();
    renderApp("/projects");
    await user.click(await screen.findByRole("button", { name: "delete payments-api" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete project?" });
    expect(dialog).toHaveTextContent("payments-api and all its runs will be deleted.");
    await user.click(within(dialog).getByRole("button", { name: "cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(requests("DELETE", "/projects/2")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "delete payments-api" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "delete" }));
    await waitFor(() => expect(screen.queryByText("payments-api")).not.toBeInTheDocument());
    expect(requests("DELETE", "/projects/2")).toHaveLength(1);
    expect(rows()).toHaveLength(2);
  });

  it("FE3-X03: PROJECT_HAS_ACTIVE_RUN is shown where the action was; nothing is deleted", async () => {
    server.use(http.delete(api("/projects/1"), () =>
      errorReply(409, "PROJECT_HAS_ACTIVE_RUN", "This project has a run in progress. Cancel it first.")));
    const user = userEvent.setup();
    renderApp("/projects");
    await user.click(await screen.findByRole("button", { name: "delete summer-sale-service" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This project has a run in progress. Cancel it first.");
    expect(screen.getByText("summer-sale-service")).toBeInTheDocument();
  });

  it("FE3-U02: the dialog works with the keyboard and the focus returns after closing", async () => {
    const user = userEvent.setup();
    renderApp("/projects");
    const opener = await screen.findByRole("button", { name: "delete inventory-tool" });
    opener.focus();
    await user.keyboard("{Enter}");
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByRole("button", { name: "cancel" })).toHaveFocus();
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "delete" })).toHaveFocus();
    await user.tab(); // stays inside the box
    expect(within(dialog).getByRole("button", { name: "cancel" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(requests("DELETE", "/projects/3")).toHaveLength(0);
  });
});
