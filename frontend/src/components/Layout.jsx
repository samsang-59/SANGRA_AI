// The frame of the logged-in pages (05_frontend_design.md F5 "Layout", screen "Layout · top bar + banners"): the top
// bar and the verify banner. The "run in progress" banner joins in FE7 (it needs the run data of FE4).
import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { resendVerification } from "../api/auth.js";
import { useAuth } from "../auth/useAuth.js";
import { messageFor } from "../errors.js";
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

export default function Layout() {
  const { user } = useAuth();
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
      <Outlet />
    </div>
  );
}
