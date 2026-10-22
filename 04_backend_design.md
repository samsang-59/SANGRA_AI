# SANGRA_AI - Step 4: Backend Layered Design

(Written in simple language. Work in progress: we add one layer at a time.)
Layer order: Router -> Validators -> Controller -> Service -> Repository -> Agent -> Core/Config.
(DB layer = Step 3, already done.)

Flow of a request: Router -> Validator -> Controller -> Service -> Repository -> DB.

Folder idea (first proposal):
```
app/
  routers/ validators/ controllers/ services/ repositories/ models/
  agent/     # AI layer: graph, nodes, tools, prompts (separate module)
  runners/   # Docker test runner, workspace and package handling
  core/      # config, auth (JWT, password hashing), DB pool
```
The agent module never touches the database directly. Graph nodes call services, services call
repositories (so the layer rules stay intact).

---------------------------------------------------------------------------------------------------
## Layer 1: Router  (DONE)

Job: map URL + method to a controller, and say whether the route is public or protected
(protected = needs a valid JWT). No logic here. All paths start with /api/v1.

### Health
| Method and path | Access | Purpose |
|---|---|---|
| GET /health | public | Checks that the database and Docker are reachable |

### Auth
| Method and path | Access | Purpose |
|---|---|---|
| POST /auth/register | public | Create account, send verification email, return a JWT (the user is logged in at once) |
| POST /auth/login | public | Return JWT |
| POST /auth/verify-email | public | Token from the email link |
| POST /auth/resend-verification | protected | Send the link again |
| POST /auth/forgot-password | public | Send reset link |
| POST /auth/reset-password | public | Token + new password |
| POST /auth/change-password | protected | Old password + new password. Every old JWT stops working (user logs in again) |
| GET /auth/me | protected | Current user |

### Projects (all protected)
| Method and path | Purpose |
|---|---|
| POST /projects | Create |
| GET /projects | List mine |
| GET /projects/{id} | One project |
| PATCH /projects/{id} | Edit |
| DELETE /projects/{id} | Delete (its runs are deleted too) |

### Runs (all protected)
| Method and path | Purpose |
|---|---|
| POST /projects/{id}/runs | Start a run (bug text; plus a zip file if it is a zip project) |
| GET /runs | History list |
| GET /runs/{id} | Full detail: attempts, edits, test results |
| GET /runs/{id}/events | Live progress stream (SSE). Optional header Last-Event-ID = the last event number the browser already has |
| POST /runs/{id}/approval | Accept or reject, with feedback |
| POST /runs/{id}/hint | Send a better hint |
| POST /runs/{id}/cancel | Cancel a run (stop_reason: cancelled_by_user) |
| POST /runs/{id}/apply-to-source | "Apply to my folder" (local mode only) |
| GET /runs/{id}/package | Download the package |
| DELETE /runs/{id} | Delete a run |

### Decisions in this layer
- Cancel is included.
- Auto-timeout: a run waiting for approval or a hint for more than 1 HOUR is stopped automatically
  (stop_reason: timed_out). A small background job checks every few minutes. Needed because of the
  rule "one active run per user", otherwise a user who closes the browser would be blocked forever.
- Decided in core/auth (Layer 7): the browser's built-in live-stream tool cannot send a login header, so the frontend opens
  the stream with fetch, which can send the Authorization header.
- Rate limiting for login, register, and forgot-password (stops password guessing). Place it in core.

---------------------------------------------------------------------------------------------------
## Layer 2: Validators  (DONE)

Job: check the SHAPE of the incoming request (FastAPI + Pydantic). A badly shaped request is rejected here.
Rule: validators check only what can be decided from the request alone. Anything that needs the database
or the current state (does this project belong to you, is there an active run) is the SERVICE's job.
Public routes get the strictest checks.

| Route | What is checked |
|---|---|
| register | name 1-100 chars (trimmed). Email valid, lowercased. Password 8+ chars, at least one letter and one digit, at most 72 BYTES (bcrypt only reads the first 72 bytes) |
| login | Email valid. Password not empty (no strength rule) |
| verify-email, reset-password | token is a string of the expected length. reset also has new_password with the register rules |
| forgot-password | Email valid |
| change-password | old_password not empty. new_password follows the register rules (8+ chars, a letter and a digit, at most 72 bytes) |
| project create / edit | name 1-100. language java or python. source_type github, local_path, or zip. source_reference required unless zip. GitHub must look like https://github.com/owner/repo. Local path must be absolute. branch optional, valid branch-name characters only. test_command 1-500 chars |
| start run | bug_description 10-2000 chars. Zip file (only for zip projects): .zip, at most 50 MB |
| approval | decision is accepted or rejected. feedback required when rejected (3-2000 chars). attempt_id of the proposal being answered |
| hint | text 3-2000 chars |
| every {id} in a URL | positive whole number |
| GET /runs | optional project_id and status. page and limit (limit at most 50) |

Why approval carries attempt_id: if an old browser tab clicks Accept on an outdated proposal, the service
sees it is not the attempt currently waiting and refuses.

Checks done by the SERVICE, not validators: local path allowed only in local mode (server setting);
a zip is required only when the saved project type is zip.

---------------------------------------------------------------------------------------------------
## Layer 3: Controller  (DONE)

Job: receive the validated request and the logged-in user, call ONE service function, and turn the
service's result into an HTTP status code + JSON body. It holds no business logic.
Rule from our design: services never throw errors or choose HTTP codes. They return a RESULT
(success or failure, data, and an error code). The controller gives the result its meaning.

### Response shape (same everywhere)
- Success: { "data": ... }
- Error:   { "error": { "code": "NOT_FOUND", "message": "..." } }
- Private fields (like password_hash) are never sent back.

### Error code -> HTTP status
| Error code | HTTP | Meaning |
|---|---|---|
| EMAIL_ALREADY_REGISTERED | 409 | Email taken |
| INVALID_CREDENTIALS | 401 | Wrong email or password (same message for both) |
| EMAIL_NOT_VERIFIED | 403 | Needs verification first (used when starting a run) |
| TOKEN_INVALID_OR_EXPIRED | 400 | Bad or old link |
| NOT_FOUND | 404 | Does not exist, or belongs to someone else |
| ACTIVE_RUN_EXISTS | 409 | User already has an active run |
| RUN_STATE_INVALID | 409 | Action not allowed in the run's current state |
| STALE_ATTEMPT | 409 | An old proposal was answered |
| LOCAL_PATH_NOT_ALLOWED | 400 | Local paths allowed only when the backend runs on the user's own machine |
| ZIP_REQUIRED | 400 | Zip project but no zip sent |
| PACKAGE_EXPIRED | 410 | Package already deleted |
| anything unexpected | 500 | Server problem |
From other layers: missing/invalid JWT -> 401 (core/auth). Too many attempts -> 429 with code RATE_LIMITED (rate limiting).
Validation errors (a badly shaped request) -> 422 with code VALIDATION_ERROR; the message names the bad fields
(see Layer 7, item 6). Codes added later by services are listed under "Error codes added by services" in Layer 4.

Important for the frontend: INVALID_CREDENTIALS (wrong email or password at login) is also a 401. The frontend's
"session expired, go to login" rule must NOT apply to the public auth routes.

### Special cases
- forgot-password always answers 200: "If this email exists, we sent a link".
- Live stream (/events): the controller keeps the connection open and sends each Run Event as it comes;
  the service supplies the events. The stream ends when the run ends. Format (the contract with the frontend):
  - Content type text/event-stream. Each event is sent as three lines and a blank line:
        id: <sequence_number>
        event: <event_type>
        data: {"sequence_number": 17, "event_type": "approval_requested", "message": "...", "details": {...}, "attempt_id": 4, "created_at": "..."}
  - Every change of the run's status adds a status_changed event with details {"status": "...", "stop_reason": "..." (only when the run ended)}.
    The LAST event of a run is the status_changed event with a final status (succeeded, failed, or stopped). After sending it the
    server closes the stream. The frontend uses this event to know the run has ended.
  - If a run is already finished when the browser connects, the server sends the events after Last-Event-ID and closes.
  - A comment line ": ping" is sent about every 15 seconds so that idle connections are not closed. The frontend ignores it.
  - Events carry only SHORT details (never full code). To show old/new code the frontend loads GET /runs/{id}.
- Download (/package): the controller returns the file.
- LOCAL_PATH_NOT_ALLOWED exists because after deployment a typed path would point to the SERVER's disk,
  which is a security hole.

### Decision: unverified users
They CAN log in, but cannot start runs until they verify (EMAIL_NOT_VERIFIED). They can use
"resend verification" from inside the app.

---------------------------------------------------------------------------------------------------
## Layer 4: Service  (DONE: AuthService, ProjectService, RunService; agent-side helpers come with the Agent layer)

Job: business logic only. Returns a result (success/failure + data + error code). Never throws HTTP errors.
When several saves must succeed or fail together, the service calls ONE repository function that does
them in a single transaction (transactions belong to the repository layer).

Two kinds of tokens (important):
| | JWT | Email token (verification / reset) |
|---|---|---|
| Created | At login and register | At register, resend, forgot-password |
| Travels | In the header of every request | Only inside the email link |
| Used | Many times, until it expires (24 hours) | Exactly once |
| Stored in DB | No (checked by its signature) | Yes, only a hash, in user_tokens |
The email token is NEVER returned to the browser as a response.

Who does what: the SERVICE creates tokens and JWTs, sends emails (through a mailer helper in core), and
makes decisions. The REPOSITORY saves. The CONTROLLER only turns results into HTTP responses.

### AuthService

register(name, email, password)
1. Check the email is not taken (EMAIL_ALREADY_REGISTERED; the unique rule in the DB is the final guard).
2. bcrypt-hash the password.
3. Create a random verification token + its hash. Save user and token hash in ONE repository transaction.
4. Email the link (real token inside the link). If sending fails: log it, do not fail signup (user can resend).
5. Create the JWT (24 hours). Return user + JWT.

login(email, password)
1. Find the user by lowercase email.
2. Check the password with bcrypt's check function.
3. Unknown email OR wrong password -> INVALID_CREDENTIALS (same message).
4. Create the JWT and return it.

verify_email(token)
1. Hash the token from the link. Find a row: type email_verification, unused, not expired (24 hours).
2. None -> TOKEN_INVALID_OR_EXPIRED.
3. One repository transaction: mark token used + set is_email_verified = true. No email is sent here.

resend_verification(current user)
1. Already verified -> return "already verified".
2. Last token created less than 60 seconds ago -> TOO_SOON (HTTP 429).
3. Else create a new token, save its hash, email the link.

forgot_password(email)
1. If the user exists: create a reset token (valid 30 minutes), save its hash, email the link.
2. If not: do nothing.
3. ALWAYS return success ("If this email exists, we sent a link").

reset_password(token, new_password)
1. Hash the token. Find a row: type password_reset, unused, not expired. The row tells us the user.
2. None -> TOKEN_INVALID_OR_EXPIRED.
3. bcrypt-hash the new password. One repository transaction: save password_hash, set
   password_changed_at = now, mark this token and other unused reset tokens as used.

change_password(current user, old_password, new_password)
1. Check old_password against the saved hash (bcrypt in a thread). Wrong -> WRONG_OLD_PASSWORD (a 400, NOT a 401, so the
   frontend does not think the login expired).
2. New password same as the old one -> NEW_PASSWORD_SAME_AS_OLD.
3. bcrypt-hash the new password. Repository: save password_hash and set password_changed_at = now.
4. Return success. No new JWT is made here (a JWT is made only at register and login). The old JWT is now rejected, so the
   frontend logs the user out and sends them to the login page ("Password changed, please log in again").

get_me(current user) -> returns the user (never the password hash).

Protected routes (done in core/auth before the controller runs): read the JWT from the request, check
signature and expiry, find the user from the ID inside it, reject if the JWT was issued before
users.password_changed_at, else hand "the current user" to the controller. Failure -> 401.

### ProjectService  (every lookup also filters by the user's ID; not yours = NOT_FOUND)

create_project(user, data)
1. local_path while not in local mode -> LOCAL_PATH_NOT_ALLOWED.
2. local mode: check the folder exists and is readable -> PATH_NOT_FOUND.
3. User already has 20 projects -> PROJECT_LIMIT_REACHED.
4. Save and return. (GitHub is NOT checked now; a bad repo fails when a run starts.)

list_projects(user): returns the user's projects, newest first.
get_project(user, id): by ID and user ID, else NOT_FOUND.

update_project(user, id, changed fields)
1. Load existing (NOT_FOUND). Merge changes with existing values.
2. Check the merged result still makes sense (INVALID_PROJECT_CONFIG). Same local-path rules as create.
3. Save and return. Allowed during an active run, because every run keeps its own snapshot.

delete_project(user, id)
1. Load (NOT_FOUND). If it has an active run -> PROJECT_HAS_ACTIVE_RUN (cancel the run first).
2. One repository transaction deletes the project and its runs (cascade). Before that, the repository
   returns the affected run IDs and package files.
3. After the DB commit, the service deletes the package files and the LangGraph data for each run ID.
   Failures are logged; the cleanup job removes leftovers.

### Error codes added by services
TOO_SOON (429), PATH_NOT_FOUND (400), PROJECT_HAS_ACTIVE_RUN (409), INVALID_PROJECT_CONFIG (400),
PROJECT_LIMIT_REACHED (409), WRONG_OLD_PASSWORD (400), NEW_PASSWORD_SAME_AS_OLD (400).

### RunService

Background work: an agent run can take minutes, so start_run answers immediately and the agent keeps working
in the background (an async background task inside the same backend process for the MVP). The screen follows
along through the live stream.

start_run(user, project_id, bug_description, zip file if needed)
1. Load the project (NOT_FOUND). Email not verified -> EMAIL_NOT_VERIFIED.
2. local_path project while not in local mode -> LOCAL_PATH_NOT_ALLOWED. Zip project without a zip -> ZIP_REQUIRED.
3. Create the Run row (status started, snapshots of the project settings, Groq model name from config).
4. One-active-run rule: do NOT check first and insert later (two requests at once could both pass).
   Just try to insert; the database's partial unique index rejects the second. Service returns ACTIVE_RUN_EXISTS.
5. If a zip came, save it to a temporary spot for the agent to unpack.
6. Start the agent in the background. Return the run (id and status) at once.

list_runs(user, filters): the user's runs, newest first, optional project/status filter, with pages.
get_run(user, run_id): run + attempts + file edits + test results + package info (NOT_FOUND if not theirs).

cancel_run(user, run_id)
1. NOT_FOUND if not theirs. Not active -> RUN_STATE_INVALID.
2. Mark stopped with stop_reason cancelled_by_user.
3. Tell the background agent to stop; stop a running test container if there is one.
4. Undo applied edits, then finish like a normal ending: build the package (cancelled runs get one too),
   delete the temporary copy.

delete_run(user, run_id)
1. NOT_FOUND if not theirs. Active -> RUN_STATE_INVALID (cancel first).
2. Repository deletes the rows in one transaction.
3. Then the service deletes the package file and the LangGraph data for this run.

submit_approval(user, run_id, attempt_id, decision, feedback)
1. NOT_FOUND if not theirs. Run must be waiting_for_approval, else RUN_STATE_INVALID.
2. attempt_id must be the proposal currently waiting, else STALE_ATTEMPT.
3. Accepted: save the decision, move the run to applying, resume the agent in the background.
4. Rejected: save decision + feedback, add 1 to reject_count. Either way the service RESUMES the agent. When
   the agent resumes it reads reject_count from the database: if it reached 3, the agent's finish step ends
   the run (stopped, stop_reason reject_limit_reached); otherwise it prepares a new fix using the feedback.
5. The save is a CONDITIONAL update ("only if still waiting for this attempt"), so a double click or two tabs
   cannot approve twice. Returns at once; progress follows through the live stream.

submit_hint(user, run_id, text)
1. NOT_FOUND if not theirs. Run must be waiting_for_hint, else RUN_STATE_INVALID.
2. Save the hint as an event and resume the agent with it.
hint_count goes up when the AGENT asks for a hint. If it would have to ask a 4th time, the run ends
(stop_reason no_match_after_3_hints).

apply_to_source(user, run_id)  ("Apply to my folder")
1. NOT_FOUND if not theirs. Local mode and local-path project only, else LOCAL_PATH_NOT_ALLOWED.
2. Run must be succeeded and applied_to_source_at empty, else RUN_STATE_INVALID.
3. Package must still exist, else PACKAGE_EXPIRED.
4. Safety check: the user may have edited the original folder after the run started. Do NOT overwrite whole
   files. Re-apply the same edits to the original with the same old_code check on the exact line range.
   Check ALL edits first; if ANY edit no longer matches, write nothing and return SOURCE_CHANGED (409):
   "your folder changed since the run, download the patch instead".
5. Record applied_to_source_at = now. Return success.
(Later improvement, not MVP: if the lines moved, search for the exact old code and apply when it is found exactly once.)

get_package(user, run_id): NOT_FOUND if not theirs; not built yet -> RUN_STATE_INVALID; deleted -> PACKAGE_EXPIRED;
otherwise the controller sends the file.

stream_events(user, run_id, after_sequence)   (the controller reads after_sequence from the Last-Event-ID header; 0 if missing)
1. NOT_FOUND if not theirs.
2. Send every saved event after after_sequence (so a reconnecting browser catches up).
3. Keep sending new events until the run is in a final status and everything has been sent (the last one is the
   status_changed event with the final status). Then the stream closes.
MVP: the service checks the run_events table about once a second. Simple, survives restarts, works with
several backend processes later. Faster push methods can come later.

### Background jobs (details later)
- Timeout sweeper: every few minutes, stop runs waiting over 1 hour (stop_reason timed_out).
- Package cleanup: delete packages past expires_at, set deleted_at.
- Workspace cleanup: delete leftover temporary copies older than 24 hours.
- Startup recovery: runs left in started/searching/applying/testing are marked failed (server_restarted);
  runs in waiting_for_approval/waiting_for_hint are left alone (LangGraph has saved their state).

### Error code added by RunService
SOURCE_CHANGED (409).

---------------------------------------------------------------------------------------------------
## Layer 5: Repository  (DONE)

### Rules for every repository
1. Only SQL lives here. No business decisions, no HTTP.
2. Always parameterised queries. Never paste request values into SQL text. For partial updates, column
   names come from a FIXED allowed list in the repository; only values are parameters.
3. Async psycopg 3 with a connection pool from core.
4. One function, one purpose. If a job needs several writes that must succeed or fail together, the
   function does them in ONE transaction.
5. Returns plain data (a row as a dict) or None, never HTTP things. "Expected failures" (duplicate email,
   bad token, active run exists, limit reached) return None or a small outcome. UNEXPECTED database errors
   are raised, logged, and the controller answers 500.
6. Every query a route uses on projects/runs also filters by user_id. Functions without the user filter
   (find_by_id) are for background code only (agent, jobs), never for routes.
7. "Is it expired?" decisions use the DATABASE clock (now()).

### UserRepository (users, user_tokens)
| Function | What it does | Transaction | Returns |
|---|---|---|---|
| find_by_email(email) | Finds the user by lowercase email | no | row or None |
| find_by_id(user_id) | Finds the user (includes is_email_verified, password_changed_at for the JWT check) | no | row or None |
| create_user_with_verification_token(name, email, password_hash, token_hash, expires_at) | Inserts user + token | YES | user row, or None if the email already exists |
| create_token(user_id, token_type, token_hash, expires_at) | Inserts a token (resend, forgot-password) | no | token row |
| get_latest_token_time(user_id, token_type) | When the newest token of that type was created (1-per-minute rule) | no | time or None |
| verify_email_with_token(token_hash) | Uses the token and marks the user verified | YES | user row, or None if bad/used/expired |
| reset_password_with_token(token_hash, new_password_hash) | Uses the token, saves the new password, sets password_changed_at = now(), marks the user's other unused reset tokens used | YES | user id, or None |
| update_password(user_id, new_password_hash) | Saves the new password and sets password_changed_at = now() (one UPDATE statement) | no | nothing |
| delete_old_tokens(older_than) | Housekeeping (cleanup job) | no | count |

Details:
- A token can be used only once, even if clicked twice at the same moment. The repository uses ONE conditional
  update: "mark this token used only if it is still unused and not expired, and give me the user id".
  Only one of two simultaneous clicks wins; the other gets None.
- Duplicate email: the unique rule in the database is the real guard. The repository catches the
  violation and returns None, so the service never sees database errors.

### ProjectRepository (projects)
| Function | What it does | Transaction | Returns |
|---|---|---|---|
| create_if_under_limit(user_id, data, limit) | Locks the user's row, counts the user's projects, inserts only if under the limit | YES | row, or None if the limit is reached |
| list_by_user(user_id, limit, offset) | The user's projects, newest first, plus total count | no | rows + total |
| find_by_id_and_user(project_id, user_id) | One project if it is theirs | no | row or None |
| update(project_id, user_id, changed_fields) | Saves only changed fields, sets updated_at | no | row or None |
| delete_with_runs(project_id, user_id) | Locks the project row, refuses if it has an active run, collects run ids and package paths, deletes (database cascades) | YES | outcome: not_found / has_active_run / deleted (+ run ids, package paths) |

Details:
- The limit of 20 per user: "check the count, then insert" is two steps, and two simultaneous requests could
  both pass at 19 and make it 21 (a race condition). Locking the user's row inside the transaction makes the
  second request wait, so it counts AFTER the first finished. The service owns the rule (passes the limit from
  config and maps None to PROJECT_LIMIT_REACHED). "One active run per user" needs no lock: it is a database
  rule (partial unique index), because "at most one" can be written as a rule.
- Delete: locking the project row means nobody can start a new run on it while we check and delete.

### RunRepository (runs)
| Function | What it does | Returns |
|---|---|---|
| create_run(...) | Inserts the run; if the one-active-run rule rejects it | row, or None |
| find_by_id_and_user(run_id, user_id) | For routes | row or None |
| find_by_id(run_id) | For background code only | row or None |
| list_by_user(user_id, filters, limit, offset) | History list + total | rows + total |
| transition_status(run_id, allowed_from, to_status, extra_fields) | Changes status ONLY IF the current status is in allowed_from | row or None |
| set_workspace_ready(run_id, path, source_commit) | Records the copy details | row |
| increment_counter(run_id, counter) | +1 on reject_count / failed_test_count / hint_count in one statement (counter name from a fixed list) | new value |
| add_tokens(run_id, n) | Adds to total_tokens | |
| finish_run(run_id, final_status, stop_reason, final_summary) | Sets final status and finished_at, only if not already finished | row or None |
| mark_workspace_deleted(run_id) | Sets workspace_deleted_at | |
| mark_applied_to_source(run_id) | Sets applied_to_source_at, only if status is succeeded and it is still empty | row or None |
| delete_run(run_id, user_id) | Locks the run, refuses if active, collects the package path, deletes (YES, transaction) | outcome: not_found / active / deleted (+ package path) |
| find_stuck_waiting(older_than) | Runs waiting over 1 hour (timeout job) | rows |
| find_interrupted_runs() | Runs left in started/searching/applying/testing (startup recovery) | rows |
| find_old_workspaces(older_than) | Temporary copies older than 24 hours and not deleted | rows |

Details:
- transition_status is the guard against races. "Cancel" and "Accept" can arrive at the same moment; each says
  "change the status only if it is currently X". Only one wins; the other gets None, which the service turns
  into RUN_STATE_INVALID.
- Every update sets updated_at = now(). The timeout job relies on runs.updated_at (while a run waits, nothing
  else touches it).

### AttemptRepository (attempts, file_edits)
| Function | What it does | Returns |
|---|---|---|
| create_attempt_with_edits(run_id, reason, explanation, edits) | Works out the next attempt number, inserts the attempt and all its edits (YES, transaction) | attempt with edits |
| find_waiting_attempt(run_id) | The attempt currently waiting for approval | row or None |
| record_approval_decision(run_id, attempt_id, decision, feedback) | ONE transaction: only if BOTH the run and the attempt are waiting, save the decision, move the run's status (applying on accept, searching on reject), add 1 to reject_count on reject | attempt + run, or None |
| mark_attempt_status(attempt_id, status) | applied / tests_passed / tests_failed with the matching time; set_reverted_at(attempt_id) records an undo | row |
| list_with_edits(run_id) | All attempts with their edits (run detail, package) | rows |
| find_successful_edits(run_id) | Edits of the attempt that passed the tests (apply-to-source) | rows |

### EventRepository (run_events)
| Function | What it does | Returns |
|---|---|---|
| add_event(run_id, attempt_id, type, message, details) | Locks the run row, takes the next sequence number, inserts (YES, transaction) | row |
| list_after(run_id, after_sequence, limit) | Events after a number (live stream) | rows |

Why the lock: several things can add events to one run at once (agent, approval click, cancel). Locking the run
row first stops two events from getting the same number (same trick as the project limit).

### TestResultRepository (test_results)
create(...), list_by_run(run_id), find_baseline(run_id).

### PackageRepository (packages)
create(run_id, path, size, expires_at), find_by_run(run_id), find_expired(limit), mark_deleted(package_id).

### Words to remember
- Run = the whole job. Attempt = one proposed fix (one try). Event = one line in the run's diary.
- A rejected fix is also an attempt. Limits count separately: at most 3 rejects and 3 failed-test tries per run.

---------------------------------------------------------------------------------------------------
## Layer 6: Agent  (DONE)

The agent is the LangGraph workflow. It lives in its own module and is started by the services through a
small interface (AgentRunner). Its steps call our services (which call repositories), so the layer rules
stay intact. LangGraph never reads our tables; our code never reads LangGraph's tables.

### 6.1 Big decisions
1. FIXED FLOW with one free part. The graph decides the order of steps. Only the "investigate" step lets the
   LLM move freely (with a step limit). This follows the rule from the start: tools stay dumb, the graph controls the flow.
2. The LLM has only READ-ONLY tools: search_files, search_in_file, read_file_lines. Writing files, asking for
   approval, and running tests are graph steps, never LLM tools. So the LLM cannot write or run anything on its own.
3. UNDO IS ALWAYS GIT. If the copy is not a git repo, the agent runs `git init` and makes a first commit inside
   the temporary copy (the user's original is never touched). Undo = one git command. The patch comes from `git diff`.
   The undo_method column was removed.
4. Counters (rejects, failed tests, hints) live in the database (runs table), not in the state, because web
   requests and jobs also read/change them.
5. The investigation chat is NOT kept in the state; a compact `findings` list is kept instead.
6. LangGraph snapshots are deleted as soon as the run ends (nothing left to resume; keeps user code out of the database).
7. Each pause is split in two tiny steps: a "request" step that does the side effects, and a "wait" step that only
   pauses. (As far as we know, when LangGraph resumes, the step that paused starts again from its first line, so
   side effects before the pause would run twice. To be verified at implementation.)

### 6.2 The agent workflow architecture (the graph)

```
prepare_workspace
      |
run_baseline_tests
      |
investigate ---(nothing found)---> request_hint -> wait_for_hint ---(user hint)---> back to investigate
      | (found)                         '-(hint limit reached)--> finish
propose_fix <------------------------------------------------------+
      |                                                             |
check_proposal --(check failed, under 3)--> propose_fix             |
      |        '--(3 failed checks)--> finish                       |
      | ok                                                          |
request_approval -> wait_for_approval                               |
      |  (graph then reads the decision from the database)          |
      |-- rejected, under limit (feedback) --------------------------+
      |-- rejected, limit reached --> finish                        |
      '-- accepted                                                  |
apply_edits                                                         |
      |                                                             |
run_tests_after_fix                                                 |
      |                                                             |
decide_next --(failed: undo, under limit)---------------------------+
      |-- passed --> finish (succeeded)
      '-- failed, limit reached --> finish (failed)
```

Mapping from the original design to the graph:
| Original design | In the graph |
|---|---|
| Human sends bug + codebase to the LLM | The run starts; prepare_workspace makes the copy; the bug text goes into the state |
| (not in the original design) | run_baseline_tests |
| LLM calls Tool 1, then Tool 2 | investigate (search_files, search_in_file, read_file_lines) |
| LLM sends files, lines, bug to Tool 3 | propose_fix + check_proposal (nothing written yet) |
| Tool 3 calls Tool 4; Tool 4 tells Tool 3 about approval | request_approval + wait_for_approval (pause/resume). No tool calls another tool |
| Tool 3 makes the change | apply_edits (after approval only) |
| LLM calls Tool 5 | run_tests_after_fix |
| Success: end. Failure: start again | decide_next: pass -> finish; fail -> undo, back to propose_fix with the error, up to the limit |

On failure the agent does NOT restart from the codebase. All memory stays in the state/database.

### 6.3 The state (the backpack)
Rules: only small plain data; no secrets; one source of truth (if our tables hold a fact, the state keeps only a pointer).
| Group | Field | Why |
|---|---|---|
| Setup | run_id, user_id | Labels so our steps know which rows to look up in OUR tables (not a link to LangGraph) |
| | bug_description, language, test_command | |
| | workspace_path | Where the temporary copy is |
| Investigation | hints | The user's extra hints, in order |
| | search_terms_tried | So the LLM does not repeat searches |
| | findings | Short list of useful places: file, line range, a few lines of code, a note |
| | investigate_steps_used | For the 15-call limit (resets after each hint) |
| Baseline | baseline_summary | Passed/failed + short error excerpt (a failing test name is a strong clue) |
| Draft | draft_edits, draft_explanation | The unchecked proposal; NOT saved to the database until it passes check_proposal |
| | proposal_checks_used, check_problems | For the "3 failed checks in a row" limit and the feedback to the LLM |
| Current try | current_attempt_id | Pointer to the attempt in our database (the full edits live there) |
| | reject_feedback, last_test_failure | Used by propose_fix for the next try |
| Flow control | outcome | Small word set by a step so the graph knows which arrow to follow (found, not_found, approved, rejected, passed, failed, limit_reached) |
| Ending | final_status, stop_reason, final_summary | Passed to finish |
NOT in the state: counters (database), the LLM chat, whole files, full test logs (saved as files and put in the package),
the Groq client object (built from settings in each step).
Two ids, two jobs: `thread_id` (set to the Run id when starting/resuming the graph) is the ONLY link to LangGraph's
tables; `run_id` inside the state is a label for our own steps. The state is for the agentic flow only, not the app flow.

### 6.4 The steps, one by one
Every step also adds Run Events (the live diary lines) through our services.
Folder layout for a run:
```
workspaces/<user_id>/<run_id>/
    code/        the temporary copy of the user's code
    artifacts/   logs, patch (these go into the package)
```

1. prepare_workspace
   - Reads: the run's snapshot (source type, source, branch) and the zip file, if any.
   - Does: builds code/. GitHub: clone the public repo (chosen or default branch), record the commit. Local path: copy the
     folder, skipping big generated folders (target, build, node_modules, __pycache__, .venv). Zip: unpack safely (refuse
     paths that escape the folder, refuse a zip that expands too much). Refuse a project over a size limit. Then make
     sure it is a git repo (git init + first commit if needed).
   - Writes: workspace_path into the state; copy details (path, source commit) into the run.
   - Arrow: run_baseline_tests. Any failure (clone failed, folder missing, too big, bad zip) -> finish with a clear reason.

2. run_baseline_tests
   - Reads: workspace_path, language, test_command.
   - Does: TestRunner starts a Docker container (image by language) with code/ mounted; time/CPU/memory limits, output cap,
     no secrets passed in. Full log goes to artifacts/.
   - Writes: a baseline Test Result row; baseline_summary in the state.
   - Arrow: investigate. If the tests cannot run at all (wrong test command, broken build, Docker problem, timeout) -> finish with a
     clear message ("your tests could not run, check the test command"), because nothing could be verified.

3. investigate (the LLM with its three tools)
   - Reads: bug text, hints, baseline_summary, search_terms_tried.
   - Does: the LLM loops with search_files, search_in_file, read_file_lines. All tools are limited to code/, skip binaries and
     ignored folders, and return size-limited results. LIMIT: 15 tool calls in total (all three tools together) per pass; the count
     starts again after each hint.
   - Writes: findings, search_terms_tried, investigate_steps_used, token usage.
   - Arrow: found -> propose_fix. Nothing found -> request_hint.

4. request_hint + wait_for_hint
   - request_hint: if hint_count is already 3 -> finish (failed, no_match_after_3_hints). Otherwise add 1 to hint_count, set the run
     to waiting_for_hint, add the "please give a hint" event.
   - wait_for_hint: only pauses. On resume it adds the user's hint to `hints` and puts the status back to searching.
   - Arrow: back to investigate.

5. propose_fix
   - Reads: bug text, hints, findings, baseline_summary, reject_feedback, last_test_failure, and PREVIOUS ATTEMPTS from the
     database (what was tried and why it did not work) so the LLM does not repeat itself.
   - Does: one LLM call with NO tools. The answer must come in a fixed format: an explanation plus a list of edits (file,
     line range, old_code, new_code, short reason).
   - Writes: draft_edits in the state (not in the database yet).
   - Arrow: check_proposal.

6. check_proposal
   - Does: checks every edit: file is inside code/, line range valid, old_code EXACTLY matches those lines, edits in the same
     file do not overlap. Sanity limits per attempt: at most 10 edits and at most 300 changed lines (a guard rail so the LLM
     cannot rewrite the project; a normal fix is tiny).
   - If fine: saves the Attempt and its edits (bottom-up order per file) to the database, puts current_attempt_id into the
     state, clears the draft -> request_approval.
   - If not: sends the problems back to propose_fix WITH the real lines from the file. After 3 failed checks in a row ->
     finish (failed, proposal_invalid).

7. request_approval + wait_for_approval
   - request_approval: moves the run to waiting_for_approval and adds the "approval requested" event. The screen shows
     old/new code by loading the attempt.
   - wait_for_approval: only pauses. When the user clicks Accept/Reject, the SERVICE has already recorded the decision (one
     transaction) and resumed the graph. After resuming, the graph reads the decision FROM THE DATABASE:
     accepted -> apply_edits; rejected, under 3 -> propose_fix with the feedback; rejected, limit reached -> finish
     (stopped, reject_limit_reached).

8. apply_edits
   - Reads: the edits of current_attempt_id from the database.
   - Does: computes the new content of every file IN MEMORY first, re-checking old_code with the same checker as step 6. Only if all edits
     pass does it write the files. All or nothing.
   - Writes: attempt status applied; event "Applied N edits in M files".
   - Arrow: run_tests_after_fix. A failure here should be nearly impossible (only the agent touches code/); if it happens the run
     ends (failed, apply_failed).

9. run_tests_after_fix
   - Does: run status -> testing; tests run in Docker like the baseline; full log saved in artifacts/.
   - Writes: an after_fix Test Result linked to the attempt; last_test_failure excerpt in the state.
   - How results count: tests fail or code no longer compiles -> failed attempt. Tests time out -> failed attempt (the fix may
     have made an endless loop). Docker could not start at all -> the run ends with test_runner_error. This is a technical problem, NOT
     counted in failed_test_count; the attempt stays "applied" (applied but never tested).
   - Arrow: decide_next.

10. decide_next
   - Passed: attempt -> tests_passed; go to finish (succeeded).
   - Failed: attempt -> tests_failed; UNDO with git and check the copy is clean (else finish: failed, undo_failed); record
     reverted_at; add 1 to failed_test_count (returns the new value). Under 3 -> run back to searching, go to propose_fix with the
     test errors. Reached 3 -> finish (failed, tests_failed_3_times).

11. finish (one shared "finalize" service function, because a cancel and the 1-hour timeout also end runs while the graph is NOT running)
   1. Builds the package: succeeded run -> changes.patch (git diff) + the fixed files; other endings -> a readable list of what was
      tried; plus the test logs (baseline and each attempt) and summary.md (status, attempts, results, and the label "tests passed,
      but no test covers this bug, please verify the fix yourself" when the baseline had already passed).
   2. Zips it, saves it under the packages folder, adds the Package row (expires in 7 days).
   3. Saves final status, stop reason, summary, finished_at on the run; adds a "Run finished" event. The summary is built from a
      template (no extra LLM call).
   4. Deletes the temporary folder and records workspace_deleted_at.
   5. After the graph has ended, the background runner that started it deletes the run's LangGraph snapshots (it cannot do that from
      inside the graph that is still running).
   If building the package fails, the run still finishes; the user can still apply the changes from the database records.

Final status meaning:
- succeeded: tests passed.
- stopped: ended by the user's action or inaction (cancelled_by_user, timed_out, reject_limit_reached).
- failed: everything else (tests_failed_3_times, proposal_invalid, no_match_after_3_hints, clone_failed, baseline could not run,
  and technical problems like test_runner_error, apply_failed, undo_failed, llm_error, internal_error, server_restarted).

### 6.5 Helper components
1. AgentRunner: start(run_id) builds the first state and starts the graph as a background task (thread_id = run_id). resume(run_id)
   continues a paused graph (called by services after an approval or a hint). cancel(run_id) stops the running task. After the graph
   ends (normally, by an unexpected error, or by cancel) it finalizes the run on errors, deletes the LangGraph snapshots, and forgets
   the task. One task per run, kept in memory. Services call the runner through a small interface set up at startup, and the steps call
   services; this avoids a circular dependency.
2. WorkspaceManager: builds code/ for GitHub/local/zip sources (size limit, safe unzip); git init + first commit when needed (line-ending
   conversion off so undo restores files byte for byte); the PATH GUARD (every path must stay inside code/); undo; diff; folder deletion.
3. CodeTools (the three read-only LLM tools): search_files (case-insensitive over file contents and names, with hit counts; includes config
   files because bugs often live in files like .properties), search_in_file (matching lines with a little context), read_file_lines
   (exact lines). All have size caps and skip binaries and ignored folders.
4. TestRunner (Docker): returns passed / failed / timed_out / error, plus exit code, full log, duration, image used. One image per language
   (config). Limits: time, memory, CPU, output size. No host secrets. Containers are labelled with the run id so a cancel can stop them.
   "Test command not found" counts as error (could not run), not as a failing test.
5. Finalizer (the finish logic above) and a small event helper emit(...) used by every step. Rule: every change of the run's status
   also adds a status_changed event (details: status, and stop_reason when the run ended). The "Run finished" event in step 11 is the
   status_changed event with the final status; it is always the last event of the run.
6. LLM client: builds the Groq client from settings for each call (investigate with the 3 tools; propose_fix with the fixed answer format).
   On Groq errors (rate limit, timeout) it retries up to 3 times with waiting, then the run ends (failed, llm_error). Adds token usage to
   the run. Prompts are plain text files in their own folder. Prompt injection (text in the user's code telling the LLM to misbehave) is
   limited by design: read-only tools, a fixed flow, and nothing is written without the user's approval.

Agent folder layout:
```
agent/    graph.py  state.py  runner.py  nodes/  tools/  llm/ (client, prompts/)
runners/  workspace_manager.py  test_runner.py  finalizer.py
```

### 6.6 Default settings (all changeable in config)
Investigate limit 15 tool calls per pass | proposal checks 3 in a row | at most 10 edits and 300 changed lines per attempt |
test time limit 10 minutes | container limits 2 CPUs and 2 GB memory | test output cap 1 MB | file read cap 1 MB |
at most 200 lines per read call | search returns at most 20 files | Groq retries 3 | caps: 3 rejects, 3 failed-test tries, 3 hints.

### 6.7 To verify or decide at implementation
- LangGraph details: the pause/resume behaviour (a paused step restarting from its first line), how to delete a thread's data, the
  Postgres checkpointer setup, exact class names.
- Groq model quality at fixing code (we only confirmed tool calling is supported); keep the model swappable.
- Size limit number for the project copy; the exact rule for comparing old_code (line endings, trailing spaces).
- Dependency cache for Maven/pip inside Docker: a cache shared between users could be poisoned by one user's build. Fine for local mode;
  deployed mode should use a cache per user.
- Docker Desktop on Windows: how folders are mounted into containers.

---------------------------------------------------------------------------------------------------
## Layer 7: Core / Config  (DONE)

The shared tools every other layer uses.

1. Settings. One place that reads the .env file: app mode (local or deployed), database URL, Groq key and model name, JWT secret and
   lifetime (24 hours), email settings, folders for workspaces and packages, Docker images, and all the limits we agreed. Checked at
   startup: the app REFUSES to start with a clear message if something is missing. Secrets are never logged. The repo gets a
   .env.example with fake values; the real .env stays out of git.
2. Database pool. A pool of reusable async connections (psycopg 3), created at startup and closed at shutdown. Repositories borrow a
   connection per call or per transaction. LangGraph's checkpointer uses its OWN connection with the settings it needs.
3. Security helpers.
   - bcrypt for passwords. It is slow on purpose, so in async code it runs in a separate thread (otherwise one login would freeze every
     other request for a moment).
   - JWT: create and verify (claims: user id, issue time, expiry 24 hours).
   - Email tokens: made with Python's secure random function, stored as a SHA-256 hash (a fast hash is fine for long random tokens).
4. The login check (get_current_user). Runs before every protected route: verifies the JWT, loads the user, rejects it if it was
   issued before users.password_changed_at, and hands the user to the controller. Failure = 401.
5. Rate limiting. In-memory counters per IP and email for sensitive routes. Defaults: login 10 attempts per 15 minutes, register 5 per
   hour, forgot-password 3 per hour, resend-verification 1 per minute, change-password 5 per 15 minutes per user. Over the limit = 429 with a "try again after" time. In-memory works
   for one backend process; a shared store (like Redis) can come when several processes are deployed.
6. Error handling. Any unexpected error is logged with a request id and answered with a plain 500 in our { error: {...} } shape, with no
   internal details. Validation errors use the same shape (HTTP 422, code VALIDATION_ERROR, the message lists the bad fields). FastAPI's
   own default 422 format is replaced by this.
7. Logging. The backend's own diary for developers (not what the user sees; Run Events are the user's diary). Structured lines with
   request id, user id, run id. Never written: passwords, tokens, API keys, or the user's code.
8. Mailer. One send_email(to, subject, body) function with two versions chosen by a setting: "console" (development: prints the email
   text and link in the terminal) and "smtp/provider" (real sending, chosen when deploying).
9. Startup and shutdown, in order: load settings -> create the DB pool -> set up the LangGraph checkpointer -> check Docker is
   reachable (warn if not) -> startup recovery -> start the background jobs (timeout sweeper, package cleanup, workspace cleanup, token
   cleanup) -> create the AgentRunner and connect the services. The jobs are safe to run twice because they use conditional updates.
10. Health route: GET /api/v1/health (public).
11. CORS. The allowed website addresses come from settings. Allowed request headers: Authorization, Content-Type, Last-Event-ID
    (the browser sends a preflight check because of these custom headers, so they must be listed).

### Decisions in this layer
- The JWT travels in the Authorization header on every request ("Bearer <token>"). The live stream (SSE) is opened by the frontend with
  fetch (which can send the header), NOT with the browser's built-in EventSource tool (which cannot send headers). What the stream shows
  does not depend on this; only the first request of the stream carries the JWT.
- Table changes (the numbered .sql files) are run MANUALLY with a separate command from the start, in both local and deployed mode.
  The app does not change tables on startup.
- Rate limit defaults as listed above.
