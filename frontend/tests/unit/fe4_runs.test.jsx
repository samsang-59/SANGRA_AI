// FE4: the start run page and the history page (F01-F04, X01-X08, S01, L02).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearToken } from "../../src/api/token.js";
import { readFilters } from "../../src/runRules.js";
import {
  GOOD_TOKEN, UNVERIFIED, VERIFIED, HttpResponse, currentPath, errorReply, fakeBackend, makeProject, makeRun,
  renderApp, saveToken,
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
      source_reference: "C:\\code\\payments-api", test_command: "pytest" }),
    makeProject(3, { name: "inventory-tool", language: "python", source_type: "zip", source_reference: null,
      test_command: "pytest -q" })];
  state.activeRun = null;
  state.runs = null;
  state.startReply = null;
  saveToken(GOOD_TOKEN);
});
afterEach(() => server.resetHandlers());

const starts = () => seen.filter((r) => r.method === "POST" && /\/projects\/\d+\/runs$/.test(r.url));
const BUG = "The summer sale shows 5% discount, it should be 10%.";
const zipFile = (name = "inventory-tool.zip", size = 4.2 * 1024 * 1024) => {
  const file = new File([new Uint8Array([80, 75, 3, 4])], name, { type: "application/zip" });
  Object.defineProperty(file, "size", { value: Math.round(size) });
  return file;
};

async function openStart(id) {
  renderApp(`/projects/${id}/run`);
  return screen.findByLabelText("Describe the bug in plain English");
}

describe("start a run", () => {
  it.each([[1, "github"], [2, "local_path"]])("FE4-F01: project %i (%s): start -> the run page", async (id) => {
    const user = userEvent.setup();
    const box = await openStart(id);
    expect(screen.queryByLabelText("Code (.zip)")).not.toBeInTheDocument();
    await user.type(box, BUG);
    await user.click(screen.getByRole("button", { name: /start run/ }));
    await waitFor(() => expect(currentPath()).toBe("/runs/77"));
    expect(await starts()[0].json()).toEqual({ bug_description: BUG });
  });

  it("FE4-F01: the page shows the project's name, language, source type and test command", async () => {
    await openStart(3);
    for (const text of ["inventory-tool", "python", "zip", "$ pytest -q"]) expect(screen.getAllByText(text).length).toBeGreaterThan(0);
    expect(screen.getByText("One bug per run")).toBeInTheDocument();
  });

  it("FE4-F02: a zip project: a file input only here; the zip is uploaded with the text", async () => {
    const user = userEvent.setup();
    const box = await openStart(3);
    await user.type(box, BUG);
    const start = screen.getByRole("button", { name: /start run/ });
    expect(start).toBeDisabled(); // no zip yet
    await user.upload(screen.getByLabelText("Code (.zip)"), zipFile());
    expect(screen.getByText("inventory-tool.zip")).toBeInTheDocument();
    expect(screen.getByText(/4\.2 MB/)).toBeInTheDocument();
    await user.click(start);
    await waitFor(() => expect(currentPath()).toBe("/runs/77"));
    const form = await starts()[0].formData();
    expect(form.get("bug_description")).toBe(BUG);
    // jsdom's File loses its name when Node's fetch sends it; the name is checked in the browser test (fe4_runs.spec)
    expect(new Uint8Array(await form.get("zip_file").arrayBuffer())).toEqual(new Uint8Array([80, 75, 3, 4]));
  });

  it("FE4-F02: the zip can be removed and chosen again by dropping it", async () => {
    const user = userEvent.setup();
    await openStart(3);
    await user.upload(screen.getByLabelText("Code (.zip)"), zipFile("a.zip"));
    await user.click(screen.getByRole("button", { name: "remove" }));
    expect(screen.queryByText("a.zip")).not.toBeInTheDocument();
    fireEvent.drop(screen.getByText(/Drop your .zip here/).closest(".dropzone"), {
      dataTransfer: { files: [zipFile("b.zip")] } });
    expect(screen.getByText("b.zip")).toBeInTheDocument();
  });

  it.each([
    ["9 characters", "123456789", true],
    ["10 characters", "1234567890", false],
    ["2000 characters", "x".repeat(2000), false],
    ["2001 characters", "x".repeat(2001), true],
    ["only spaces", "               ", true],
  ])("FE4-X01: %s -> the button is %s", async (_name, text, disabled) => {
    await openStart(1);
    fireEvent.change(screen.getByLabelText("Describe the bug in plain English"), { target: { value: text } });
    expect(screen.getByText(`${text.trim().length} / 2000 · min 10`)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: /start run/ });
    if (disabled) {
      expect(start).toBeDisabled();
      fireEvent.submit(start.closest("form"));
      expect(starts()).toHaveLength(0);
    } else {
      expect(start).toBeEnabled();
    }
  });

  it.each([
    ["a file that is not .zip", () => new File(["x"], "code.tar.gz"), "Only .zip files can be uploaded."],
    ["a 51 MB zip", () => zipFile("big.zip", 51 * 1024 * 1024), "The file is 51.0 MB; the limit is 50 MB."],
  ])("FE4-X02: %s is refused before any upload", async (_name, file, message) => {
    const user = userEvent.setup({ applyAccept: false });
    const box = await openStart(3);
    await user.type(box, BUG);
    await user.upload(screen.getByLabelText("Code (.zip)"), file());
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: /start run/ })).toBeDisabled();
    expect(starts()).toHaveLength(0);
  });

  it("FE4-X03: ACTIVE_RUN_EXISTS -> 'You already have a run in progress' with a link to it", async () => {
    state.startReply = () => errorReply(409, "ACTIVE_RUN_EXISTS", "You already have a run in progress.");
    state.activeRun = makeRun(42, { status: "waiting_for_approval" });
    const user = userEvent.setup();
    const box = await openStart(1);
    await user.type(box, BUG);
    await user.click(screen.getByRole("button", { name: /start run/ }));
    expect(await screen.findByText("You already have a run in progress")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: /open it/ })).toHaveAttribute("href", "/runs/42"));
  });

  it.each([
    ["EMAIL_NOT_VERIFIED", 403, "Please verify your email first.", "Verify your email to start runs"],
    ["ZIP_REQUIRED", 400, "This project needs a .zip file with the code.", "This project needs a .zip file with the code."],
    ["NOT_FOUND", 404, "Not found.", "Not found."],
  ])("FE4-X04: %s shows its message", async (code, status, message, shown) => {
    state.startReply = () => errorReply(status, code, message);
    const user = userEvent.setup();
    const box = await openStart(1);
    await user.type(box, BUG);
    await user.click(screen.getByRole("button", { name: /start run/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(shown);
    expect(currentPath()).toBe("/projects/1/run");
  });

  it("FE4-X04: an unverified user cannot start; the reason is shown", async () => {
    state.user = UNVERIFIED;
    const user = userEvent.setup();
    const box = await openStart(1);
    await user.type(box, BUG);
    expect(screen.getByRole("button", { name: /start run/ })).toBeDisabled();
    expect(screen.getAllByText("Verify your email to start runs").length).toBeGreaterThan(0);
  });

  it("the start page of a project that is not yours shows the not-found page", async () => {
    renderApp("/projects/999/run");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("FE4-X05: a double click on Start makes one run", async () => {
    let release;
    state.startReply = async () => {
      await new Promise((resolve) => { release = resolve; });
      return HttpResponse.json({ data: { id: 78 } }, { status: 201 });
    };
    const user = userEvent.setup();
    const box = await openStart(1);
    await user.type(box, BUG);
    await user.dblClick(screen.getByRole("button", { name: /start run/ }));
    expect(starts()).toHaveLength(1);
    release();
    await waitFor(() => expect(currentPath()).toBe("/runs/78"));
  });

  it("FE4-X06: leaving the page during the upload stops it; no run page is opened", async () => {
    let aborted = false;
    state.startReply = (request) => new Promise((resolve) => {
      request.signal.addEventListener("abort", () => {
        aborted = true;
        resolve(HttpResponse.json({ data: { id: 79 } }, { status: 201 }));
      });
    });
    const user = userEvent.setup();
    const box = await openStart(3);
    await user.type(box, BUG);
    await user.upload(screen.getByLabelText("Code (.zip)"), zipFile());
    await user.click(screen.getByRole("button", { name: /start run/ }));
    expect(await screen.findByRole("button", { name: /uploading/ })).toBeDisabled();
    await user.click(screen.getByRole("link", { name: "cancel" }));
    await screen.findByRole("heading", { name: "Projects" });
    await waitFor(() => expect(aborted).toBe(true));
    expect(currentPath()).toBe("/projects");
  });

  it("FE4-S01 / L02: a 2,000-character text with HTML is sent and shown as plain text", async () => {
    const text = `<script>window.hacked=5</script><img src=x onerror="window.hacked=6">${"x".repeat(1931)}`;
    expect(text.length).toBe(2000);
    const user = userEvent.setup();
    const view = renderApp("/projects/1/run");
    const box = await screen.findByLabelText("Describe the bug in plain English");
    fireEvent.change(box, { target: { value: text } });
    expect(box).toHaveValue(text);
    expect(view.container.querySelector("main script, main img")).toBeNull();
    await user.click(screen.getByRole("button", { name: /start run/ }));
    await waitFor(() => expect(currentPath()).toBe("/runs/77"));
    expect((await starts()[0].json()).bug_description).toBe(text);
    expect(window.hacked).toBeUndefined();
  });
});

describe("history", () => {
  const manyRuns = (count) => Array.from({ length: count }, (_, i) => makeRun(count - i, {
    project_id: (i % 3) + 1, project_name: ["summer-sale-service", "payments-api", "inventory-tool"][i % 3],
    status: ["succeeded", "failed", "stopped"][i % 3], created_at: new Date(Date.UTC(2026, 9, 22, 14, 5) - i * 3_600_000).toISOString(),
  }));

  it("FE4-F03: a table with badge, project and date; 20 per page; page buttons", async () => {
    state.runs = manyRuns(45);
    const user = userEvent.setup();
    renderApp("/runs");
    await screen.findByText("#45");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(20);
    expect(within(rows[0]).getByText("summer-sale-service")).toBeInTheDocument();
    expect(within(rows[0]).getByText("succeeded")).toHaveClass("badge", "badge-success");
    expect(within(rows[1]).getByText("failed")).toHaveClass("badge-danger");
    expect(within(rows[2]).getByText("stopped")).toHaveClass("badge-muted");
    expect(within(rows[0]).getByText(/^\d{1,2} Oct 2026, \d{2}:\d{2}$/)).toBeInTheDocument();
    expect(screen.getByText("20 per page · page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prev/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /next/ }));
    await screen.findByText("#25");
    expect(currentPath()).toBe("/runs?page=2");
    expect(screen.getByText("20 per page · page 2 of 3")).toBeInTheDocument();
  });

  it("FE4-F03: the filters are sent and saved in the URL", async () => {
    state.runs = manyRuns(45);
    const user = userEvent.setup();
    renderApp("/runs");
    await screen.findByText("#45");
    await user.selectOptions(screen.getByLabelText("project"), "payments-api");
    await user.selectOptions(screen.getByLabelText("status"), "failed");
    await waitFor(() => expect(currentPath()).toBe("/runs?project=2&status=failed"));
    await waitFor(() => expect(screen.getAllByRole("row").slice(1).every((r) => within(r).queryByText("failed"))).toBe(true));
    const last = seen.filter((r) => r.url.includes("/runs?")).at(-1);
    expect(new URL(last.url).search).toBe("?project_id=2&status=failed&page=1&limit=20");
  });

  it("FE4-F03: the 'active' filter", async () => {
    state.runs = [makeRun(5, { status: "waiting_for_approval" }), makeRun(4, { status: "succeeded" })];
    renderApp("/runs?status=active");
    await screen.findByText("#5");
    expect(within(screen.getByRole("table")).getByText("waiting_for_approval")).toHaveClass("badge-accent");
    expect(screen.queryByText("#4")).not.toBeInTheDocument();
  });

  it("FE4-F04: clicking a row opens the run page", async () => {
    state.runs = manyRuns(3);
    const user = userEvent.setup();
    renderApp("/runs");
    await user.click(await screen.findByText("#2"));
    expect(currentPath()).toBe("/runs/2");
  });

  it.each([["page=abc"], ["page=-1"], ["page=0"], ["status=foo"], ["project=x"], ["page=1.5"]])(
    "FE4-X07: %s falls back to the defaults", async (query) => {
      state.runs = manyRuns(5);
      renderApp(`/runs?${query}`);
      expect(await screen.findByText("#5")).toBeInTheDocument();
      expect(screen.getByText("20 per page · page 1 of 1")).toBeInTheDocument();
    });

  it("FE4-X07: page=9999 -> an empty page, no crash", async () => {
    state.runs = manyRuns(5);
    renderApp("/runs?page=9999");
    expect(await screen.findByText("There are no runs on this page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "show all runs" })).toHaveAttribute("href", "/runs");
  });

  it("FE4-X08: an empty history -> an empty state with a button to start a run", async () => {
    state.runs = [];
    renderApp("/runs");
    expect(await screen.findByText("No runs yet - start one from a project")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "start a run" })).toHaveAttribute("href", "/projects");
  });

  it("readFilters keeps only valid values", () => {
    const read = (q) => readFilters(new URLSearchParams(q));
    expect(read("page=3&project=2&status=active")).toEqual({ page: 3, projectId: 2, status: "active" });
    expect(read("page=abc&project=-4&status=<b>")).toEqual({ page: 1, projectId: null, status: null });
  });

  it("FE4-S01: a project name with HTML in the history is plain text", async () => {
    state.runs = [makeRun(1, { project_name: "<b>bold</b><script>window.hacked=7</script>" })];
    const view = renderApp("/runs");
    expect(await screen.findByText("<b>bold</b><script>window.hacked=7</script>")).toBeInTheDocument();
    expect(view.container.querySelector("tbody b, tbody script")).toBeNull();
  });
});
