import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { createProject, updateProject } from "../api/projects.js";
import { Field, SubmitButton } from "../components/Form.jsx";
import { validate } from "../components/formUtils.js";
import { Notice, Spinner } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { keys, useProject } from "../hooks/queries.js";
import NotFoundPage from "./NotFoundPage.jsx";
import {
  checkBranch, checkGithubUrl, checkLocalPath, checkProjectName, checkTestCommand,
} from "../validation.js";

const SOURCE_HELP = {
  github: "The agent clones the repo for each run.",
  local_path: "The agent copies this folder for each run (only when the backend runs on your own machine).",
  zip: "You upload a .zip of the code with each run.",
};

function Choice({ label, name, options, value, onChange }) {
  return (
    <fieldset className="field choice">
      <legend>{label}</legend>
      <div className="choice-options">
        {options.map((option) => (
          <label key={option} className={`choice-option${value === option ? " choice-selected" : ""}`}>
            <input type="radio" name={name} value={option} checked={value === option}
              onChange={() => onChange(option)} />
            {option}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

const EMPTY = { name: "", language: "java", source_type: "github", source_reference: "", branch: "", test_command: "" };

function fromProject(project) {
  return { name: project.name, language: project.language, source_type: project.source_type,
    source_reference: project.source_reference ?? "", branch: project.branch ?? "", test_command: project.test_command };
}

/** The body to send: trimmed values; the reference only for github and local_path; the branch only for github. */
function body(form) {
  const result = { name: form.name.trim(), language: form.language, source_type: form.source_type,
    test_command: form.test_command.trim() };
  if (form.source_type !== "zip") result.source_reference = form.source_reference.trim();
  if (form.source_type === "github" && form.branch.trim()) result.branch = form.branch.trim();
  return result;
}

/** Edit sends only what changed (FE3-F03). A cleared branch is sent as null; switching to zip clears the reference. */
function changes(original, form) {
  const before = body(original);
  const after = body(form);
  const changed = {};
  for (const field of ["name", "language", "source_type", "test_command", "source_reference", "branch"]) {
    const a = before[field] ?? null;
    const b = after[field] ?? null;
    if (a !== b) changed[field] = b;
  }
  return changed;
}

function ProjectForm({ project }) {
  const editing = Boolean(project);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(() => (project ? fromProject(project) : EMPTY));
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const nameRef = useRef(null);
  const referenceRef = useRef(null);
  const branchRef = useRef(null);
  const commandRef = useRef(null);
  const set = (field) => (value) => setForm((f) => ({ ...f, [field]: value }));
  const setFromInput = (field) => (event) => set(field)(event.target.value);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const checks = { name: checkProjectName(form.name) };
    if (form.source_type === "github") checks.source_reference = checkGithubUrl(form.source_reference);
    if (form.source_type === "local_path") checks.source_reference = checkLocalPath(form.source_reference);
    if (form.source_type === "github") checks.branch = checkBranch(form.branch);
    checks.test_command = checkTestCommand(form.test_command);
    const found = validate(checks, { name: nameRef, source_reference: referenceRef, branch: branchRef,
      test_command: commandRef });
    setErrors(found);
    if (Object.keys(found).length) return;

    const payload = editing ? changes(fromProject(project), form) : body(form);
    if (editing && Object.keys(payload).length === 0) {
      navigate("/projects");
      return;
    }
    setBusy(true);
    setServerError("");
    try {
      if (editing) await updateProject(project.id, payload);
      else await createProject(payload);
      await queryClient.invalidateQueries({ queryKey: keys.projects });
      if (editing) queryClient.removeQueries({ queryKey: keys.project(project.id) });
      navigate("/projects");
    } catch (error) {
      if (editing && error.code === "NOT_FOUND") {
        // Deleted in another tab (FE3-X04): back to the list with a message.
        await queryClient.invalidateQueries({ queryKey: keys.projects });
        navigate("/projects", { state: { notice: "This project no longer exists (it was deleted)." } });
        return;
      }
      setServerError(messageFor(error)); // 422 names the field; the 409/400 codes say what to change
      setBusy(false);
    }
  }

  const referenceLabel = form.source_type === "github" ? "GitHub URL" : "Folder path";
  const referenceHint = form.source_type === "github" ? "Public repos only"
    : "An absolute path, for example C:\\code\\shop or /home/me/shop";

  return (
    <form className="card section-card" onSubmit={submit} noValidate>
      {serverError && <Notice kind="error">{serverError}</Notice>}
      <Field ref={nameRef} label="Name" value={form.name} onChange={setFromInput("name")} error={errors.name}
        hint={"1\u2013100 characters"} autoComplete="off" />
      <Choice label="Language" name="language" options={["java", "python"]} value={form.language}
        onChange={set("language")} />
      <div className="field">
        <Choice label="Source type" name="source_type" options={["github", "local_path", "zip"]} value={form.source_type}
          onChange={set("source_type")} />
        <p className="field-hint">{SOURCE_HELP[form.source_type]}</p>
      </div>
      {form.source_type !== "zip" && (
        <Field ref={referenceRef} label={referenceLabel} value={form.source_reference}
          onChange={setFromInput("source_reference")} error={errors.source_reference} hint={referenceHint}
          mono spellCheck="false" autoComplete="off"
          placeholder={form.source_type === "github" ? "https://github.com/owner/repo" : "/home/me/shop"} />
      )}
      {form.source_type === "github" && (
        <Field ref={branchRef} label={<>Branch <span className="label-muted">(optional)</span></>} value={form.branch}
          onChange={setFromInput("branch")} error={errors.branch} mono spellCheck="false" autoComplete="off"
          placeholder={"empty = the repo\u2019s default branch"} />
      )}
      <Field ref={commandRef} label="Test command" value={form.test_command} onChange={setFromInput("test_command")}
        error={errors.test_command} mono hint={"1\u2013500 characters \u00b7 runs inside Docker \u00b7 success = exit code 0"}
        spellCheck="false" autoComplete="off" placeholder={form.language === "java" ? "mvn test" : "pytest"} />
      <div className="form-footer">
        <Link className="button" to="/projects">cancel</Link>
        <SubmitButton busy={busy}>{editing ? "save changes" : "create project"}</SubmitButton>
      </div>
    </form>
  );
}

export default function ProjectFormPage() {
  const { projectId } = useParams();
  const editing = projectId !== undefined;
  const project = useProject(editing ? projectId : null);

  if (editing && project.isError && project.error.code === "NOT_FOUND") return <NotFoundPage />;
  return (
    <main className="page page-narrow">
      <div>
        <p className="breadcrumb mono">
          <Link to="/projects">projects</Link> / {editing ? (project.data ? project.data.name : "\u2026") : "new"}
          {editing && " / edit"}
        </p>
        <h1>{editing ? "Edit project" : "New project"}</h1>
      </div>
      {editing && project.isPending && <div className="center-block"><Spinner label="Loading the project" /></div>}
      {editing && project.isError && (
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={() => project.refetch()}>retry</button>}>
          {messageFor(project.error)}
        </Notice>
      )}
      {(!editing || project.isSuccess) && <ProjectForm key={projectId ?? "new"} project={editing ? project.data : null} />}
    </main>
  );
}
