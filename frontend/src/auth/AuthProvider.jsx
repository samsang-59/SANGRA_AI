// The auth state (05_frontend_design.md F3): who is logged in. Token in localStorage (via api/token.js), the user
// from getMe(), and a status: loading | loggedIn | loggedOut | unreachable (the backend could not be reached at start).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as authApi from "../api/auth.js";
import { setUnauthorizedHandler } from "../api/client.js";
import { clearToken, getToken, setToken } from "../api/token.js";
import { AuthContext, SESSION_ENDED } from "./useAuth.js";

const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function AuthProvider({ children }) {
  const [state, setState] = useState(() => {
    const token = getToken();
    if (!token) return { status: "loggedOut", user: null };
    if (!JWT_SHAPE.test(token)) {
      clearToken(); // a corrupted value (FE2-X04)
      return { status: "loggedOut", user: null };
    }
    return { status: "loading", user: null };
  });
  // A message for the login page ("Session ended ...", "Password changed ..."), with its kind.
  const [notice, setNotice] = useState(null);
  const statusRef = useRef(state.status);
  useEffect(() => {
    statusRef.current = state.status;
  }, [state.status]);

  const loadUser = useCallback(async () => {
    setState({ status: "loading", user: null });
    try {
      const user = await authApi.getMe();
      setState({ status: "loggedIn", user });
    } catch (error) {
      if (error.status === 401) {
        clearToken();
        setState({ status: "loggedOut", user: null }); // an expired saved token: no message, no error screen
      } else {
        setState({ status: "unreachable", user: null, error }); // the token is kept (FE2-X02)
      }
    }
  }, []);

  const started = useRef(false);
  useEffect(() => {
    // Only once on start (React's development mode runs effects twice); later loads come from retry().
    if (started.current) return;
    started.current = true;
    if (statusRef.current === "loading") loadUser();
  }, [loadUser]);

  useEffect(() => {
    // The 401 rule (F2.4): a protected request answered 401, so the login is no longer valid.
    setUnauthorizedHandler(() => {
      if (statusRef.current !== "loggedIn") return;
      clearToken();
      setNotice({ kind: "info", text: SESSION_ENDED });
      setState({ status: "loggedOut", user: null });
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const loginWith = useCallback((data) => {
    setToken(data.token);
    setNotice(null);
    setState({ status: "loggedIn", user: data.user });
    return data.user;
  }, []);

  const value = useMemo(() => ({
    status: state.status,
    user: state.user,
    endedBy: state.endedBy,
    error: state.error,
    notice,
    clearNotice: () => setNotice(null),
    login: async (email, password) => loginWith(await authApi.login(email, password)),
    register: async (name, email, password) => loginWith(await authApi.register(name, email, password)),
    logout: (message = null) => {
      clearToken(); // JWTs are not stored by the backend, so it is not called
      setNotice(message);
      // F3: logout goes to plain /login; only a session that ended by itself remembers the page (the guard reads this)
      setState({ status: "loggedOut", user: null, endedBy: "logout" });
    },
    refreshUser: async () => {
      const user = await authApi.getMe();
      setState({ status: "loggedIn", user });
      return user;
    },
    retry: loadUser,
  }), [state, notice, loginWith, loadUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
