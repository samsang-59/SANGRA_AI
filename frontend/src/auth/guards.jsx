// Route guards (F3): ProtectedRoute for pages that need login, PublicOnlyRoute for login, register, forgot, reset.
import { Navigate, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { safeReturnPath } from "../validation.js";
import { useAuth } from "./useAuth.js";
import { FullPageSpinner, ServerUnreachable } from "../components/Status.jsx";

function Waiting({ auth }) {
  if (auth.status === "unreachable") return <ServerUnreachable onRetry={auth.retry} />;
  return <FullPageSpinner label="Loading your account" />;
}

export function ProtectedRoute() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.status === "loading" || auth.status === "unreachable") return <Waiting auth={auth} />;
  if (auth.status === "loggedOut") {
    if (auth.endedBy === "logout") return <Navigate to="/login" replace />;
    const wanted = location.pathname + location.search;
    return <Navigate to={`/login?returnTo=${encodeURIComponent(wanted)}`} replace />;
  }
  return <Outlet />;
}

export function PublicOnlyRoute() {
  const auth = useAuth();
  const [params] = useSearchParams();
  if (auth.status === "loading" || auth.status === "unreachable") return <Waiting auth={auth} />;
  // Logged in (also right after login or register): back to the page the user wanted, else the projects.
  if (auth.status === "loggedIn") return <Navigate to={safeReturnPath(params.get("returnTo")) || "/projects"} replace />;
  return <Outlet />;
}

/** /verify-email works for both (F3); it only waits for the start-up check. */
export function AnyAuthRoute() {
  const auth = useAuth();
  if (auth.status === "loading" || auth.status === "unreachable") return <Waiting auth={auth} />;
  return <Outlet />;
}
