// The result view of an ended run (05_frontend_design.md F6): the badge and the reason in plain words, the summary,
// "apply to my folder" (succeeded local-path runs not applied yet), "download package", "delete run", then the test
// panel and the attempts.
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { applyToSource, deleteRun, downloadPackage } from "../api/runs.js";
import { messageFor } from "../errors.js";
import { keys } from "../hooks/queries.js";
import { NO_TEST_COVERS, PACKAGE_EXPIRED_TEXT, canApply, splitSummary } from "../runResult.js";
import { stopReasonText } from "../runStatus.js";
import AttemptList from "./AttemptList.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { CheckIcon, DownloadIcon, WarningIcon } from "./icons.jsx";
import { Notice } from "./Status.jsx";
import StatusBadge from "./StatusBadge.jsx";
import TestResultPanel from "./TestResultPanel.jsx";

/** One request at a time per button, even on a double click (FE6-X06). */
function useOnce() {
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  async function run(work) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    try {
      await work();
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  return [busy, run];
}

export default function ResultView({ run }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [applying, applyOnce] = useOnce();
  const [downloading, downloadOnce] = useOnce();
  const [deleting, deleteOnce] = useOnce();
  const [appliedNow, setAppliedNow] = useState(false);
  const [expired, setExpired] = useState(false);
  const [asking, setAsking] = useState(false);
  const [messages, setMessages] = useState({}); // { apply, download, delete }: the message of the last failure
  const { text, warn } = splitSummary(run);
  const pack = run.package;
  const packageGone = expired || (pack && pack.available === false);
  const say = (key, value) => setMessages((previous) => ({ ...previous, [key]: value }));

  const apply = () => applyOnce(async () => {
    say("apply", "");
    try {
      const updated = await applyToSource(run.id);
      setAppliedNow(true);
      queryClient.setQueryData(keys.run(run.id), (old) => (old ? { ...old, ...updated } : old));
    } catch (problem) {
      say("apply", problem.code === "PACKAGE_EXPIRED" ? PACKAGE_EXPIRED_TEXT : messageFor(problem));
    }
  });

  const download = () => downloadOnce(async () => {
    say("download", "");
    try {
      await downloadPackage(run.id);
    } catch (problem) {
      if (problem.code === "PACKAGE_EXPIRED") setExpired(true);
      say("download", problem.code === "PACKAGE_EXPIRED" ? PACKAGE_EXPIRED_TEXT : messageFor(problem));
    }
  });

  const remove = () => deleteOnce(async () => {
    say("delete", "");
    try {
      await deleteRun(run.id);
      setAsking(false);
      queryClient.removeQueries({ queryKey: keys.run(run.id) });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: keys.activeRun });
      navigate("/runs");
    } catch (problem) {
      setAsking(false);
      say("delete", messageFor(problem));
    }
  });

  const retryDownload = messages.download && !packageGone
    ? <button type="button" className="notice-action" onClick={download}>retry</button> : null;
  return (
    <>
      <section className={`card run-result run-result-${run.status}`} aria-label="result">
        <div className="result-main">
          <div className="row run-title">
            <StatusBadge status={run.status} large />
            <h1>{run.status === "succeeded" ? "Tests passed" : stopReasonText(run.stop_reason)}</h1>
          </div>
          <p className={`result-summary${text ? "" : " text-muted"}`}>{text || "No summary available."}</p>
          {warn && (
            <div className="notice notice-warning result-warning" role="note">
              <span className="notice-svg" aria-hidden="true"><WarningIcon /></span>
              <span className="notice-text">{NO_TEST_COVERS}</span>
            </div>
          )}
        </div>
        <div className="result-actions">
          {canApply(run) && (
            <button type="button" className="button button-primary" onClick={apply} disabled={applying}>
              <CheckIcon /> {applying ? "applying…" : "apply to my folder"}
            </button>
          )}
          {appliedNow && (
            <button type="button" className="button" disabled><CheckIcon /> applied</button>
          )}
          <button type="button" className="button" onClick={download} disabled={downloading || !pack || packageGone}>
            <DownloadIcon /> {downloading ? "downloading…" : "download package"}
          </button>
          <p className="result-note">
            {!pack ? "No package available" : packageGone ? PACKAGE_EXPIRED_TEXT : "Package is kept for 7 days"}
          </p>
          <button type="button" className="button button-danger" onClick={() => setAsking(true)} disabled={deleting}>
            delete run
          </button>
        </div>
      </section>
      {(messages.apply || messages.download || messages.delete) && (
        <div className="result-messages">
          {messages.apply && <Notice kind="error">{messages.apply}</Notice>}
          {messages.download && <Notice kind="error" action={retryDownload}>{messages.download}</Notice>}
          {messages.delete && <Notice kind="error">{messages.delete}</Notice>}
        </div>
      )}
      <TestResultPanel run={run} />
      <AttemptList attempts={run.attempts} />
      {asking && (
        <ConfirmDialog title="Delete this run?" confirmLabel="delete run" busy={deleting} onConfirm={remove}
          onCancel={() => setAsking(false)}>
          The run, its attempts, test results and package are deleted. Your code is not changed.
        </ConfirmDialog>
      )}
    </>
  );
}
