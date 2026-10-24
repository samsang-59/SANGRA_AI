// The frame of the logged-in pages (05_frontend_design.md F5 "Layout", F6 "Active-run banner", screen "Layout · top
// bar + banners"): the top bar, the verify banner and the "run in progress" banner (["activeRun"], every 30 s).
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { resendVerification } from "../api/auth.js";
import { useAuth } from "../auth/useAuth.js";
import { messageFor } from "../errors.js";
import { useActiveRun } from "../hooks/queries.js";
import ErrorBoundary from "./ErrorBoundary.jsx";
import Brand from "./Brand.jsx";
import { MailIcon, UserIcon } from "./icons.jsx";

export function VerifyBanner() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function resend() {
    if (busy) return;
    setBusy(true);
    try {
      const data = await resendVerification();
      setResult({ ok: true, text: data?.message || "We sent a new verification link." });
    } catch (error) {
      setResult({ ok: false, text: messageFor(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="banner banner-info" role="region" aria-label="Email not verified">
      <MailIcon />
      <span className="banner-text">Verify your email to start runs</span>
      {result && (
        <span className={`banner-result ${result.ok ? "text-success" : "text-danger"}`} role="status">{result.text}</span>
      )}
      <button type="button" className="button button-small button-info" onClick={resend} disabled={busy}>
        {busy ? "sending…" : "resend"}
      </button>
    </div>
  );
}

/** Only one run can be active per user, so one link is enough. */
export function ActiveRunBanner() {
  const activeRun = useActiveRun();
  if (!activeRun.data) return null;
  return (
    <div className="banner banner-accent" role="region" aria-label="Run in progress">
      <span className="dot" aria-hidden="true" />
      <span>A run is in progress</span>
      <Link className="banner-link mono" to={`/runs/${activeRun.data.id}`}>open it {"→"}</Link>
    </div>
  );
}

export default function Layout() {
  const { user } = useAuth();
  const location = useLocation();
  return (
    <div className="app">
      <header className="top-bar">
        <Link to="/projects" className="top-bar-brand"><Brand size={20} /></Link>
        <nav className="top-bar-nav" aria-label="Main">
          <NavLink to="/projects" className="nav-link">projects</NavLink>
          <NavLink to="/runs" className="nav-link">history</NavLink>
        </nav>
        <NavLink to="/account" className="nav-link nav-account"><UserIcon /> account</NavLink>
      </header>
      {user && !user.is_email_verified && <VerifyBanner />}
      <ActiveRunBanner />
      {/* A crash in one page shows the error screen, not a white page; another address starts fresh (FE7-F02). */}
      <ErrorBoundary resetKey={location.pathname}>
        <Outlet />
      </ErrorBoundary>
    </div>
  );
}
