// The attempts of an ended run (F6), newest first: number, why it started, the decision, whether it was undone, its
// status, the explanation, and every edit as old and new code.
import CodeDiff from "./CodeDiff.jsx";

const TONE = { tests_passed: "success", tests_failed: "danger", rejected: "danger" };

function Attempt({ attempt }) {
  const edits = [...(attempt.edits ?? [])].sort((a, b) => a.edit_order - b.edit_order);
  return (
    <article className="card attempt-card" aria-label={`attempt #${attempt.attempt_number}`}>
      <header className="attempt-header">
        <span className="mono">
          <strong>attempt #{attempt.attempt_number}</strong> <span className="text-muted">{attempt.reason_started}</span>
          {attempt.decision && <span className="text-muted"> {"·"} decision: {attempt.decision}</span>}
          {attempt.reverted_at && <span className="text-muted"> {"·"} reverted</span>}
        </span>
        <span className={`tag tag-${TONE[attempt.status] ?? "muted"}`}>{attempt.status}</span>
      </header>
      {attempt.explanation && <p className="attempt-text">{attempt.explanation}</p>}
      {attempt.decision_feedback && (
        <p className="attempt-feedback"><span className="mono text-muted">your feedback {"›"}</span> {attempt.decision_feedback}</p>
      )}
      {edits.map((edit) => <CodeDiff key={edit.id ?? edit.edit_order} edit={edit} showReason={false} />)}
      {edits.length === 0 && <p className="test-missing">no edits</p>}
    </article>
  );
}

export default function AttemptList({ attempts }) {
  const newestFirst = [...(attempts ?? [])].sort((a, b) => b.attempt_number - a.attempt_number);
  return (
    <section className="result-section" aria-labelledby="attempts-title">
      <h2 id="attempts-title">Attempts</h2>
      {newestFirst.length === 0
        ? <p className="test-missing">No fix was proposed in this run.</p>
        : newestFirst.map((attempt) => <Attempt key={attempt.id} attempt={attempt} />)}
    </section>
  );
}
