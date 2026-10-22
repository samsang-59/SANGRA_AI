# SANGRA_AI - Step 5: Frontend Design

(Written in simple language. Work in progress: we add one part at a time. Date: 2026-10-22.)
Tool: React + Vite. Claude writes the code. The screen designs come from Claude Design (given by you at implementation time).
Order of parts: F1 Screens and routes -> F2 API layer -> F3 Auth state -> F4 Data handling -> F5 Pages/components -> F6 Run page (live + result). All DONE.

---------------------------------------------------------------------------------------------------
## F1: Screens and routes  (DECIDED)

One page per URL. React Router handles the URLs. Email links open the frontend, so the backend needs a FRONTEND_URL setting (added to Layer 7).

| URL | Page | Login needed | Backend routes it uses |
|---|---|---|---|
| /login | Login | no (logged-in users are sent to /projects) | /auth/login |
| /register | Register | no | /auth/register |
| /verify-email?token=... | Verify email (from the email link) | no | /auth/verify-email |
| /forgot-password | Forgot password | no | /auth/forgot-password |
| /reset-password?token=... | Reset password (from the email link) | no | /auth/reset-password |
| /projects | Projects list (create, edit, delete, "Run" button) | yes | /projects routes |
| /projects/new and /projects/:id/edit | Project form | yes | POST and PATCH /projects |
| /projects/:id/run | Start a run: bug text, plus a zip file for zip projects | yes | POST /projects/{id}/runs |
| /runs | History list (filter by project and status, pages) | yes | GET /runs |
| /runs/:id | **Run page**: live view while the run is active, result view when it has ended (F6) | yes | GET /runs/{id}, /events, /approval, /hint, /cancel, /package, /apply-to-source, DELETE /runs/{id} |
| /account | Account page | yes | /auth/me, /auth/resend-verification, /auth/change-password |
| / | Goes to /projects | | |
| anything else | "Page not found" | | |

Decision: the "live run" screen and the "run detail" screen are ONE page (/runs/:id). It looks at the run's status: active runs show the
live view, ended runs show the result view. Reason: it is the same run, the same URL, and the page just changes when the run ends.

Account page contents (decided): email (read-only), verified status with a "Resend verification email" button (only when not
verified), Change password form (old password, new password, confirm), Logout button.
Not included (decided): change email, delete account, profile picture.
After a password change the user is logged out and sent to login ("Password changed, please log in again").
After register the backend returns a JWT, so the user is logged in at once (they can resend the verification email from the app).
The verify-email and reset-password pages remove the token from the address bar after reading it (so it does not stay in browser history).

---------------------------------------------------------------------------------------------------
## F2: API layer  (DECIDED)

Job: the only place that talks to the backend. Pages never call fetch themselves.
```
src/api/
  client.js      the HTTP client
  auth.js        one function per auth route
  projects.js    one function per project route
  runs.js        one function per run route
  stream.js      openRunStream (the live stream helper)
```

### F2.1 HTTP client (client.js)
Why: every request needs the same things (base URL, JWT header, error handling). Write them once, all pages reuse them.

| Job | How |
|---|---|
| Base URL | Read once from .env (VITE_API_URL). Default http://localhost:8000/api/v1 |
| JWT | Adds "Authorization: Bearer <token>" when a token is saved |
| Request body | A normal object is sent as JSON. A FormData (zip upload) is sent as it is; the browser sets the content type |
| Success | Returns only the inside of { "data": ... } |
| Error | Throws one shape: ApiError { status, code, message }. Network failure -> code NETWORK_ERROR (status 0). A reply that is not our JSON -> code UNKNOWN_ERROR |
| 401 rule | See F2.4 |

Pages catch ApiError and show the message; they use the code only for special cases (for example EMAIL_NOT_VERIFIED shows a "verify your email" hint).

### F2.2 Route functions
| File | Functions |
|---|---|
| auth.js | register, login, verifyEmail, resendVerification, forgotPassword, resetPassword, changePassword, getMe |
| projects.js | createProject, listProjects, getProject, updateProject, deleteProject |
| runs.js | startRun(projectId, bug, zipFile?), listRuns(filters), getRun, submitApproval(runId, attemptId, decision, feedback?), sendHint, cancelRun, applyToSource, downloadPackage, deleteRun |

- startRun sends a FormData when there is a zip file.
- downloadPackage: a plain link cannot carry the JWT header, so the function fetches the file as a blob and saves it itself.

### F2.3 Live stream helper (stream.js)
openRunStream(runId, onEvent, { onEnd, onError, onReconnecting }) returns a stop() function (the screen calls it when it closes).
- Opens GET /runs/{id}/events with fetch, sending the JWT header and, when reconnecting, Last-Event-ID: <last number>.
- Joins the pieces that arrive, splits them into events (events end with a blank line), converts each into an object:
  { sequence_number, event_type, message, details, attempt_id, created_at }, then calls onEvent(object).
- Skips an event whose number it already handled (no duplicates after a reconnect). Ignores lines starting with ":" (ping).
- Stops and calls onEnd when it receives the status_changed event with a final status (succeeded, failed, stopped).
- If the connection drops before that: call onReconnecting, reconnect after 2s, 4s, 8s ... up to 5 tries, then onError.
- Events have short details only. On approval_requested the screen loads GET /runs/{id} to show old and new code.
- A 401 on the stream follows the same rule as F2.4.
- stop() aborts the connection (needed on screen close, and because React in development runs effects twice).
How the screen uses it: the screen hands over a callback; the helper does all the receiving work and calls the callback with
finished objects; the callback calls a useState setter and React redraws the screen.

### F2.4 The 401 rule
A 401 on a PROTECTED route means the login is no longer valid (expired after 24 hours, password changed, or broken token):
the client deletes the saved token and calls a function registered by the auth state (F3), which sends the user to login with
"Session ended, please log in again". The client itself does not know the router.
The PUBLIC auth routes (register, login, verify-email, forgot-password, reset-password) are EXEMPT: a 401 there is just
INVALID_CREDENTIALS, and the login form shows the backend's message ("Wrong email or password") without any redirect.
(WRONG_OLD_PASSWORD on change-password is a 400, so it is never mistaken for an expired login.)

### F2.5 Backend rules the API layer depends on (already written in 04_backend_design.md)
- Response shape: { data } or { error: { code, message } }, also for validation errors (422, VALIDATION_ERROR) and 429 (RATE_LIMITED, TOO_SOON).
- Stream format, final status_changed event, ping lines, Last-Event-ID header (Layer 3 special cases, Layer 4 stream_events).
- CORS allows the headers Authorization, Content-Type, Last-Event-ID (Layer 7).

## F3: Auth state  (DECIDED)

Job: the app remembers who is logged in. (Hotel example: the reception desk. It gives the room card, remembers you are checked in
after a refresh, and takes the card away at checkout or expiry. The HTTP client is the waiter who shows the card.)

### What it holds (in one React Context, AuthProvider)
| Piece | Meaning |
|---|---|
| token | The JWT. Saved in localStorage (key sangra_token) so a refresh does not log the user out. Decided: localStorage, not memory and not a cookie |
| user | { name, email, is_email_verified } from getMe() |
| status | loading (checking the saved token), loggedIn, loggedOut |

### What it offers
login(email, password) | register(name, email, password) (the backend returns a JWT, so it logs in at once) | logout() | refreshUser().
logout() deletes token and user and goes to /login. The backend is not called, because JWTs are not stored there.
All localStorage reads and writes are wrapped in try/catch (it can be blocked in private windows).

### When the app opens
1. No saved token -> loggedOut.
2. A saved token -> status loading, call getMe().
3. Works -> loggedIn. A 401 -> delete the token, loggedOut.
While loading, a spinner is shown (otherwise a logged-in user would see a flash of the login page on every refresh).

### The 401 function (the answer to F2.4)
At startup AuthProvider gives the HTTP client one function. When a protected request gets a 401 the client calls it:
delete token and user, set loggedOut, go to /login with the message "Session ended, please log in again".
The JWT lasts 24 hours and there is no refresh token. After that the user logs in again.

### Route guards
- ProtectedRoute (all pages that need login): loading -> spinner. loggedOut -> /login, remembering the wanted page so the user returns
  there after login. loggedIn -> the page.
- PublicOnlyRoute (login, register, forgot, reset): loggedIn -> /projects.
- /verify-email works for both (a verified link can be opened logged in or out). After success: refreshUser() if logged in.

### Unverified users
They can log in but cannot start runs. While is_email_verified is false, a banner on every page says "Verify your email to start runs"
with a Resend button (resendVerification). The Run button is disabled with the same reason.

### Two tabs
Logging out in one tab does not log out the other at once. The other tab finds out on its next request (401 rule). Syncing tabs with the
browser's storage event can be added later without redesign.

---------------------------------------------------------------------------------------------------
## F4: Data and state handling  (DECIDED)

Job: how pages ask the API layer for data and show loading, errors and empty states.

### Tools
- React Router for URLs. No axios (the client from F2 uses fetch). No Redux.
- TanStack Query (React Query) for data from the backend. It gives loading/error/refetch for free, so pages do not write the
  useEffect + useState + loading code again and again. Pages call small hooks (useProjects, useRun ...) that wrap the F2 functions.
- Forms: plain useState (no form library for the MVP).
- Styling: decided later, when you give the Claude Design screens (we choose what fits them).

### Data keys and what refreshes them
| Data | Key | Refreshed when |
|---|---|---|
| Project list | ["projects"] | after create, edit, delete project |
| One project | ["project", id] | after edit |
| History | ["runs", filters, page] | after start, delete or cancel |
| One run | ["run", id] | on the live events listed in F6, and after approval, hint, cancel, apply |
| Active run (for the banner) | ["activeRun"] | every 30 seconds while logged in, and after start or end of a run |

The "active run" comes from GET /runs?status=active. The special value "active" (= any of the six active statuses) was added to the
backend filter, so the frontend can find the run that is blocking a new start.

### The four states every data page shows
loading (spinner) | error (message from ApiError, with a Retry button) | empty ("No projects yet - create one") | data.
Shared small components do this, so every page looks the same.

### How errors are shown (by code)
| Code | What the user sees |
|---|---|
| VALIDATION_ERROR (422) | The message (it names the bad fields) above the form. The form also checks the same rules before sending (see F5) |
| INVALID_CREDENTIALS | "Wrong email or password" on the login form (no redirect) |
| EMAIL_NOT_VERIFIED | "Verify your email first" with the Resend button |
| ACTIVE_RUN_EXISTS | "You already have a run in progress" with a link to it (found with the "active" filter) |
| PROJECT_HAS_ACTIVE_RUN, RUN_STATE_INVALID, STALE_ATTEMPT | The message, then the page reloads the run so it shows the real state |
| NOT_FOUND | "Page not found" page |
| RATE_LIMITED, TOO_SOON | The message ("try again after ...") |
| LOCAL_PATH_NOT_ALLOWED, PATH_NOT_FOUND, ZIP_REQUIRED, SOURCE_CHANGED, PACKAGE_EXPIRED | The message on the page where it happened |
| NETWORK_ERROR | "Cannot reach the server" with Retry |
| anything else | The backend message, or "Something went wrong" |

### Double clicks
Every button that sends something is disabled while its request is running (the backend also protects with conditional updates).

---------------------------------------------------------------------------------------------------
## F5: Pages and components  (DECIDED structure; the looks come from your Claude Design screens)

```
src/
  api/          F2 (client, auth, projects, runs, stream)
  auth/         AuthProvider, ProtectedRoute, PublicOnlyRoute
  hooks/        useProjects, useProject, useRuns, useRun, useActiveRun (wrap TanStack Query + api functions)
  pages/        LoginPage, RegisterPage, VerifyEmailPage, ForgotPasswordPage, ResetPasswordPage, ProjectsPage, ProjectFormPage,
                StartRunPage, HistoryPage, RunPage, AccountPage, NotFoundPage
  components/   Layout (top bar, verify banner, active-run banner), Spinner, ErrorMessage, EmptyState, StatusBadge, ConfirmDialog,
                CodeDiff (old vs new code), EventTimeline, ApprovalBox, HintBox, TestResultPanel, AttemptList
  App.jsx       route table      main.jsx   starts the app (QueryClient, AuthProvider, Router)
```

### Page notes
| Page | What it does | Checks before sending (same rules as the backend; the backend stays the real judge) |
|---|---|---|
| Register | name, email, password | name 1-100, valid email, password 8+ with a letter and a digit, at most 72 bytes |
| Login | email, password | not empty |
| Projects | list, "Run" button, edit, delete (confirm dialog). Shows the active-run banner | |
| Project form | name, language (java/python), source type (github/local_path/zip), source reference (hidden for zip), branch (optional), test command | name 1-100, test command 1-500, GitHub URL shape, absolute path for local_path |
| Start run | bug text; zip upload only if the project is a zip project. Disabled if the user is unverified | bug 10-2000 chars, file ends with .zip and is at most 50 MB |
| History | table of runs with status badge, project, date; filters (project, status); page buttons (20 per page, limit at most 50) | |
| Run page | see F6 | |
| Account | see F1 | new password rules as register |

Local path option: always shown in the project form. If the backend is not in local mode it answers LOCAL_PATH_NOT_ALLOWED and the
message is shown (later improvement: /health can tell the frontend the mode so the option is hidden).

---------------------------------------------------------------------------------------------------
## F6: The Run page (live + result)  (DECIDED)

One page, /runs/:id. It works from two sources: the run record (GET /runs/{id}, kept fresh by TanStack Query) and the live events (F2.3).

### Steps when the page opens
1. Load the run with getRun.
2. If the status is active (started, searching, waiting_for_hint, waiting_for_approval, applying, testing): call openRunStream.
   The first connection has no Last-Event-ID, so the server sends ALL events of the run so far, then the new ones. So the timeline is
   built only from the stream (no separate "history of events" request), and coming back to a running run later shows everything.
3. If the run has ended: show the result view. No stream is opened.
4. When the page closes, call stop(). Leaving the page does NOT cancel the run; only the Cancel button does.

### What each event does
| Event | The page does |
|---|---|
| any event | Adds the message to the timeline (kept in order of sequence_number) |
| status_changed | Refresh the run record (status, counters). The status badge changes |
| approval_requested | Refresh the run record: the new attempt (explanation and edits) is loaded and the approval box appears |
| hint_requested | Refresh the run record: the hint box appears |
| tests_finished | Refresh the run record (test results) |
| status_changed with a final status | The stream helper calls onEnd; refresh the run record one last time; switch to the result view |
| error | Shown in the timeline in red |
The stream helper also tells the page when it is reconnecting (onReconnecting), and the page shows a small "Reconnecting..." note.

### What is shown by status (live view)
| Status | Screen |
|---|---|
| started, searching, applying, testing | Timeline + a label of what is happening + Cancel button |
| waiting_for_hint | Hint box (text 3-2000 chars, "Hint 2 of 3"), Send hint, Cancel. Note: "This run stops automatically after 1 hour of waiting" |
| waiting_for_approval | Approval box: the agent's explanation, each edit as old vs new code (file, lines), "Rejected 1 of 3". Accept and Reject. Reject needs feedback (3-2000 chars). Same 1-hour note. Cancel |

Approval details: the page sends the attempt_id of the proposal it is showing. If the answer is STALE_ATTEMPT or RUN_STATE_INVALID
(old tab, double click, cancelled meanwhile), the page shows the message and reloads the run so it shows the real state.
After Accept or Reject the buttons stay disabled until the next status_changed event arrives.

### Result view (status succeeded, failed or stopped)
- Big status badge and the reason in plain words (map below), plus final_summary. If the baseline already passed the summary carries the
  label "tests passed, but no test covers this bug, please verify the fix yourself".
- Test panel: baseline result next to the after-fix result (the human is the final judge).
- Attempts list: each attempt with its explanation, edits (old vs new), decision, and test status.
- Download package button (fetches the file with the JWT). PACKAGE_EXPIRED -> "The package expired (kept 7 days)". Cancelled runs also have a package.
- "Apply to my folder": shown only for succeeded runs of local_path projects that were not applied yet. SOURCE_CHANGED shows its message
  ("your folder changed since the run, download the patch instead").
- Delete run (confirm dialog).

### Stop reason -> plain words
| stop_reason | Text |
|---|---|
| cancelled_by_user | You cancelled the run |
| timed_out | Stopped: waited over 1 hour for you |
| reject_limit_reached | Stopped: 3 fixes were rejected |
| tests_failed_3_times | Failed: 3 fixes did not pass the tests |
| proposal_invalid | Failed: the agent could not make a valid fix |
| no_match_after_3_hints | Failed: the bug was not found even after 3 hints |
| clone_failed | Failed: the code could not be copied (check the project source) |
| anything else (test_runner_error, apply_failed, undo_failed, llm_error, internal_error, server_restarted ...) | Failed: a technical problem happened (details in the timeline) |

### Active-run banner (Layout)
While a run is active, every page shows "A run is in progress - open it" (from the ["activeRun"] data). Only one active run per user is
possible, so one link is enough.

---------------------------------------------------------------------------------------------------
## To verify or decide at implementation time
- Styling approach and component library, after you give the Claude Design screens.
- The exact look of the code diff (old vs new) and the timeline.
- Whether /health should also tell the frontend the app mode (local or deployed).
- How the fetch-based stream reader behaves on slow networks (test reconnect: stop the backend in the middle of a run).
- Browser support for streaming fetch responses (modern browsers are fine).
