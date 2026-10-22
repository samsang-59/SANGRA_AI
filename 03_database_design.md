# SANGRA_AI - Step 3: Database Design

(Written in simple language. Date: 2026-10-20)
Method used: entities -> attributes -> tables -> primary/foreign keys -> constraints -> how tables link.

## 1. Big picture

- Database: PostgreSQL. Driver: psycopg 3 (async). Raw SQL, written only inside repositories.
- Table changes are done with plain numbered .sql migration files.
- We do NOT store code copies in the database. We store small records only: users, runs, bug text,
  diffs, results, and where the download package is.
- LangGraph keeps its own tables (created by its setup() call). We do not design them.
  The link is only the Run ID used as the LangGraph thread ID (written as text, for example "42").
  There is NO foreign key between our tables and LangGraph's tables.

## 2. Entities (the "things" we remember)

User, User Token, Project, Run, Attempt (approval is merged into it), File Edit, Run Event,
Test Result, Package.

Reasons for some choices:
- Approval is merged into Attempt: each Attempt gets exactly one answer from the human,
  so a separate table would always have one row per Attempt.
- Package is a separate table: a Run is a permanent record, a Package is a temporary file that is
  deleted after 7 days. Keeping them apart keeps the Run table clean and makes cleanup simple.
- Runs keep a snapshot (copy) of the Project settings they used. If the Project changes later,
  old runs still show what really happened.

## 3. IDs

- Every table has `id bigint GENERATED ALWAYS AS IDENTITY` (normal counting IDs: 1, 2, 3...).
- Because IDs can be guessed, EVERY query for a project, run, or anything under them must also
  filter by the logged-in user. If it is not theirs, answer "not found".

## 4. Tables, columns, and rules

Every table also has: `id` (primary key) and `created_at timestamptz DEFAULT now()`.
"null" means the column may be empty. All times are `timestamptz`.

### users
| Column | Type | Rules |
|---|---|---|
| name | text | NOT NULL |
| email | text | NOT NULL, UNIQUE (stored lowercase) |
| password_hash | text | NOT NULL (bcrypt) |
| is_email_verified | boolean | NOT NULL, DEFAULT false |
| password_changed_at | timestamptz | NOT NULL, DEFAULT now(). A JWT issued before this time is rejected, so a password reset or a password change logs out old tokens |
| updated_at | timestamptz | |

### user_tokens  (email verification and password reset links)
| Column | Type | Rules |
|---|---|---|
| user_id | bigint | FK -> users.id, ON DELETE CASCADE |
| token_type | text | CHECK in ('email_verification', 'password_reset') |
| token_hash | text | NOT NULL, UNIQUE (only a hash is stored, never the real token) |
| expires_at | timestamptz | NOT NULL |
| used_at | timestamptz null | empty until the link is used |

### projects
| Column | Type | Rules |
|---|---|---|
| user_id | bigint | FK -> users.id, ON DELETE CASCADE |
| name | text | NOT NULL (same name may repeat for one user) |
| language | text | CHECK in ('java', 'python') |
| source_type | text | CHECK in ('github', 'local_path', 'zip') |
| source_reference | text null | must be filled unless source_type is 'zip' |
| branch | text null | empty means the repo's default branch (not hardcoded as "main") |
| test_command | text | NOT NULL |
| updated_at | timestamptz | |

### runs
| Column | Type | Rules |
|---|---|---|
| user_id | bigint | FK -> users.id, ON DELETE CASCADE |
| project_id | bigint | FK -> projects.id, ON DELETE CASCADE (deleting a project deletes its runs) |
| bug_description | text | NOT NULL |
| status | text | CHECK in ('started','searching','waiting_for_hint','waiting_for_approval','applying','testing','succeeded','failed','stopped') |
| language | text | snapshot, CHECK in ('java','python') |
| test_command | text | snapshot, NOT NULL |
| branch | text null | snapshot |
| source_type | text | snapshot, CHECK in ('github','local_path','zip') |
| source_reference | text null | snapshot |
| source_commit | text null | for GitHub: the exact commit that was cloned |
| llm_model | text | NOT NULL (Groq model used) |
| workspace_path | text null | where the temporary copy lives |
| workspace_deleted_at | timestamptz null | |
| stop_reason | text null | why the run ended without success |
| reject_count | integer | NOT NULL, DEFAULT 0, CHECK >= 0 |
| failed_test_count | integer | NOT NULL, DEFAULT 0, CHECK >= 0 |
| hint_count | integer | NOT NULL, DEFAULT 0, CHECK >= 0 |
| final_summary | text null | the agent's closing explanation |
| total_tokens | integer null | LLM tokens used in this run |
| applied_to_source_at | timestamptz null | when the user clicked "Apply to my folder" (empty = not yet) |
| started_at | timestamptz | |
| finished_at | timestamptz null | |
| updated_at | timestamptz | |

Special rule: ONE ACTIVE RUN PER USER. A partial unique index on `user_id` for rows whose status is
one of (started, searching, waiting_for_hint, waiting_for_approval, applying, testing).
The database itself blocks a second active run, even if two requests arrive at the same moment.

The caps (3 rejects, 3 failed-test tries, 3 hints) are settings in code, not in the database.

### attempts  (one proposed fix; approval is part of it)
| Column | Type | Rules |
|---|---|---|
| run_id | bigint | FK -> runs.id, ON DELETE CASCADE |
| attempt_number | integer | CHECK >= 1, UNIQUE together with run_id |
| reason_started | text | CHECK in ('first', 'after_reject', 'after_test_failure') |
| explanation | text | the agent's plain-English reason for the fix |
| status | text | CHECK in ('proposed','waiting_for_approval','rejected','applied','tests_passed','tests_failed'). An undone attempt keeps status tests_failed; the undo is recorded in reverted_at. 'applied' with no test result means applied but never tested (for example Docker could not start) |
| decision | text null | CHECK in ('accepted', 'rejected') |
| decision_feedback | text null | the user's reason when rejecting |
| decided_at | timestamptz null | |
| applied_at | timestamptz null | |
| reverted_at | timestamptz null | |
| updated_at | timestamptz | |

### file_edits  (one change inside an attempt; replace-only)
| Column | Type | Rules |
|---|---|---|
| attempt_id | bigint | FK -> attempts.id, ON DELETE CASCADE |
| file_path | text | NOT NULL |
| start_line, end_line | integer | CHECK start_line >= 1 AND end_line >= start_line |
| old_code | text | NOT NULL |
| new_code | text | NOT NULL (empty string means delete) |
| edit_order | integer | UNIQUE together with attempt_id |
| explanation | text null | |

How edits behave:
- Every edit is "replace this line range with this new code".
- Edits in the same file are applied from the bottom up, so line numbers do not shift.
- Before writing, the apply tool checks that old_code still matches the lines. If not, it refuses
  (this protects against the LLM giving wrong line numbers).
- All or nothing: if one edit fails, the whole attempt is undone and marked failed.

### run_events  (the step log used for live progress and history)
| Column | Type | Rules |
|---|---|---|
| run_id | bigint | FK -> runs.id, ON DELETE CASCADE |
| attempt_id | bigint null | FK -> attempts.id, ON DELETE CASCADE |
| sequence_number | integer | UNIQUE together with run_id (1, 2, 3 ... inside one run) |
| event_type | text | CHECK in ('status_changed','tool_called','tool_result','hint_requested','hint_received','approval_requested','approval_decided','tests_started','tests_finished','error') |
| message | text | the line shown on screen |
| details | jsonb null | SHORT summaries only (file names, counts, line ranges), never full file contents |

Use: if the browser disconnects, it asks "send everything after event 17".
Event rules: every status change of a run adds a status_changed event with details {"status": ..., "stop_reason": ... (only at the end)}.
The last event of a run is the status_changed event with a final status. attempt_id is filled for events about one proposal
(for example approval_requested), so the screen knows which attempt to load.
We do NOT store the raw prompts and replies between our backend and Groq.

### test_results
| Column | Type | Rules |
|---|---|---|
| run_id | bigint | FK -> runs.id, ON DELETE CASCADE |
| attempt_id | bigint null | FK -> attempts.id, ON DELETE CASCADE, UNIQUE (one after-fix result per attempt) |
| test_type | text | CHECK in ('baseline', 'after_fix') |
| status | text | CHECK in ('passed', 'failed', 'error', 'timed_out') |
| exit_code | integer null | |
| log_excerpt | text | cut to a size limit chosen at implementation; must be long enough to show test names and results |
| duration_ms | integer | CHECK >= 0 |
| docker_image | text | |
| started_at, finished_at | timestamptz | |

Rules: a baseline row must have NO attempt, an after_fix row MUST have one.
Only one baseline per run (partial unique index).
The full test log goes into the download package.

### packages
| Column | Type | Rules |
|---|---|---|
| run_id | bigint | FK -> runs.id, ON DELETE CASCADE, UNIQUE (one package per run) |
| storage_path | text | NOT NULL |
| size_bytes | bigint | CHECK >= 0 |
| expires_at | timestamptz | CHECK later than created_at (created + 7 days) |
| deleted_at | timestamptz null | filled when the cleanup job deletes the file |

## 5. How the tables link

```
users ─┬─< user_tokens
       ├─< projects ──< runs
       └─< runs
runs ─┬─< attempts ─┬─< file_edits
      │             └── 0..1 test_result (after_fix)
      ├─< run_events   (an event may point to an attempt)
      ├── 1 baseline test_result
      └── 0..1 package
```
(`─<` means "has many".)

Deleting: user deleted -> everything under them goes. Project deleted -> its runs go. Run deleted -> its
attempts, edits, events, results, and package row go. Because LangGraph's tables have no foreign key,
the service must ALSO delete that run's LangGraph data when a run is deleted.

## 6. Decisions recorded in this step

- Success = the test command exits with code 0 (all tests pass).
- If the baseline already passed, the final result carries the label: "Tests passed, but no test
  covers this bug, please verify the fix yourself".
- The human is the final judge: the result screen shows baseline output next to after-fix output.
- Later ideas (not MVP): a parsed list of test names with pass/fail, and the agent writing a new test.
- Allowed-value lists use CHECK on text (not Postgres ENUM), because the lists will change while we build.
- JWT is not stored. It lasts 24 hours. Logging out means the browser drops the token.
  users.password_changed_at lets us reject any JWT issued before the last password change,
  so a password reset or change logs out all old tokens.
- Forgot-password uses the same secret-link pattern as email verification (table user_tokens).

## 7. To do at implementation time

- Suggested indexes: runs(user_id, created_at DESC), projects(user_id), packages(expires_at) where deleted_at is empty.
- Check how to delete a LangGraph thread's data (method name and version).
- Choose token expiry times (verification link, reset link, JWT).
- Choose the log_excerpt size limit.
- Build the cleanup job (packages after 7 days; temporary workspaces after 24 hours at most).
