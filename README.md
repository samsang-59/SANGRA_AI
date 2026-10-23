# SANGRA_AI

**An AI coding agent that fixes bugs, with a human in the loop.**

You describe a bug in plain English. SANGRA_AI searches your code, finds the wrong lines, shows you the fix,
**waits for your approval**, applies it to a safe copy, and runs your tests inside Docker. If the tests fail,
it undoes the change and tries again (up to 3 times). Your original code is never touched.

> **Status:** implementation in progress, phase by phase (see [the plan](06_implementation_plan.md) and
> [Implementation progress](#implementation-progress) below). Current phase: **B7 Docker test runner** (awaiting review).

---

## Simple example

A junior developer is told: *"The summer sale shows 5% discount, it should be 10%."*
They do not read the whole codebase. They search for "discount", open only those files, find the wrong line,
ask a senior *"Can I change this?"*, make the change after approval, and run the tests.

SANGRA_AI does the same thing.

![System overview](diagrams/d1_overview.png)

---

## How it works

1. **You give** the bug in English, the code (GitHub link, zip, or local folder), the language, and the test command.
2. **Safe copy:** the agent copies the code into a temporary workspace. The original is never changed.
3. **Baseline tests:** it runs your tests once *before* changing anything, so it knows what was already failing.
4. **Investigate:** the LLM searches the code with three **read-only** tools (`search_files`, `search_in_file`, `read_file_lines`).
5. **Propose a fix:** a list of exact edits (file, lines, old code, new code). Nothing is written yet.
6. **Check:** every edit is validated (path stays inside the workspace, old code matches exactly, size limits).
7. **Pause for you:** you see all changes side by side and click **Accept** or **Reject** (with feedback).
8. **Apply and test:** accepted edits are written and the tests run in a Docker container.
9. **Decide:** tests pass → done. Tests fail → undo with git and try again with the error (max 3 tries).
10. **Finish:** you get a download package (patch, changed files, test logs, summary). In local mode you can click
    **"Apply to my folder"**.

If the agent cannot find the bug, it asks you for a hint (max 3 times).

![Agent workflow](diagrams/d3_agent.png)

---

## Key design ideas

| Idea | Why |
|---|---|
| **The graph controls the flow, not the LLM** | LangGraph decides the order of steps. The LLM only moves freely inside the "investigate" step, with a 15-call limit. |
| **The LLM can only read** | Writing files, asking for approval, and running tests are graph steps, never LLM tools. Limits the damage of prompt injection. |
| **Human approval is a pause, not a tool** | The graph saves its state, waits for you, and resumes after your answer. |
| **Work on a copy; undo is always git** | If the copy is not a git repo, the agent runs `git init` inside the copy. Undo is one git command; the patch comes from `git diff`. |
| **Tests run in Docker** | Time, CPU, memory and output limits. No secrets passed in. Bad code can only damage the box. |
| **One active run per user** | Enforced by a partial unique index in the database, so two requests at the same moment cannot both pass. |
| **Hard limits** | 3 rejects, 3 failed-test tries, 3 hints, max 10 edits / 300 changed lines per attempt. |

---

## Tech stack

| Part | Choice |
|---|---|
| Backend | Python, FastAPI (async) |
| Agent | LangGraph (with the Postgres checkpointer for pause/resume) |
| LLM | Groq (`openai/gpt-oss-120b`, set in config so it is easy to swap) |
| Database | PostgreSQL, psycopg 3 (async), raw SQL in repositories, numbered `.sql` migrations |
| Auth | JWT (24 h) + bcrypt, email verification and password reset links |
| Live progress | Server-Sent Events (read with `fetch` so the JWT header can be sent) |
| Test runner | Docker (Docker SDK for Python), one image per language |
| Frontend | React + Vite, React Router, TanStack Query |
| Testing | pytest, Vitest + React Testing Library, MSW, Playwright, Locust |

Supported project languages: **Java** and **Python**.

---

## Architecture

The backend follows a strict layer order. The agent lives in its own module and calls the services, never the database directly.

```
Router -> Validators -> Controller -> Service -> Repository -> PostgreSQL
                                         ^
                                         |
                                Agent (LangGraph graph, nodes, read-only tools)
                                Runners (workspace manager, Docker test runner, finalizer)
                                Core (settings, DB pool, JWT, bcrypt, rate limiter, mailer, logging)
```

| Diagram | Shows |
|---|---|
| [d1_overview.png](diagrams/d1_overview.png) | The whole system, with the agent inside the backend |
| [d2_request.png](diagrams/d2_request.png) | One request through the layers (starting a run) |
| [d3_agent.png](diagrams/d3_agent.png) | The agent workflow (the LangGraph graph) |
| [d4_status.png](diagrams/d4_status.png) | The life of a run (its statuses) |
| [d5_pause.png](diagrams/d5_pause.png) | Pause and resume for approval |
| [d6_er.png](diagrams/d6_er.png) | Database tables and how they link |

The same diagrams are also in [workflow.md](workflow.md) as Mermaid (rendered by GitHub).

---

## Design documents

Read them in this order:

| # | Document | What it covers |
|---|---|---|
| 1 | [01_brainstorm.md](01_brainstorm.md) | The idea, the flow, decisions, safety rules, known limits |
| 2 | [02_tech_stack.md](02_tech_stack.md) | Stack choices, Groq model notes, storage plan for code copies and packages |
| 3 | [03_database_design.md](03_database_design.md) | 9 tables, columns, constraints, and how they link |
| 4 | [04_backend_design.md](04_backend_design.md) | All 7 backend layers: routes, validation, error codes, services, repositories, the agent, core |
| 5 | [05_frontend_design.md](05_frontend_design.md) | Screens and routes, API layer, auth state, data handling, the live Run page |
| 6 | [06_implementation_plan.md](06_implementation_plan.md) | Phases B0–B11 and FE0–FE7, with functional, failure, security, concurrency, recovery and load tests for each |
| – | [workflow.md](workflow.md) | The whole system as diagrams |

---

## Roadmap

| Phase | What gets built |
|---|---|
| B0 | Foundation: settings, app skeleton, errors, logging, CORS, health |
| B1 | Database: migrations, migration runner, connection pool |
| B2 | Security helpers: bcrypt, JWT, email tokens, mailer, rate limiter |
| B3 | Auth: register, login, verify, resend, forgot, reset, change password |
| B4 | Projects CRUD |
| B5 | Runs, events, state machine, live stream (with a fake agent) |
| B6 | Workspace manager and the 3 read-only code tools |
| B7 | Docker test runner |
| B8 | LLM client and prompts |
| B9 | The LangGraph agent: all 11 steps, pause and resume, limits |
| B10 | Package, download, apply to my folder, cleanup jobs |
| B11 | End-to-end, security sweep, soak and chaos tests |
| FE0–FE7 | Frontend: setup, API layer, auth, projects, runs, live Run page, result view, polish |
| Later | MCP server: a thin wrapper around the finished agent |

A phase is done only when all of its tests pass **and** all earlier tests still pass.

---

## Implementation progress

| Phase | State | Tests |
|---|---|---|
| B0 Foundation | Done (approved) | 49 of 49 pass |
| B1 Database and migrations | Done (approved) | 93 of 93 pass |
| B2 Security helpers, mailer, rate limiter, login check | Done (approved) | 29 of 29 pass |
| B3 Auth feature (8 routes) | Done (approved) | 48 of 48 pass |
| B4 Projects | Done (approved) | 46 of 46 pass |
| B5 Runs, events, state rules, live stream (fake agent) | Done (approved) | 100 of 100 pass |
| B6 Workspace manager and the 3 read-only code tools | Done (approved) | 61 of 61 pass |
| B7 Docker test runner | Built, awaiting review | 24 of 24 pass (450 in total with B0 to B6); B7-M01 is manual (Windows) |
| B8 to B11, FE0 to FE7 | Not started | |

### Running the backend

Needs Python 3.10+ (developed on 3.13) and PostgreSQL. Docker is optional until B7 (health reports it as `unreachable`).

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env              # then put real values in .env (the app refuses the placeholder JWT secret)
.venv/bin/python -m app.migrate   # creates or updates the tables (run by hand; the app never changes tables itself)
.venv/bin/python -m app           # serves http://127.0.0.1:8000/api/v1/health
```

### Running the tests

The tests use a real PostgreSQL test database (never SQLite):

```bash
createdb sangra_test   # owned by a role that can log in with a password
export TEST_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/sangra_test
cd backend && .venv/bin/pytest -q
```

### B0: Foundation (notes)

Built: settings from `.env`, checked at startup (`app/core/config.py`); logging with request id, user id and run id,
secrets masked (`app/core/logging.py`); one error shape for every error (`app/core/errors.py`); request id and error-catching
middleware, plus the body size limit, 1 MB normally and 50 MB on the zip upload route (`app/core/middleware.py`); CORS; the DB pool
skeleton (`app/core/db.py`); `GET /api/v1/health` through router, controller, service and repository; the startup and shutdown
order (`app/main.py`).

Test results: `backend/tests/test_b0_foundation.py`. All 49 tests pass (the per-setting cases count separately).

**B0-L01 target changed from 100 ms to 250 ms (decided after review).** Test: 500 `/health` calls at the same moment against a
real server process (4-core container, one uvicorn process, a plain-socket client). All 500 answer 200; p95 is about 140 to 200 ms
across runs. A bare FastAPI app with no middleware and no database gets about 60 to 100 ms on the same machine, so the 100 ms
target was below what the framework itself can do in one process. One code fix went in first: health calls that arrive together
share one database ping and one Docker ping (p95 went from about 1,200 ms to the level above).

Decisions made where the design docs were silent (please check):
1. Code lives in `backend/` (the frontend will go in `frontend/`). The layer folders follow 04_backend_design.md.
2. Framework errors also use our error shape, with these extra codes: `METHOD_NOT_ALLOWED` (405), `PAYLOAD_TOO_LARGE` (413),
   `SERVICE_UNAVAILABLE` (503, health when the database is down) and `BAD_REQUEST` (400).
3. The health body is `{ "data": { "status": "ok" | "degraded", "database": "ok", "docker": "ok" | "unreachable" } }`.
4. Startup does not wait for the database (the pool connects in the background), so `/health` can answer 503 while it is down.
5. The connection pool is created in B0 because health needs it. B1 adds the pool tests.
6. Every response carries an `X-Request-ID` header with the id used in the logs.
7. The JWT secret must be at least 32 characters and must not be the placeholder from `.env.example` (B0-X02, B2-S03).

### B1: Database and migrations (notes)

Built: `backend/migrations/001_initial_schema.sql` (the 9 tables of 03_database_design.md with their checks and the suggested
indexes); the migration command `python -m app.migrate` (and `--status`), which keeps a `schema_migrations` table with a checksum
per applied file; the connection pool now checks a connection before handing it out.

Migration command rules: files are `NNN_name.sql`, numbered with no gaps or repeats; each file runs in one transaction (an error
rolls back the whole file and it is not recorded); a changed or missing applied file is refused; an advisory lock stops two
commands from running at the same moment; a database that cannot be reached gives a clear message and exit code 1.

Test results: `backend/tests/test_b1_database.py`, 93 tests, all pass. Load numbers (10,000 runs for 100 users, 100,000 events):
history page of one user 0.6 ms (uses `runs_user_created_idx`); events after N 0.5 ms; stuck waiting runs 0.6 ms; old workspaces
4 ms; expired packages 0.7 ms; a later migration (add a column and an index) on that data 21 ms.

Decisions made where the design docs were silent (please check):
1. `NOT NULL` is added to every column that has a fixed list of values (a `CHECK ... IN` alone lets NULL through), except
   `attempts.decision`, which the design marks as nullable. Also `NOT NULL` on `created_at`, `updated_at`, `runs.started_at`
   (all default to now()), `run_events.message`, the numbering columns, the line numbers, `packages.size_bytes` and
   `packages.expires_at`.
2. `users.email` has `CHECK (email = lower(email))`, so "stored lowercase" is guaranteed by the database.
3. `run_events.sequence_number` has `CHECK (sequence_number >= 1)` (the design numbers events 1, 2, 3 ...).
4. No indexes beyond the three the design suggests, plus the unique rules. The load tests show these are enough.
5. B1-S02 (SQL text through repositories) runs through parameterised inserts into every text column now, because no repository
   takes text yet. Each repository phase repeats it through its own functions (B3-S04 and onward).
6. The B1-L03 queries are written the way the design describes the jobs. B5 and B10 measure the real repository functions again.

### B2: Security helpers, mailer, rate limiter, login check (notes)

Built:
- `app/core/security.py`: bcrypt hashing and checking in their own threads; JWT create and verify (HS256 only;
  claims `sub`, `iat`, `exp` = 24 hours; nothing else); email tokens (32 random bytes from `secrets`, stored as SHA-256);
  a dummy hash so an unknown email costs the same time as a wrong password.
- `app/core/auth.py`: `get_current_user`, the login check for protected routes (one `Bearer` header, valid JWT, user still
  exists, JWT not issued before the last password change). Every failure is 401 `UNAUTHORIZED`.
- `app/core/mailer.py`: console mailer (prints the email and its link in the terminal) and SMTP mailer, picked by
  `MAILER_MODE`; `deliver()` never raises, logs a failure without the email body, and returns False.
- `app/core/rate_limit.py`: in-memory sliding-window limiter with the default limits from the design (login 10 per 15 min,
  register 5 per hour, forgot-password 3 per hour, resend 1 per minute, change-password 5 per 15 min). Over the limit:
  429 `RATE_LIMITED` with "Try again in N seconds" and a `Retry-After` header. Old entries are swept away.
- `app/validators/common.py`: the password rules (8+ characters, a letter and a digit, at most 72 bytes, checked first).
- `app/core/clock.py`: the app clock, replaced by a fake clock in the expiry and rate-limit tests.

Test results: `backend/tests/test_b2_security.py`, 29 tests, all pass. Numbers on this 4-core container:
unknown email 275 ms vs wrong password 272 ms (B2-S01, ratio 1.01); slowest `/health` during 20 parallel hashes 12 ms (B2-C01);
100 parallel hashes at cost 12 take about 6.9 s, so this machine does about **14 logins per second** (B2-L02);
10,000 rate-limit keys use about 8 MB, about 0.4 MB after the sweep (B2-L01).

Fixed during this phase: B2-C01 first failed (1,142 ms). bcrypt ran on Python's shared thread pool, so other thread work
(the Docker check of `/health`) queued behind the hashes. bcrypt now has its own threads (one per CPU).

Decisions made where the design docs were silent (please check):
1. Gaps #1 is implemented as proposed: the JWT issue time is compared with `password_changed_at` rounded down to whole seconds.
2. The login rate limit counts both the IP and the email; a request is refused if either is over the limit. A refused
   attempt is not counted. (Wired into the routes in B3.)
3. The console mailer prints to the terminal (stdout), not to the log, because the log must never hold tokens.
4. SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `MAIL_FROM`) were added for
   `MAILER_MODE=smtp`; sending through a real SMTP server is a manual check for later (the provider is not chosen yet).
5. A login password longer than 72 bytes is never a match, and it still costs one bcrypt check (so timing tells nothing).
6. The 2 warnings in the test run come from the Docker SDK: it does not close its socket when the Docker socket is missing.
   The socket is freed right away when the object goes, so nothing leaks.

### B3: Auth feature (notes)

Built: the 8 auth routes through all layers: `routers/auth.py` -> `validators/auth.py` -> `controllers/auth_controller.py`
-> `services/auth_service.py` -> `repositories/user_repository.py`.

| Route | Answer |
|---|---|
| `POST /auth/register` | 201 `{ data: { user, token } }`; a verification email with `FRONTEND_URL/verify-email?token=...` |
| `POST /auth/login` | 200 `{ data: { user, token } }`, or 401 `INVALID_CREDENTIALS` (same message for unknown email and wrong password) |
| `POST /auth/verify-email` | 200 `{ data: { message } }`, or 400 `TOKEN_INVALID_OR_EXPIRED` |
| `POST /auth/resend-verification` (protected) | 200 (also "already verified"), or 429 `TOO_SOON` within 60 seconds |
| `POST /auth/forgot-password` | always 200 "If this email exists, we sent a link ..." |
| `POST /auth/reset-password` | 200, or 400 `TOKEN_INVALID_OR_EXPIRED`; all other unused reset links of the user stop working |
| `POST /auth/change-password` (protected) | 200 (no new JWT), 400 `WRONG_OLD_PASSWORD` / `NEW_PASSWORD_SAME_AS_OLD` |
| `GET /auth/me` (protected) | 200 the user (`id, name, email, is_email_verified, created_at`; never the password hash) |

Rate limits are checked in the controllers (the limiter lives in core): login by IP and email, register by IP, forgot-password
by IP and email, resend and change-password by user.

Test results: `backend/tests/test_b3_auth.py`, 48 tests, all pass. Numbers: 50 logins at once at bcrypt cost 12 took 3.8 s with
`/health` at most 60 ms meanwhile (B3-L01); `/auth/me` p95 2.4 ms over 1,000 calls (B3-L02); 500 parallel `/auth/me` in 0.8 s
with no pool errors (B3-L03); forgot-password answers in 4.1 ms for a known email vs 2.0 ms for an unknown one (B3-X12).

Fixed during this phase: B3-R01 (the database goes away, then comes back) was flaky. psycopg-pool retries a failed connection
after 1, 2, 4, 8 ... seconds, so after an outage the app stayed broken for a while after the database was back (after a one-minute
outage, up to about another minute). The pool now retries about once a second (`app/core/db.py`). The test was made stricter (a
longer outage) and was shown to fail without the fix. psycopg-pool stays pinned and a test checks the setting is still in place.

Decisions made where the design docs were silent (please check):
1. Login returns `{ user, token }` like register (the design says "Return JWT"); the frontend can use the user at once.
2. Token lifetimes are passed as seconds and added to the database's `now()` (repository rule 7: expiry uses the database clock).
   `get_latest_token_time` also returns how many seconds ago the token was made, by the database clock, for the 60-second rule.
3. The forgot-password email is sent in the background after the answer, so the answer time does not tell whether the email exists.
   The app waits for such emails when it shuts down.
4. Emails: surrounding spaces are trimmed, then the email must be valid and is lowercased. Names are trimmed, then 1-100 characters.
   Email link tokens must be exactly 43 URL-safe characters (the length we create), else 422.
5. Extra unknown fields in a request body are ignored.
6. Gaps #1 in practice: an old JWT made in the SAME second as a password change still works (whole-second comparison, as proposed).
   B3-X17 waits for the next second before changing the password.

### B4: Projects (notes)

Built: the 5 project routes through all layers (`routers/projects.py`, `validators/project.py` and `validators/project_rules.py`,
`controllers/project_controller.py`, `services/project_service.py`, `repositories/project_repository.py`), plus
`services/cleanup_service.py`, which runs AFTER the database commit of a delete (package files now, LangGraph data from B9).

| Route | Answer |
|---|---|
| `POST /projects` | 201 the project; 400 `LOCAL_PATH_NOT_ALLOWED` / `PATH_NOT_FOUND`; 409 `PROJECT_LIMIT_REACHED` (20) |
| `GET /projects` | 200 `{ items, total }`, newest first, only the user's own |
| `GET /projects/{id}` | 200, or 404 `NOT_FOUND` (same answer for "not yours" and "does not exist") |
| `PATCH /projects/{id}` | 200; only the fields sent change; 400 `INVALID_PROJECT_CONFIG` if the merged project is not valid |
| `DELETE /projects/{id}` | 200 `{ id, deleted: true }`; 409 `PROJECT_HAS_ACTIVE_RUN`; its runs are deleted too |

Test results: `backend/tests/test_b4_projects.py`, 46 of 46 pass. The list of 20 projects: p95 4.4 ms (B4-L01).

**B4-L02 measured at a fixed concurrency (option b, decided after review).** The plan's form, 500 list calls at the same
moment, measured p95 about 740 to 940 ms on one process (about 450 list calls per second; each one checks the login, runs 3
queries and sends 20 rows). The test now sends the 500 calls with a fixed number in flight. With 100 in flight the result sat
right on the 300 ms line on this machine (226 to 318 ms over three runs, so the test would be flaky); with **50 in flight p95 is
about 105 to 130 ms**, so 50 is used. One code fix went in first: JSON responses no longer go through FastAPI's generic encoder.

Decisions made where the design docs were silent (please check):
1. GitHub URLs must be exactly `https://github.com/<owner>/<repo>` (a `.git` ending is allowed; no trailing slash, no extra path,
   no query, no user name). Local paths must be absolute (Linux or Windows style) and must not contain `..` or a null byte.
2. Branch names: letters, digits, `.`, `_`, `/`, `-`; not starting with `-`, `/` or `.`; no `..` or `//`; not ending with `/`,
   `.` or `.lock`; at most 255 characters. An empty branch means the repo's default branch.
3. A zip project keeps no `source_reference` (one that is sent is ignored), also when a project is switched to zip.
4. Names and test commands are trimmed before the length check.
5. On PATCH, the local-folder checks (local mode, the folder exists) run only when `source_type` or `source_reference` change,
   so renaming a project whose folder was moved still works. Sending `null` for name, language, source type or test command is 422.
6. `GET /projects` returns all projects of the user (at most 20), so it has no page parameters.
7. Package files are only ever deleted inside `PACKAGES_DIR`, whatever path the database holds.
8. B4-C03 (delete and edit at the same moment): when the edit runs first both answer 200 and the project still ends up deleted;
   when the delete runs first the edit gets 404. Never a 500.
9. B4-X09 "not readable": the tests run as root, which can read everything, so the refusal of the operating system is simulated.

### B5: Runs, events, state rules, live stream (notes)

Built (the agent is a FAKE in this phase; the real one comes in B9):
- The 8 run routes through all layers: `routers/runs.py`, `validators/run.py`, `controllers/run_controller.py`,
  `services/run_service.py`, `repositories/run_repository.py`, `attempt_repository.py`, `event_repository.py`
  (plus small reads in `test_result_repository.py` and `package_repository.py`).
- `services/run_progress.py`: what the agent's steps use (events, status changes, hints, attempts, approval
  requests) and the ONE shared finish function used by the agent, a cancel and the timeout job.
- `agent/runner.py`: the AgentRunner (one background task per run; start, resume, cancel; an unexpected agent
  error ends the run as `failed` / `internal_error`).
- `services/jobs.py`: startup recovery and the 1-hour timeout sweeper.

| Route | Answer |
|---|---|
| `POST /projects/{id}/runs` | 201 the run (status `started`); the agent works in the background. 403 `EMAIL_NOT_VERIFIED`, 404, 400 `LOCAL_PATH_NOT_ALLOWED` / `ZIP_REQUIRED`, 409 `ACTIVE_RUN_EXISTS` |
| `GET /runs` | 200 `{ items, total, page, limit }`, newest first; `project_id`, `status` (or `active`), `page`, `limit` (at most 50) |
| `GET /runs/{id}` | 200 the run with `attempts` (each with `edits`), `test_results` and `package` |
| `GET /runs/{id}/events` | the live stream (Server-Sent Events, format below) |
| `POST /runs/{id}/approval` | 200; 409 `RUN_STATE_INVALID` / `STALE_ATTEMPT` |
| `POST /runs/{id}/hint` | 200; 409 `RUN_STATE_INVALID` |
| `POST /runs/{id}/cancel` | 200 (stopped, `cancelled_by_user`); 409 `RUN_STATE_INVALID` |
| `DELETE /runs/{id}` | 200 `{ id, deleted: true }`; 409 `RUN_STATE_INVALID` for an active run |

Live stream: each event is `id: <sequence_number>`, `event: <event_type>`, `data: {sequence_number, event_type, message,
details, attempt_id, created_at}` and a blank line. The `Last-Event-ID` header resumes after that number. `: ping` every
15 seconds. The stream ends after the final `status_changed` event. Between checks (about once a second) it holds no
database connection.

Test results: `tests/test_b5_runs.py` and `tests/test_b5_stream_jobs.py`, 100 tests, all pass. Numbers on this machine:
100 live streams on 100 runs got every event in order (B5-L01); while 100 streams waited, listing projects stayed under 16 ms
and the pool needed 5 connections (B5-L02); 100 waiting streams cause about 100 database queries per second, one per stream
per check (B5-L03); a replay of 5,000 events takes 0.5 s (B5-L04); 20 runs started at once all ran in parallel (B5-L05);
100 runs in a row left no tasks behind (B5-L06); about 1,800 events per second can be saved (B5-L07); a history page of a
user with 5,000 runs takes p95 10 ms (B5-L08). B5-L09: a stream held open for the full 30 minutes with only pings stayed open, and the server's memory changed by 47 KB (run once with `SANGRA_SOAK_SECONDS=1800`).

Decisions made where the design docs were silent (please check):
1. Starting a run takes JSON `{ "bug_description" }`, or, for zip projects, `multipart/form-data` with the fields
   `bug_description` and `zip_file`. A zip over 50 MB is 422; a whole upload over 51 MB is refused with 413 while it
   streams in (the rest is never read). The zip waits at `WORKSPACES_DIR/<user_id>/<run_id>/source.zip` for the agent.
   A zip sent for a non-zip project is ignored.
2. A hint is saved by ONE conditional update: only if the run is still waiting for a hint, the hint event is saved and the
   run goes back to `searching` (the design lets the agent's wait step change the status; doing it in the same step as
   the save stops a double click from sending two hints).
3. Every status change and its `status_changed` event are saved in one transaction. Once a run has its final status, no
   other event can be added, so the final `status_changed` event is always the last one.
4. An accepted attempt keeps the status `waiting_for_approval` with `decision = accepted` until the agent applies it
   (the table has no `accepted` status).
5. An odd `Last-Event-ID` (not a number, negative, too big) counts as 0: the whole diary is sent again.
6. Until the agent exists (B9), the real app ends a started run at once as `failed` / `internal_error` with the summary
   "The agent is not part of this build yet". Tests use the fake agent.
7. Startup recovery only ends runs created before this server process started and not run by it. If the database is down
   at startup, recovery is tried again in the background every 5 seconds.
8. B5-C02 (Accept and Cancel at the same moment): a cancel is allowed while a run is `applying`, so when the Accept lands
   first both answer 200 and the run ends `stopped`; when the Cancel lands first the Accept gets 409. Never a mixed state.
   B5-C06 (delete and cancel): when the cancel ends the run first the delete may then succeed; nothing is half deleted.
9. The `package` and `apply-to-source` routes (part of B5-S01's table) come in B10 and are added to that test there.
10. B5-L09 runs for 20 seconds in the normal test run (`SANGRA_SOAK_SECONDS` makes it longer).

### B6: Workspace manager and the 3 read-only code tools (notes)

Built (no LLM yet; the agent uses these from B9):
- `app/runners/workspace_manager.py`: builds `WORKSPACES_DIR/<user_id>/<run_id>/code/` (plus `artifacts/`) from a public
  GitHub repo (shallow clone of the chosen or default branch, the commit is recorded), a local folder or an uploaded zip;
  makes it a git repository (`git init` + a first commit of everything copied); the path guard `safe_path()`; undo
  (`git reset --hard` + `git clean`, then checks the copy is clean, else `undo_failed`); the diff (the patch); delete;
  and the age-based cleanup of old run folders.
- `app/agent/tools/code_tools.py`: `search_files`, `search_in_file`, `read_file_lines`. Read-only, size-capped, they
  answer `{ "error": ... }` for a bad request instead of failing.

Safety (Gaps #5 and #6 are implemented):
- git always runs without system or global config, without hooks or fsmonitor, HTTPS only, with no password prompt, and
  with only the proxy and CA variables of the environment (never our secrets). Commands are argument lists, never shell text.
- A local folder or zip never brings its own `.git` folder: the copy gets a fresh repository of ours, so a hostile
  `.git/config` (fsmonitor, filters, sshCommand) or hook can never run. Tested with a zip and with a local folder.
- Zips: entries with `..`, absolute or drive paths, symlink entries and password-protected entries are refused; the total
  size and the number of files are limited, and the bytes are counted while unpacking (a zip that lies about its sizes
  is refused).
- Local copies never follow or copy symlinks, sockets or devices, and skip `target`, `build`, `node_modules`,
  `__pycache__`, `.venv` and `.git`.
- The path guard refuses `..` (also `%2e%2e`), absolute and drive paths, `\`, null bytes, very long paths, anything
  inside `.git/`, and symlinks that lead out of `code/`.

Test results: `tests/test_b6_workspace.py`, 61 tests, all pass (the clone tests use the tiny public repo
`octocat/Hello-World`, so they need internet). Numbers: `search_files` over 50,000 files 0.7 to 0.9 s (B6-L01);
200 lines of a 1 MB file in under 9 ms (B6-L02); 10 parallel copies of a 100 MB folder in 12 to 15 s using about 2 GB
(copy + git objects), all of it freed again after delete (B6-L03).

Decisions made where the design docs were silent (please check):
1. Limits (all settings): the copy may be at most 500 MB and 50,000 files; a clone may take at most 5 minutes; leftover
   run folders older than 24 hours are removed by the cleanup (wired to a job in B10).
2. A local folder that is already a git repository is copied WITHOUT its `.git` folder and gets our own fresh repository
   (the safest reading of Gaps #5; undo and the patch only need our first commit). `source_commit` is filled only for GitHub.
3. Files ignored by `.gitignore` are still copied and recorded in our first commit (`git add --force`), so undo restores
   them and tests that need them keep working.
4. Search is case-insensitive for English letters (the bytes are lower-cased); non-English letters must match exactly.
   Binary files are recognised by a zero byte in the first 8 KB. Lines in tool answers are cut at 300 characters.
5. Every tool answer is capped at 16,000 characters (setting `TOOL_OUTPUT_MAX_CHARS`). 200 lines of typical code fit;
   200 lines of 100 characters do not, and the answer is cut from the end with `truncated: true`. The final number is
   checked against the model's context in B8 (B8-X08).
6. Decided after review: when EVERY file of a zip sits inside one single top folder (as in GitHub's "Download ZIP",
   for example `repo-main/`), that folder is removed while unpacking, so the test command runs in the project root.
   A zip with files at the top, or with several top folders, is unpacked as it is.
7. B6-X05 "not readable" and B6-X15 "locked file": the tests run as root, so "not readable" is simulated, and the locked
   file uses `chattr +i` (Linux).

### B7: Docker test runner (notes)

Built: `app/runners/test_runner.py`. One container per test run, with only the run's `code/` folder mounted (at
`/workspace`) plus a dependency cache volume (at `/cache`), labelled with the run id. The test command runs with
`sh -c` inside the container. Limits: 10 minutes, 2 CPUs, 2 GB memory, 512 processes, 1 MB of output. No Linux
capabilities, `no-new-privileges`, and never root. Nothing from the backend's environment is passed in. At most 2 test
containers run at the same moment (Gaps #8; more wait their turn). At startup, containers left over after a crash are
removed (Gaps #7). The full log is saved to `artifacts/`; the last 8,000 characters are kept as the excerpt.

| What happened | Result |
|---|---|
| exit code 0 | `passed` |
| any other exit code (failing tests, compile error) | `failed` |
| exit code 126 or 127 (the test command was not found or cannot run) | `error` |
| killed for using too much memory, or for printing more than 1 MB | `failed`, with a note |
| over the time limit | `timed_out` (the container is killed and removed) |
| Docker not running, image missing and not pullable, Docker failing during the run | `error`, with a clear note |

Test results: `tests/test_b7_test_runner.py`, 24 tests against the real Docker engine, all pass. Numbers on this machine:
a Maven project with JUnit 5 takes 11.5 to 12 s with an empty dependency cache and 3.9 to 4 s with a warm one (B7-L02);
10 pytest runs with at most 3 at once took 12 s (B7-L01); when Docker was stopped in the middle of a test the runner
answered `error` after 12.6 s and nothing hung (B7-R02); 1 GB of output was cut at 1 MB while the backend held only a few
MB (B7-X06). B7-M01 (Docker Desktop on Windows with a OneDrive folder) needs a real Windows machine: still to be done by hand.

Fixed during this phase: B7-R02 found that when Docker disappears in the middle of a test, removing the container could
raise an error and the runner crashed instead of answering `error`. Cleaning up can no longer raise.
Note: one full run had a single B7-C02 failure that did not come back in 4 further full runs (it came right after a run
in which Docker had been restarted and 11 crash leftovers were present). The test now prints every outcome if it fails.

Decisions made where the design docs were silent (please check):
1. Images: `maven:3.9-eclipse-temurin-17` for Java and `python:3.12-slim` for Python (settings). The Python image has no
   pytest, so a Python project's test command installs what it needs, for example
   `pip install -q -r requirements.txt && python -m pytest -q` (pip installs into the cache volume as the container user).
2. Dependency cache: a Docker volume mounted as the container's home (`/cache`): one per language in local mode, one per
   user and language in deployed mode (so one user's build cannot poison another's, design 6.7). The first use gives the
   new volume to the container user.
3. Container user (Gaps #9): the backend's own user id, so the files the tests create stay removable by the backend; when
   the backend runs as root, user 1000 is used and the run's copy is opened for writing. Root is never used. No exceptions
   were needed for Maven or pip.
4. Using too much memory or printing more than 1 MB counts as `failed` (the code under test did it), with a note.
5. Internet is allowed inside test containers (MVP, as designed).
6. In the cloud sandbox where this is developed, the network inspects TLS, so the TEST SUITE hands the sandbox's
   certificate to the test containers (`sandbox_network_extras()` in `tests/conftest.py`). The app does not do this.

---

## Known limits (MVP)

- Keyword search does not catch every bug; the LLM tries related words but it will miss some.
- Public GitHub repos only (no private repo tokens yet).
- Docker Desktop must be installed.
- Internet is allowed inside the test container for Maven / pip downloads (to be tightened later).
- If the baseline tests already pass, a passing result is labelled *"tests passed, but no test covers this bug, please verify the fix yourself"*.

---

## Author

Sangram Ganta
