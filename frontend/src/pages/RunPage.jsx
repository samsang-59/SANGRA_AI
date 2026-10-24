// The Run page (05_frontend_design.md F6), /runs/:runId. It works from two sources: the run record (useRun) and the
// live events (useRunStream). Active runs show the live view; ended runs show the result view.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { cancelRun } from "../api/runs.js";
import ApprovalBox from "../components/ApprovalBox.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import EventTimeline from "../components/EventTimeline.jsx";
import HintBox from "../components/HintBox.jsx";
import ResultView from "../components/ResultView.jsx";
import { Notice, Spinner } from "../components/Status.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import { messageFor } from "../errors.js";
import { keys, useProject, useRun } from "../hooks/queries.js";
import { useRunStream } from "../hooks/useRunStream.js";
import { ACTIVE_STATUSES, STATUS_LABELS, formatDate } from "../runStatus.js";
import NotFoundPage from "./NotFoundPage.jsx";

const WAITING = ["waiting_for_approval", "waiting_for_hint"];

function Connection({ state, onRetry }) {
  if (state === "live") return <span className="log-state text-success mono"><span className="dot" aria-hidden="true" /> live</span>;
  if (state === "reconnecting" || state === "connecting") {
    return (
      <span className="log-state text-accent mono" role="status">
        <span className="spinner spinner-inline" aria-hidden="true" />
        {state === "reconnecting" ? "Reconnecting…" : "Connecting…"}
      </span>
    );
  }
  if (state === "lost") {
    return (
      <span className="log-state text-danger mono" role="alert">
        Lost connection
        <button type="button" className="button button-small" onClick={onRetry}>retry</button>
      </span>
    );
  }
  return null;
}

/** The attempt the approval box answers: the one waiting, else the newest. */
function waitingAttempt(run) {
  const attempts = run.attempts ?? [];
  return attempts.find((a) => a.status === "waiting_for_approval") ?? attempts[attempts.length - 1] ?? null;
}

function LiveView({ run, onStale }) {
  const { events, connection, retry } = useRunStream(run.id);
  const waiting = WAITING.includes(run.status);
  const attempt = run.status === "waiting_for_approval" ? waitingAttempt(run) : null;
  const log = (
    <EventTimeline events={events}
      header={<><span>agent log</span><Connection state={connection} onRetry={retry} /></>}
      footer={waiting
        ? <span className="text-accent">waiting for you <span className="cursor" aria-hidden="true" /></span>
        : <span className="text-info">agent is working <span className="cursor" aria-hidden="true" /></span>} />
  );
  if (!waiting) {
    return (
      <>
        {log}
        <p className="label-muted">Leaving this page does not stop the run. Only {"“"}cancel run{"”"} does.</p>
      </>
    );
  }
  return (
    <div className="run-columns">
      <div className="run-main">
        {run.status === "waiting_for_hint" && <HintBox key={`hint-${run.hint_count}`} run={run} onStale={onStale} />}
        {attempt && <ApprovalBox key={`attempt-${attempt.id}`} run={run} attempt={attempt} onStale={onStale} />}
        {run.status === "waiting_for_approval" && !attempt && <Spinner label="Loading the proposed fix" />}
      </div>
      <div className="run-side">{log}</div>
    </div>
  );
}

function CancelButton({ run }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      await cancelRun(run.id);
      setAsking(false);
    } catch (problem) {
      setAsking(false);
      setError(messageFor(problem));
    } finally {
      setBusy(false);
      queryClient.invalidateQueries({ queryKey: keys.run(run.id) });
      queryClient.invalidateQueries({ queryKey: keys.activeRun });
    }
  }

  return (
    <div className="run-cancel">
      <button type="button" className="button button-danger" onClick={() => setAsking(true)}>cancel run</button>
      {error && <Notice kind="error">{error}</Notice>}
      {asking && (
        <ConfirmDialog title="Cancel this run?" confirmLabel="cancel run" cancelLabel="keep running"
          busyText="cancelling…" busy={busy} onConfirm={confirm} onCancel={() => setAsking(false)}>
          The agent stops and the copy is cleaned up. Your code is not changed.
        </ConfirmDialog>
      )}
    </div>
  );
}

export default function RunPage() {
  const { runId } = useParams();
  const runQuery = useRun(runId);
  const run = runQuery.data;
  const project = useProject(run ? run.project_id : null);
  const queryClient = useQueryClient();
  const [stale, setStale] = useState({ runId, message: "" }); // an answer that came too late (FE5-X03)
  const staleMessage = stale.runId === runId ? stale.message : "";
  const onStale = (message) => {
    setStale({ runId, message });
    queryClient.invalidateQueries({ queryKey: keys.run(runId) });
  };

  if (runQuery.isError && runQuery.error.code === "NOT_FOUND") return <NotFoundPage />; // FE5-X10
  if (runQuery.isPending) {
    return <main className="page"><div className="center-block"><Spinner label="Loading the run" /></div></main>;
  }
  if (!run) {
    return (
      <main className="page">
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={() => runQuery.refetch()}>retry</button>}>
          {messageFor(runQuery.error)}
        </Notice>
      </main>
    );
  }
  const active = ACTIVE_STATUSES.includes(run.status);
  return (
    <main className="page">
      <div className="page-header">
        <div className="run-heading">
          <p className="breadcrumb mono run-breadcrumb">
            <Link to="/runs">history</Link> / <span className="text-strong">run #{run.id}</span>
            <span className="breadcrumb-sep" aria-hidden="true">|</span>
            <span>{project.data?.name ?? ""}</span>
            <span className="tag">{run.language}</span>
            <span className="tag">{run.source_type}</span>
            {!active && <><span className="breadcrumb-sep" aria-hidden="true">|</span>{formatDate(run.created_at)}</>}
          </p>
          {active && (
            <div className="row run-title">
              <h1>{STATUS_LABELS[run.status]}</h1>
              <StatusBadge status={run.status} />
            </div>
          )}
          <p className="run-bug"><span className="mono text-accent">bug {"›"}</span> {run.bug_description}</p>
        </div>
        {active && <CancelButton run={run} />}
      </div>
      {/* A status change is announced to screen readers (FE5-U01). */}
      <p className="visually-hidden" aria-live="polite">Status: {run.status}. {STATUS_LABELS[run.status] ?? ""}</p>
      {runQuery.isError && <Notice kind="error">{messageFor(runQuery.error)}</Notice>}
      {staleMessage && <Notice kind="warning">{staleMessage}</Notice>}
      {active ? <LiveView key={run.id} run={run} onStale={onStale} /> : <ResultView key={run.id} run={run} />}
    </main>
  );
}
