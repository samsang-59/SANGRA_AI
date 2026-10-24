// The approval box (F6): the agent's explanation, every edit as old and new code, then Accept or Reject.
// Reject needs feedback of 3-2000 characters. The attempt shown is the one answered (its id is sent).
import { useState } from "react";
import { submitApproval } from "../api/runs.js";
import { messageFor } from "../errors.js";
import CodeDiff from "./CodeDiff.jsx";
import { Notice } from "./Status.jsx";

export const FEEDBACK_MIN = 3;
export const FEEDBACK_MAX = 2000;

export default function ApprovalBox({ run, attempt, onSent, onStale }) {
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(null); // "accepted" | "rejected" while sending
  const [sent, setSent] = useState(false); // stays until the next status change replaces this box (FE5-F04)
  const [error, setError] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  const length = feedback.trim().length;
  const edits = [...(attempt.edits ?? [])].sort((a, b) => a.edit_order - b.edit_order);
  const files = new Set(edits.map((e) => e.file_path)).size;

  async function decide(decision) {
    if (busy || sent) return;
    if (decision === "rejected" && (length < FEEDBACK_MIN || length > FEEDBACK_MAX)) {
      setFeedbackError(`To reject, tell the agent what is wrong (${FEEDBACK_MIN}-${FEEDBACK_MAX} characters).`);
      return;
    }
    setFeedbackError("");
    setError("");
    setBusy(decision);
    try {
      await submitApproval(run.id, attempt.id, decision, decision === "rejected" ? feedback.trim() : undefined);
      setSent(true);
      onSent?.();
    } catch (problem) {
      // The proposal is no longer open (old tab, double click, cancelled meanwhile): the page shows the message and
      // reloads the run to show the real state (FE5-X03). Other errors stay here and the buttons work again (X12).
      if (problem.code === "STALE_ATTEMPT" || problem.code === "RUN_STATE_INVALID") onStale?.(messageFor(problem));
      else setError(messageFor(problem));
    } finally {
      setBusy(null);
    }
  }

  const locked = Boolean(busy) || sent;
  return (
    <div className="approval">
      <div className="card info-bar mono">
        <span>attempt <strong>#{attempt.attempt_number}</strong> {"·"} {attempt.reason_started}</span>
        <span>rejected <strong>{run.reject_count} of 3</strong></span>
        <span>edits <strong>{edits.length}</strong> in <strong>{files}</strong> {files === 1 ? "file" : "files"}</span>
        <span className="info-bar-note">{"⏱"} This run stops automatically after 1 hour of waiting</span>
      </div>
      <section className="card explain-card">
        <p className="explain-label mono">// agent explanation</p>
        <p className="explain-text">{attempt.explanation}</p>
      </section>
      {edits.map((edit) => <CodeDiff key={edit.id ?? edit.edit_order} edit={edit} />)}
      <section className="card decide-card" aria-label="your decision">
        <p className="decide-question mono">
          Apply {edits.length === 1 ? "this edit" : `these ${edits.length} edits`} to the copy and run the tests?
        </p>
        <div className="field">
          <label htmlFor="approval-feedback">
            Feedback <span className="label-muted">(required to reject {"·"} {FEEDBACK_MIN}{"–"}{FEEDBACK_MAX} characters)</span>
          </label>
          <textarea id="approval-feedback" className={`input${feedbackError ? " input-error" : ""}`} rows={3}
            value={feedback} onChange={(e) => setFeedback(e.target.value)} disabled={locked}
            placeholder={"Tell the agent what is wrong with this fix…"}
            aria-invalid={feedbackError ? "true" : undefined}
            aria-describedby={feedbackError ? "approval-feedback-error" : undefined} />
          {feedbackError && <p id="approval-feedback-error" className="field-error" role="alert">{feedbackError}</p>}
        </div>
        {error && <Notice kind="error">{error}</Notice>}
        {sent && <p className="label-muted" role="status">Sent. Waiting for the agent{"…"}</p>}
        <div className="row">
          <button type="button" className="button button-primary" onClick={() => decide("accepted")} disabled={locked}>
            {busy === "accepted" ? "sending…" : "accept & test"}
          </button>
          <button type="button" className="button" onClick={() => decide("rejected")} disabled={locked}>
            {busy === "rejected" ? "sending…" : "reject"}
          </button>
        </div>
      </section>
    </div>
  );
}
