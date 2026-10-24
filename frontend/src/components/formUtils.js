// Form helpers shared by the pages.
export const NEW_PASSWORD_RULES = ["at least 8 characters", "at least one letter and one digit", "at most 72 bytes"];

/** Runs the checks; returns the errors and focuses the first wrong field (FE2-U02). */
export function validate(checks, refs) {
  const errors = {};
  for (const [name, message] of Object.entries(checks)) if (message) errors[name] = message;
  const first = Object.keys(checks).find((name) => errors[name]);
  if (first && refs[first]?.current) refs[first].current.focus();
  return errors;
}
