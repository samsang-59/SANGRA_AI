import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { verifyEmail } from "../api/auth.js";
import { useAuth } from "../auth/useAuth.js";
import AuthLayout from "../components/AuthLayout.jsx";
import { Spinner } from "../components/Status.jsx";
import { messageFor } from "../errors.js";

// The link token works only once, so the call is made exactly once, also when React's development mode runs the
// effect twice (Gaps #12, FE2-X13). The token is removed from the address bar at once (F1).
export default function VerifyEmailPage() {
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [state, setState] = useState({ phase: "checking" });
  const call = useRef(null);

  useEffect(() => {
    if (call.current) return;
    const token = new URLSearchParams(location.search).get("token");
    navigate(location.pathname, { replace: true }); // the token leaves the address bar and the history
    call.current = token
      ? verifyEmail(token).then(
        () => ({ phase: "success" }),
        (error) => ({ phase: "failed", error }),
      )
      : Promise.resolve({ phase: "failed", error: { code: "TOKEN_INVALID_OR_EXPIRED" } });
    call.current.then(async (result) => {
      if (result.phase === "success" && auth.status === "loggedIn") {
        try {
          await auth.refreshUser();
        } catch {
          // the banner updates on the next load
        }
      }
      setState(result);
    });
  }, [auth, location.pathname, location.search, navigate]);

  const loggedIn = auth.status === "loggedIn";
  return (
    <AuthLayout>
      {state.phase === "checking" && (
        <div className="card result-card">
          <Spinner label="Verifying" />
          <h1 className="result-title">Verifying your email...</h1>
        </div>
      )}
      {state.phase === "success" && (
        <div className="card result-card result-card-success">
          <span className="result-icon result-icon-success" aria-hidden="true">{"✓"}</span>
          <h1 className="result-title">Email verified</h1>
          <p className="result-text">You can now start runs.</p>
          <Link className="button button-primary" to={loggedIn ? "/projects" : "/login"}>
            {loggedIn ? "go to projects" : "log in"}
          </Link>
        </div>
      )}
      {state.phase === "failed" && (
        <div className="card result-card result-card-error" role="alert">
          <span className="result-icon result-icon-error" aria-hidden="true">{"✕"}</span>
          {state.error.code === "TOKEN_INVALID_OR_EXPIRED" || state.error.code === "VALIDATION_ERROR" ? (
            <>
              <h1 className="result-title">This link is invalid or expired</h1>
              <p className="result-text">
                {loggedIn ? "Send a new link from your account page." : "Log in, then send a new link from your account page."}
              </p>
            </>
          ) : (
            <>
              <h1 className="result-title">The email could not be verified</h1>
              <p className="result-text">{messageFor(state.error)}</p>
            </>
          )}
          <Link className="button" to={loggedIn ? "/account" : "/login"}>{loggedIn ? "go to account" : "log in"}</Link>
        </div>
      )}
    </AuthLayout>
  );
}
