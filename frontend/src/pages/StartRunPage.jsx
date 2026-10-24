import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { startRun } from "../api/runs.js";
import { useAuth } from "../auth/useAuth.js";
import { SubmitButton } from "../components/Form.jsx";
import { Notice, Spinner } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { keys, useActiveRun, useProject } from "../hooks/queries.js";
import NotFoundPage from "./NotFoundPage.jsx";
import { BUG_MAX, BUG_MIN, VERIFY_REASON, checkZip, megabytes } from "../runRules.js";

function ZipPicker({ file, error, onFile }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);
  const choose = (files) => files && files[0] && onFile(files[0]);

  return (
    <div className="field">
      <span className="field-label">Code (.zip) <span className="label-muted">{"—"} only for zip projects</span></span>
      <div className={`dropzone${dragging ? " dropzone-active" : ""}${error ? " dropzone-error" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files); }}>
        <span className="dropzone-icon" aria-hidden="true">{"⤒"}</span>
        <p className="dropzone-text">
          Drop your .zip here or{" "}
          <button type="button" className="link-button" onClick={() => input.current?.click()}>browse</button>
        </p>
        <p className="dropzone-hint mono">.zip only {"·"} max 50 MB</p>
        <input ref={input} type="file" accept=".zip,application/zip" className="visually-hidden"
          aria-label="Code (.zip)" onChange={(e) => { choose(e.target.files); e.target.value = ""; }} />
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
      {file && !error && (
        <div className="file-row">
          <span className="mono"><strong>{file.name}</strong> <span className="label-muted">{"·"} {megabytes(file.size)}</span></span>
          <button type="button" className="button button-small" onClick={() => onFile(null)}>remove</button>
        </div>
      )}
    </div>
  );
}

function StartRunForm({ project }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const activeRun = useActiveRun();
  const [bug, setBug] = useState("");
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const upload = useRef(null);
  const isZip = project.source_type === "zip";
  const length = bug.trim().length;
  const bugOk = length >= BUG_MIN && length <= BUG_MAX;
  const verified = Boolean(user?.is_email_verified);

  // Leaving the page during the upload stops it (FE4-X06).
  useEffect(() => () => upload.current?.abort(), []);

  function pickFile(next) {
    setFile(next);
    setFileError(next ? checkZip(next) : "");
  }

  async function submit(event) {
    event.preventDefault();
    if (busy || !bugOk || !verified) return;
    if (isZip) {
      const zipProblem = checkZip(file);
      if (zipProblem) {
        setFileError(zipProblem);
        return;
      }
    }
    setBusy(true);
    setProblem(null);
    upload.current = new AbortController();
    try {
      const run = await startRun(project.id, bug.trim(), isZip ? file : null, { signal: upload.current.signal });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: keys.activeRun });
      navigate(`/runs/${run.id}`);
    } catch (error) {
      if (error.code === "ABORTED") return;
      if (error.code === "ACTIVE_RUN_EXISTS") queryClient.invalidateQueries({ queryKey: keys.activeRun });
      setProblem(error);
      setBusy(false);
    }
  }

  const activeLink = activeRun.data ? `/runs/${activeRun.data.id}` : "/runs?status=active";
  return (
    <form className="card section-card" onSubmit={submit} noValidate>
      <div className="field">
        <label htmlFor="bug-description">Describe the bug in plain English</label>
        <textarea id="bug-description" className={`input${length > BUG_MAX ? " input-error" : ""}`} rows={7}
          value={bug} onChange={(e) => setBug(e.target.value)} aria-describedby="bug-count"
          placeholder="The summer sale shows 5% discount, it should be 10%." />
        <div className="row-between field-hint">
          <span>One bug per run</span>
          <span id="bug-count" className={`mono${length > BUG_MAX ? " text-danger" : ""}`} aria-live="polite">
            {length} / {BUG_MAX} {"·"} min {BUG_MIN}
          </span>
        </div>
      </div>
      {isZip && <ZipPicker file={file} error={fileError} onFile={pickFile} />}
      {!verified && <Notice kind="info">{VERIFY_REASON}</Notice>}
      {problem?.code === "ACTIVE_RUN_EXISTS" && (
        <Notice kind="warning" action={<Link className="notice-link" to={activeLink}>open it {"→"}</Link>}>
          You already have a run in progress
        </Notice>
      )}
      {problem && problem.code !== "ACTIVE_RUN_EXISTS" && (
        <Notice kind="error">
          {problem.code === "EMAIL_NOT_VERIFIED" ? VERIFY_REASON : messageFor(problem)}
        </Notice>
      )}
      <div className="form-footer">
        <Link className="button" to="/projects">cancel</Link>
        <SubmitButton busy={busy} disabled={busy || !bugOk || !verified || (isZip && (!file || Boolean(fileError)))}
          busyText={isZip ? "uploading…" : "starting…"}>
          {"▶"} start run
        </SubmitButton>
      </div>
    </form>
  );
}

export default function StartRunPage() {
  const { projectId } = useParams();
  const project = useProject(projectId);
  if (project.isError && project.error.code === "NOT_FOUND") return <NotFoundPage />;
  return (
    <main className="page page-narrow">
      <div>
        <p className="breadcrumb mono">
          <Link to="/projects">projects</Link> / {project.data?.name ?? "…"} / run
        </p>
        <h1>Start a run</h1>
      </div>
      {project.isPending && <div className="center-block"><Spinner label="Loading the project" /></div>}
      {project.isError && (
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={() => project.refetch()}>retry</button>}>
          {messageFor(project.error)}
        </Notice>
      )}
      {project.isSuccess && (
        <>
          <div className="row tags-row">
            <span className="tag">{project.data.name}</span>
            <span className="tag">{project.data.language}</span>
            <span className="tag tag-info">{project.data.source_type}</span>
            <span className="tag">$ {project.data.test_command}</span>
          </div>
          <StartRunForm project={project.data} />
        </>
      )}
    </main>
  );
}
