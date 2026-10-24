import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/useAuth.js";
import AuthLayout from "../components/AuthLayout.jsx";
import { Field, PasswordField, SubmitButton } from "../components/Form.jsx";
import { NEW_PASSWORD_RULES, validate } from "../components/formUtils.js";
import { Notice } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { checkEmail, checkName, checkNewPassword, safeReturnPath } from "../validation.js";

export default function RegisterPage() {
  const auth = useAuth();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const nameRef = useRef(null);
  const emailRef = useRef(null);
  const passwordRef = useRef(null);
  const returnTo = safeReturnPath(params.get("returnTo"));
  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const found = validate({ name: checkName(form.name), email: checkEmail(form.email),
      password: checkNewPassword(form.password) }, { name: nameRef, email: emailRef, password: passwordRef });
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setServerError("");
    try {
      await auth.register(form.name.trim(), form.email.trim(), form.password); // logged in at once (F3)
    } catch (error) {
      setServerError(messageFor(error));
      setBusy(false);
    }
  }

  return (
    <AuthLayout subtitle="Create your account"
      footer={<>Already have an account? <Link to={returnTo ? `/login?returnTo=${encodeURIComponent(returnTo)}` : "/login"}>Log in</Link></>}>
      <form className="card auth-card" onSubmit={submit} noValidate>
        {serverError && <Notice kind="error">{serverError}</Notice>}
        <Field ref={nameRef} label="Name" autoComplete="name" value={form.name} onChange={set("name")}
          error={errors.name} hint="1–100 characters" placeholder="Your name" />
        <Field ref={emailRef} label="Email" type="email" autoComplete="email" value={form.email}
          onChange={set("email")} error={errors.email} placeholder="you@example.com" />
        <PasswordField ref={passwordRef} label="Password" autoComplete="new-password" value={form.password}
          onChange={set("password")} error={errors.password} rules={NEW_PASSWORD_RULES} />
        <SubmitButton busy={busy} className="button-primary button-wide">create account</SubmitButton>
      </form>
    </AuthLayout>
  );
}
