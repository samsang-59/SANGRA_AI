import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth.js";
import AuthLayout from "../components/AuthLayout.jsx";
import { Field, PasswordField, SubmitButton } from "../components/Form.jsx";
import { validate } from "../components/formUtils.js";
import { Notice } from "../components/Status.jsx";
import { checkEmail, checkLoginPassword, safeReturnPath } from "../validation.js";
import { messageFor } from "../errors.js";

export default function LoginPage() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const emailRef = useRef(null);
  const passwordRef = useRef(null);
  const returnTo = safeReturnPath(params.get("returnTo"));

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const found = validate({ email: checkEmail(email), password: checkLoginPassword(password) }, { email: emailRef, password: passwordRef });
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setServerError("");
    try {
      await auth.login(email.trim(), password); // the route guard then goes to returnTo or /projects
    } catch (error) {
      setServerError(messageFor(error));
      setBusy(false);
    }
  }

  return (
    <AuthLayout subtitle="Log in to your account"
      footer={<>No account? <Link to={returnTo ? `/register?returnTo=${encodeURIComponent(returnTo)}` : "/register"}>Create one</Link></>}>
      <form className="card auth-card" onSubmit={submit} noValidate>
        {auth.notice && !serverError && <Notice kind={auth.notice.kind}>{auth.notice.text}</Notice>}
        {serverError && <Notice kind="error">{serverError}</Notice>}
        <Field ref={emailRef} label="Email" type="email" autoComplete="email" value={email}
          onChange={(e) => setEmail(e.target.value)} error={errors.email} placeholder="you@example.com" />
        <PasswordField ref={passwordRef} label="Password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} error={errors.password}
          aside={<Link to="/forgot-password" className="field-aside">Forgot password?</Link>} />
        <SubmitButton busy={busy} className="button-primary button-wide">log in</SubmitButton>
      </form>
    </AuthLayout>
  );
}
