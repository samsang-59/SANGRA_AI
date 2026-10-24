import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { resetPassword } from "../api/auth.js";
import { useAuth } from "../auth/useAuth.js";
import AuthLayout from "../components/AuthLayout.jsx";
import { PasswordField, SubmitButton } from "../components/Form.jsx";
import { NEW_PASSWORD_RULES, validate } from "../components/formUtils.js";
import { Notice } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { checkConfirm, checkNewPassword } from "../validation.js";

const TOKEN_ERRORS = ["TOKEN_INVALID_OR_EXPIRED"];

export default function ResetPasswordPage() {
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // The token is read once and then removed from the address bar (F1).
  const [token] = useState(() => new URLSearchParams(location.search).get("token"));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState(null);
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef(null);
  const confirmRef = useRef(null);

  useEffect(() => {
    if (location.search) navigate(location.pathname, { replace: true });
  }, [location.pathname, location.search, navigate]);

  const linkBad = !token || (serverError && (TOKEN_ERRORS.includes(serverError.code)
    || (serverError.code === "VALIDATION_ERROR" && /token/.test(serverError.message))));

  async function submit(event) {
    event.preventDefault();
    if (busy || !token) return;
    const found = validate({ password: checkNewPassword(password), confirm: checkConfirm(password, confirm) }, { password: passwordRef, confirm: confirmRef });
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setServerError(null);
    try {
      await resetPassword(token, password);
      auth.logout({ kind: "success", text: "Password changed, please log in" }); // the message for the login page
      navigate("/login", { replace: true });
    } catch (error) {
      setServerError(error);
      setBusy(false);
    }
  }

  return (
    <AuthLayout subtitle="Set a new password">
      <form className="card auth-card" onSubmit={submit} noValidate>
        {serverError && !linkBad && <Notice kind="error">{messageFor(serverError)}</Notice>}
        <PasswordField ref={passwordRef} label="New password" autoComplete="new-password" value={password}
          onChange={(e) => setPassword(e.target.value)} error={errors.password} rules={NEW_PASSWORD_RULES} />
        <PasswordField ref={confirmRef} label="Confirm new password" autoComplete="new-password" value={confirm}
          onChange={(e) => setConfirm(e.target.value)} error={errors.confirm} />
        <SubmitButton busy={busy} disabled={busy || !token} className="button-primary button-wide">save new password</SubmitButton>
      </form>
      {linkBad && (
        <Notice kind="error" action={<Link className="notice-link" to="/forgot-password">get a new link {"→"}</Link>}>
          This link is invalid or expired
        </Notice>
      )}
    </AuthLayout>
  );
}
