import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { forgotPassword } from "../api/auth.js";
import AuthLayout from "../components/AuthLayout.jsx";
import { Field, SubmitButton } from "../components/Form.jsx";
import { validate } from "../components/formUtils.js";
import { Notice } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { checkEmail } from "../validation.js";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const emailRef = useRef(null);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const found = validate({ email: checkEmail(email) }, { email: emailRef });
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setServerError("");
    try {
      await forgotPassword(email.trim());
      setSent(true); // the same answer for every email, known or not (FE2-F14)
    } catch (error) {
      setServerError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <AuthLayout>
        <div className="card result-card" role="status">
          <span className="result-icon result-icon-accent" aria-hidden="true">{"✉"}</span>
          <h1 className="result-title">Check your email</h1>
          <p className="result-text">If this email exists, we sent a link</p>
          <Link to="/login">{"←"} back to log in</Link>
        </div>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout>
      <form className="card auth-card" onSubmit={submit} noValidate>
        <div>
          <h1 className="card-title">Forgot password</h1>
          <p className="card-text">Enter your email and we will send you a reset link.</p>
        </div>
        {serverError && <Notice kind="error">{serverError}</Notice>}
        <Field ref={emailRef} label="Email" type="email" autoComplete="email" value={email}
          onChange={(e) => setEmail(e.target.value)} error={errors.email} placeholder="you@example.com" />
        <SubmitButton busy={busy} className="button-primary button-wide">send reset link</SubmitButton>
        <p className="card-center"><Link to="/login">{"←"} back to log in</Link></p>
      </form>
    </AuthLayout>
  );
}
