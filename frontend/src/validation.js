// The checks a form makes before sending (05_frontend_design.md F5): the same rules as the backend validators,
// which stay the real judge. Each function returns an error text, or "" when the value is fine.
export const NAME_MAX = 100;
export const PASSWORD_MIN_CHARS = 8;
export const PASSWORD_MAX_BYTES = 72;
export const EMAIL_MAX = 254;

const utf8Bytes = (text) => new TextEncoder().encode(text).length;

export function checkName(name) {
  const value = name.trim();
  if (!value) return "Enter your name.";
  if (value.length > NAME_MAX) return `Name must be 1-${NAME_MAX} characters.`;
  return "";
}

export function checkEmail(email) {
  const value = email.trim();
  if (!value) return "Enter your email.";
  if (value.length > EMAIL_MAX || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return "Enter a valid email address.";
  return "";
}

export function checkNewPassword(password) {
  if (utf8Bytes(password) > PASSWORD_MAX_BYTES) return `Password must be at most ${PASSWORD_MAX_BYTES} bytes.`;
  if (password.length < PASSWORD_MIN_CHARS) return `Password must be at least ${PASSWORD_MIN_CHARS} characters.`;
  if (!/\p{L}/u.test(password)) return "Password must contain at least one letter.";
  if (!/\p{Nd}/u.test(password)) return "Password must contain at least one digit.";
  return "";
}

export function checkLoginPassword(password) {
  return password ? "" : "Enter your password.";
}

export function checkConfirm(password, confirm) {
  if (!confirm) return "Repeat the new password.";
  return password === confirm ? "" : "The two passwords are not the same.";
}

/** A place inside the app to go back to after login (FE2-S01): only "/something", never "//host" or a full URL. */
export function safeReturnPath(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")
      || value.includes("://") || [...value].some((ch) => ch.charCodeAt(0) < 32)) {
    return null;
  }
  return value;
}
