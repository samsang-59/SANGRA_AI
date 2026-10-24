// The test panel of the result view (F6): the baseline result next to the after-fix result, so the human can judge.
// Missing results (a Docker error, a run that stopped before testing) show "not available" (FE6-X04).
import { pickResults } from "../runResult.js";

const TONE = { passed: "success", failed: "danger", error: "danger", timed_out: "danger" };

function seconds(ms) {
  return ms == null ? null : `${(ms / 1000).toFixed(1)}s`;
}

function Result({ title, result }) {
  if (!result) {
    return (
      <section className="card test-card" aria-label={title}>
        <header className="test-header"><span>{title}</span></header>
        <p className="test-missing">not available</p>
      </section>
    );
  }
  const meta = [["exit code", result.exit_code], ["duration", seconds(result.duration_ms)],
    ["image", result.docker_image]].filter(([, value]) => value != null && value !== "");
  return (
    <section className="card test-card" aria-label={title}>
      <header className="test-header">
        <span>{title}</span>
        <span className={`tag tag-${TONE[result.status] ?? "muted"}`}>{result.status}</span>
      </header>
      {meta.length > 0 && (
        <p className="test-meta">
          {meta.map(([label, value]) => <span key={label}>{label} <strong>{value}</strong></span>)}
        </p>
      )}
      {result.log_excerpt
        ? <pre className="test-log" tabIndex={0} aria-label={`${title}: test output`}>{result.log_excerpt}</pre>
        : <p className="test-missing">no test output</p>}
    </section>
  );
}

export default function TestResultPanel({ run }) {
  const { baseline, afterFix, attemptNumber } = pickResults(run);
  return (
    <section className="result-section" aria-labelledby="tests-title">
      <h2 id="tests-title">Test results</h2>
      <div className="test-grid">
        <Result title="baseline (before any change)" result={baseline} />
        <Result title={attemptNumber ? `after fix (attempt #${attemptNumber})` : "after fix"} result={afterFix} />
      </div>
    </section>
  );
}
