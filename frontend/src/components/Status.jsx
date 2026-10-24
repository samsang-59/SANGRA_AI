// Shared small pieces (F4, foundations sheet): Spinner, Notice (error / info / success / warning), full-page states.
import Brand from "./Brand.jsx";

export function Spinner({ size = 32, label = "Loading" }) {
  return <span className="spinner" style={{ width: size, height: size }} role="status" aria-label={label} />;
}

export function FullPageSpinner({ label }) {
  return (
    <main className="center-page">
      <Spinner label={label} />
    </main>
  );
}

const ICONS = { error: "!", info: "i", success: "✓", warning: "!" };

/** A message box. kind: error | info | success | warning. The text is always plain text, never HTML (FE2-S11). */
export function Notice({ kind = "error", children, action = null, id }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"} id={id}>
      <span className={`notice-icon notice-icon-${kind}`} aria-hidden="true">{ICONS[kind]}</span>
      <span className="notice-text">{children}</span>
      {action}
    </div>
  );
}

/** The backend could not be reached when the app opened (FE2-X02): the token is kept, Retry tries again. */
export function ServerUnreachable({ onRetry }) {
  return (
    <main className="center-page">
      <div className="auth-column">
        <div className="auth-heading"><Brand size={26} /></div>
        <Notice kind="error" action={<button type="button" className="notice-action" onClick={onRetry}>retry</button>}>
          Cannot reach the server
        </Notice>
      </div>
    </main>
  );
}
