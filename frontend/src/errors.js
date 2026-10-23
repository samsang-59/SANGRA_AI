// What the user sees for an ApiError (05_frontend_design.md F4, "How errors are shown"). Pages show this text; a
// few codes get special handling on their page (links, reloads, the 404 page).
const BY_CODE = {
  INVALID_CREDENTIALS: "Wrong email or password",
  NETWORK_ERROR: "Cannot reach the server",
  TIMEOUT: "The server did not answer in time. Please try again.",
};

export function messageFor(error) {
  if (!error) return "";
  if (BY_CODE[error.code]) return BY_CODE[error.code];
  if (error.code === "UNKNOWN_ERROR" || !error.message) return "Something went wrong";
  return error.message; // the backend's own message (VALIDATION_ERROR names the fields, 429 says when to retry)
}
