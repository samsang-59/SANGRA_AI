// The auth context and its hook (the provider is in AuthProvider.jsx).
import { createContext, useContext } from "react";

export const SESSION_ENDED = "Session ended, please log in again";
export const PASSWORD_CHANGED = "Password changed, please log in again";

export const AuthContext = createContext(null);

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
