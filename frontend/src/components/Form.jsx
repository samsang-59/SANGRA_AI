// Form pieces: a labelled field with help text and an error (announced, linked with aria-describedby), a password
// input with show/hide (FE2-S12), and a submit button that is disabled while its request runs (FE2-X16).
import { forwardRef, useId, useState } from "react";

export const Field = forwardRef(function Field(
  { label, hint, hintMono = false, mono = false, error, rules, aside, type = "text", textarea = false, ...input },
  ref,
) {
  const id = useId();
  const describedBy = [error && `${id}-error`, hint && `${id}-hint`, rules && `${id}-rules`].filter(Boolean).join(" ");
  const Tag = textarea ? "textarea" : "input";
  return (
    <div className="field">
      <div className="field-label-row">
        <label htmlFor={id}>{label}</label>
        {aside}
      </div>
      <Tag
        ref={ref}
        id={id}
        type={textarea ? undefined : type}
        className={`input${mono ? " input-mono" : ""}${error ? " input-error" : ""}`}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={describedBy || undefined}
        {...input}
      />
      {error && <p className="field-error" id={`${id}-error`}>{error}</p>}
      {hint && <p className={`field-hint${hintMono ? " field-hint-mono" : ""}`} id={`${id}-hint`}>{hint}</p>}
      {rules && (
        <ul className="field-rules" id={`${id}-rules`}>
          {rules.map((rule) => <li key={rule}>{rule}</li>)}
        </ul>
      )}
    </div>
  );
});

export const PasswordField = forwardRef(function PasswordField(props, ref) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-field">
      <Field ref={ref} {...props} type={visible ? "text" : "password"} spellCheck="false" />
      <button type="button" className="password-toggle" onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "Hide password" : "Show password"} aria-pressed={visible}>
        {visible ? "hide" : "show"}
      </button>
    </div>
  );
});

export function SubmitButton({ busy, children, busyText = "sending…", className = "button-primary", ...rest }) {
  return (
    <button type="submit" className={`button ${className}${busy ? " button-busy" : ""}`} disabled={busy}
      aria-busy={busy || undefined} {...rest}>
      {busy ? <><span className="spinner spinner-inline" aria-hidden="true" />{busyText}</> : children}
    </button>
  );
}
