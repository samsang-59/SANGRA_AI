// The hint box (F6): the agent could not find the bug; the user gives a hint of 3-2000 characters ("hint 2 of 3").
import { useState } from "react";
import { sendHint } from "../api/runs.js";
import { messageFor } from "../errors.js";
import { Notice } from "./Status.jsx";

export const HINT_MIN = 3;
export const HINT_MAX = 2000;

export default function HintBox({ run, onSent, onStale }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const length = text.trim().length;
  const ok = length >= HINT_MIN && length <= HINT_MAX;

  async function submit(event) {
    event.preventDefault();
    if (busy || sent || !ok) return;
    setBusy(true);
    setError("");
    try {
      await sendHint(run.id, text.trim());
      setSent(true);
      onSent?.();
    } catch (problem) {
      if (problem.code === "RUN_STATE_INVALID") onStale?.(messageFor(problem)); // the page reloads the run
      else setError(messageFor(problem));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="approval">
      <div className="card info-bar mono">
        <span>hint <strong>{run.hint_count} of 3</strong></span>
        <span className="info-bar-note">{"⏱"} This run stops automatically after 1 hour of waiting</span>
      </div>
      <form className="card decide-card decide-card-accent" onSubmit={submit} aria-label="hint for the agent">
        <p className="explain-label mono">// agent</p>
        <p className="explain-text">
          I could not find the code for this bug. Please give a better hint {"—"} a file name, a function name, or
          a word that appears in the code.
        </p>
        <div className="field">
          <label htmlFor="hint-text">Your hint</label>
          <textarea id="hint-text" className={`input${length > HINT_MAX ? " input-error" : ""}`} rows={4} value={text}
            onChange={(e) => setText(e.target.value)} disabled={busy || sent} aria-describedby="hint-count"
            placeholder="e.g. look at refund_calculator.py, the partial refund function" />
          <p id="hint-count" className={`field-hint mono hint-count${length > HINT_MAX ? " text-danger" : ""}`}>
            {length} / {HINT_MAX} {"·"} min {HINT_MIN}
          </p>
        </div>
        {error && <Notice kind="error">{error}</Notice>}
        {sent && <p className="label-muted" role="status">Sent. The agent is searching again{"…"}</p>}
        <div>
          <button type="submit" className="button button-primary" disabled={busy || sent || !ok}>
            {busy ? "sending…" : "send hint"}
          </button>
        </div>
      </form>
    </div>
  );
}
