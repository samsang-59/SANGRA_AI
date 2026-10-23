import { useRef, useState } from "react";
import { changePassword, resendVerification } from "../api/auth.js";
import { PASSWORD_CHANGED, useAuth } from "../auth/useAuth.js";
import { PasswordField, SubmitButton } from "../components/Form.jsx";
import { validate } from "../components/formUtils.js";
import { Notice } from "../components/Status.jsx";
import { messageFor } from "../errors.js";
import { checkConfirm, checkLoginPassword, checkNewPassword } from "../validation.js";

function EmailCard({ user }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function resend() {
    if (busy) return;
    setBusy(true);
    try {
      const data = await resendVerification();
      setResult({ kind: "success", text: data?.message || "We sent a new verification link." });
    } catch (error) {
      setResult({ kind: "error", text: messageFor(error) }); // TOO_SOON says how long to wait
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card section-card" aria-labelledby="account-email">
      <div className="field">
        <label id="account-email" htmlFor="account-email-value">Email</label>
        <input id="account-email-value" className="input" value={user.email} readOnly />
      </div>
      <div className="row-between">
        <div className="row">
          <span className="text-2">Email status</span>
          {user.is_email_verified
            ? <span className="tag tag-success">verified</span>
            : <span className="tag tag-accent">not verified</span>}
        </div>
        {!user.is_email_verified && (
          <button type="button" className="button" onClick={resend} disabled={busy}>
            {busy ? "sending…" : "resend verification email"}
          </button>
        )}
      </div>
      {result && <Notice kind={result.kind}>{result.text}</Notice>}
    </section>
  );
}

function ChangePasswordCard() {
  const auth = useAuth();
  const [form, setForm] = useState({ old: "", next: "", confirm: "" });
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const oldRef = useRef(null);
  const nextRef = useRef(null);
  const confirmRef = useRef(null);
  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const found = validate({ old: checkLoginPassword(form.old), next: checkNewPassword(form.next),
      confirm: checkConfirm(form.next, form.confirm) }, { old: oldRef, next: nextRef, confirm: confirmRef });
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setServerError("");
    try {
      await changePassword(form.old, form.next);
      // Every JWT issued before the change is now refused, so the user logs in again (F1).
      auth.logout({ kind: "success", text: PASSWORD_CHANGED });
    } catch (error) {
      setServerError(messageFor(error)); // WRONG_OLD_PASSWORD is a 400: the user stays logged in (FE2-X15)
      setBusy(false);
    }
  }

  return (
    <form className="card section-card" onSubmit={submit} noValidate aria-labelledby="change-password-title">
      <div>
        <h2 id="change-password-title">Change password</h2>
        <p className="card-text">You will be logged out on every device and asked to log in again.</p>
      </div>
      {serverError && <Notice kind="error">{serverError}</Notice>}
      <PasswordField ref={oldRef} label="Old password" autoComplete="current-password" value={form.old}
        onChange={set("old")} error={errors.old} />
      <PasswordField ref={nextRef} label="New password" autoComplete="new-password" value={form.next}
        onChange={set("next")} error={errors.next}
        hint={"8+ characters · a letter and a digit · at most 72 bytes"} hintMono />
      <PasswordField ref={confirmRef} label="Confirm new password" autoComplete="new-password" value={form.confirm}
        onChange={set("confirm")} error={errors.confirm} />
      <div className="row-end">
        <SubmitButton busy={busy}>change password</SubmitButton>
      </div>
    </form>
  );
}

export default function AccountPage() {
  const auth = useAuth();
  return (
    <main className="page page-narrow">
      <h1>Account</h1>
      <EmailCard user={auth.user} />
      <ChangePasswordCard />
      <section className="card logout-card">
        <span>Log out of this browser</span>
        <button type="button" className="button button-danger" onClick={() => auth.logout()}>log out</button>
      </section>
    </main>
  );
}
