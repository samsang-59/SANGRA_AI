# SANGRA_AI

**An AI coding agent that fixes bugs, with a human in the loop.**

You describe a bug in plain English. SANGRA_AI searches your code, finds the wrong lines, shows you the fix,
**waits for your approval**, applies it to a safe copy, and runs your tests inside Docker. If the tests fail,
it undoes the change and tries again (up to 3 times). Your original code is never touched.

> **Status:** implementation in progress, phase by phase (see [the plan](06_implementation_plan.md) and
> [Implementation progress](#implementation-progress) below). Current phase: **FE4 Start run page and History page** (awaiting review). The backend (B0 to B11) is complete.

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
| B7 Docker test runner | Done (approved) | 24 of 24 pass; B7-M01 is manual (Windows), not done yet |
| B8 LLM client and prompts | Done (approved) | 29 of 29 pass; B8-M01 and B8-Q01 (real Groq) not run yet |
| B9 The LangGraph agent (11 steps, pause and resume, limits) | Done (approved) | 62 of 62 pass (541 in total with B0 to B8); B9-E01 (real Groq) not run yet |
| B10 Finish, package, download, apply to my folder, cleanup | Done (approved) | 36 of 36 pass (577 in total with B0 to B9) |
| B11 End to end, security sweep, soak and chaos tests | Done (approved) | 30 of 30 pass (607 in total with B0 to B10); B11-E01 to E07 with the real Groq not run yet |
| FE0 Frontend project setup (Vite, React, router, QueryClient, env, design tokens) | Done (approved) | 22 unit + 6 browser tests pass (Chromium); lint and build scan clean; backend 607 of 607 still pass. FE0-B01 in Firefox not run yet |
| FE1 API layer (client, 22 route functions, live stream helper) | Done (approved) | 161 unit + 2 real-backend contract tests pass; FE0 tests still pass; backend 607 of 607 pass |
| FE2 Auth state, guards and auth pages (register, login, verify, forgot, reset, account) | Done (approved) | 234 unit + 11 browser + 2 contract tests pass; backend 607 of 607 pass |
| FE3 Projects pages (list, create, edit, delete) | Done (approved) | 282 unit + 17 browser + 2 contract tests pass (Chromium; Firefox blocked by the network policy); backend 607 of 607 pass |
| FE4 Start run page and History page | Built, awaiting review | 317 unit + 23 browser + 2 contract tests pass (Chromium); backend 607 of 607 pass |
| FE5 to FE7 | Not started | |

### Open items (manual checks still to do)

These checks cannot be run from the cloud container where the backend is built. None of them blocks the next phase.

| ID | What it is, in simple words | Why it is still open | What is needed |
|---|---|---|---|
| B7-M01 | On a Windows laptop with Docker Desktop, run the tests of a project stored in the OneDrive Desktop folder. | The build machine is Linux, so Windows cannot be tried there. Docker Desktop on Windows sometimes cannot share folders inside OneDrive. | On your laptop: create a "local folder" project pointing to a folder on the OneDrive Desktop, start a run, check that the tests run. |
| B8-M01 | Three real calls to the AI model (Groq) on the discount example (5% instead of 10%): find the bug, propose a fix, read the answer. Record the time and the tokens used. | Every test so far uses a fake AI with scripted answers. The cloud container's network blocks `api.groq.com` and has no Groq key. | Option (a) or (b) below. |
| B8-Q01 | Quality check of the AI: 10 small projects with one planted bug each (5 Java, 5 Python). Record whether the AI finds the right file and line and gives a valid, correct fix. Not an all-pass test (the AI is not deterministic). Bar: at least 5 of 10 valid fixes, otherwise change the model or the prompts first. | Same as B8-M01. | Option (a) or (b) below. |
| B9-E01 | One complete run with everything real: real Groq, real Docker, the discount example. You approve the fix, the tests pass, the run succeeds. Time and tokens recorded. | Same as B8-M01. Every other part of this flow is already tested with fakes. | Option (a) or (b) below. |

Two ways to unblock B8-M01, B8-Q01 and B9-E01:
- (a) In the cloud: in the claude.ai environment settings, allow `api.groq.com` in the network settings and add the
  Groq key as a secret named `GROQ_API_KEY`. Then the checks can be run there.
- (b) On your own machine, once the backend is finished: put `GROQ_API_KEY` in `backend/.env`, start the backend and
  Docker Desktop, and run the checks by hand (step-by-step instructions to be added).

B11-E01 to B11-E07 (the full journeys) are also manual checks with the real Groq. They were run automatically
with everything real except the model (a scripted fake); see the B11 notes. With the real Groq they are part of
option (a) or (b) above.

New work is pushed to the branch `claude/optimistic-rubin-i5mzzg` and merged into `main` by pull request. If `main`
does not show the latest phase yet, check out that branch (`git checkout claude/optimistic-rubin-i5mzzg`).

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

Some tests need Docker running (the real test runner, B7 and B11) and use the `maven:3.9-eclipse-temurin-17` and
`python:3.12-slim` images. `pytest -q -m "not slow"` skips the slow ones. The B11 load, soak and limit tests run a
short version by default; `SANGRA_LONG_TESTS=1 .venv/bin/pytest -q tests/test_b11_load.py tests/test_b11_deploy.py`
runs the full lengths of the plan (10 minutes of load, 30 minutes of soak, a fresh virtual environment for D01).
The dependency audit (B11-S05) needs `pip install pip-audit`.

### Running the frontend

Needs Node.js 22 or newer. The backend must run as well (it answers on `http://localhost:8000/api/v1`).

```bash
cd frontend
npm install
cp .env.example .env              # VITE_API_URL = the backend address (default http://localhost:8000/api/v1)
npm run dev                       # serves http://localhost:5173
```

Frontend checks: `npm run lint`, `npm test` (unit tests, Vitest), `npm run build && npm run check:build` (the production
build, then a scan of it for secrets), `npm run test:e2e` (browser tests with Playwright; it starts `npm run dev` and
`npm run preview` itself, so run `npm run build` first; `E2E_FIREFOX=1` adds Firefox where it is installed), and
`npm run test:contract` (the API layer against the real backend: it creates a fresh database, starts the backend
with the scripted agent and the file mailer, runs the tests and drops the database; needs `backend/.venv` and
PostgreSQL).

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
6. Until the agent existed (B5 to B8), the app ended a started run at once as `failed` / `internal_error`. Since B9 the
   real LangGraph agent runs every run.
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
   200 lines of 100 characters do not, and the answer is cut from the end with `truncated: true`. B8-X08 confirmed
   that 15 answers at this cap fit the model's context.
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

### B8: LLM client and prompts (notes)

Built (in `app/agent/llm/`):
- `client.py`: `GroqChatModel` (the Groq SDK; a client is built from the settings for each call) and `LLMClient`
  (retries rate limits, server errors, timeouts and dropped connections up to 3 times, waiting 1 s, 2 s, 4 s; then
  `LLMError`, which ends a run as `llm_error`; a wrong API key or a refused request is not retried). It adds up the
  tokens. Logs hold only metadata: never prompts, replies, code or the API key.
- `prompts/`: the prompts as plain text files (`investigate_system`, `investigate_user`, `propose_system`,
  `propose_user`), filled in with `string.Template`. They tell the model that the bug text, hints and code are data,
  not instructions.
- `investigation.py`: the investigate loop. The model may call only `search_files`, `search_in_file` and
  `read_file_lines`, at most 15 times per pass. Any other tool or bad arguments get an error back and the loop goes on.
  The model ends with a fixed JSON answer (`found` / `not_found` with the places). Each place is checked against the
  real file and its current lines are attached. Only this compact result is kept (the chat is not).
- `proposal.py` and `parsing.py`: propose_fix (one call, no tools) and the parser of the fixed answer format
  (explanation + edits: file, line range, old_code, new_code, reason). An answer that cannot be read gets one more
  try with the reason; after that it is a failed proposal, never a crash. The parser checks only the shape; whether
  the edits match the files is checked in B9.

Test results: `tests/test_b8_llm.py`, 29 tests, all pass, using a scripted FakeLLM and a local fake Groq server that
answers with real HTTP errors (429, 500, 401, a dropped connection, no answer at all) to the real Groq SDK. Numbers:
with 15 tool answers at the size cap, the largest investigation prompt is about 89,000 tokens, below the 122,880
tokens left before the reply (B8-X08); 20 parallel LLM calls of 0.2 s each finish in under 0.6 s (B8-C01).

**Not run yet (manual, [M]):** B8-M01 (three real Groq calls on the Amazon discount example) and B8-Q01 (the quality
benchmark on 10 seeded bugs). This cloud environment's network policy blocks `api.groq.com`, and they also need a
Groq API key. To run them here: allow `api.groq.com` in the environment's Network access settings and provide the key
as an environment secret (`GROQ_API_KEY`); or run them on your own machine.

Decisions made where the design docs were silent (please check):
1. The investigation ends with a fixed JSON answer (`outcome`, `findings`, `summary`; at most 5 places of at most 60
   lines). When the 15 tool calls are used up, the model gets one last call without tools to give that answer; if it
   cannot, the outcome is `not_found`. A `found` answer whose places do not exist counts as `not_found`.
2. An answer with a little text or a ```json fence around the JSON is accepted. An answer without readable JSON
   (a refusal, prose in another language) is a failed proposal.
3. Settings: replies are at most 8,192 tokens, a call may take at most 60 s, and the model's context is taken as
   131,072 tokens (`LLM_CONTEXT_TOKENS`). **That context size could not be checked against Groq's documentation from
   here (blocked).**
4. Token counting for B8-X08 uses a cautious estimate (3 characters per token) because the tokenizer files cannot be
   downloaded from this environment. Real counts come from Groq's own `usage` numbers at run time.
5. The tool answer size cap stays at 16,000 characters (the open question from B6): 15 answers at the cap fit.

### B9: The LangGraph agent (notes)

Built (in `app/agent/`):
- `graph.py`: the fixed flow of section 6.2 with the 11 steps; `nodes/steps.py`: the steps themselves; `state.py`: the
  small state of section 6.3 (no counters, no chat, no whole files); `edits.py`: the edit checker shared by
  check_proposal, apply_edits (all or nothing, each file written through a temporary file and an atomic rename) and,
  in B10, "Apply to my folder"; `workflow.py`: start and resume through LangGraph with the Postgres checkpointer
  (its own connections); `fakes.py`: the scripted LLM and test runner for test setups (Gaps #10).
- The AgentRunner now runs this graph. Each pause is a "request" step (does the work) followed by a "wait" step that only
  pauses; after a resume the decision and the hint are read from the database, never from the resume value.
- When a run ends (agent, cancel, timeout or restart), in this order: anything still changed is undone (unless the run
  succeeded), the temporary copy is deleted (`workspace_deleted_at`), and the LangGraph snapshot is deleted (by the
  runner after the graph has ended, or at once if no graph runs for the run). B10 adds the package before the copy
  is deleted. Deleting a run or a project also deletes its LangGraph rows.
- `python -m app.migrate` now also creates LangGraph's own tables (its `setup()` call).

Checked at implementation (the open items of section 6.7):
- When LangGraph resumes, the paused step starts again from its first line (confirmed against Postgres), so the
  request/wait split is needed and works: the approval event and the status change happen once per pause (B9-R01).
- `adelete_thread(thread_id)` removes every LangGraph row of a run. Resuming a thread that has no paused snapshot would
  start the graph again from the top with an empty state, so the workflow refuses that (B9-C04).

Test results: `tests/test_b9_agent.py` (43) and `tests/test_b9_agent_recovery.py` (19), 62 tests, all pass, three
runs in a row. They use the real graph and the real Postgres checkpointer with a FakeLLM and a FakeTestRunner, plus one
full run with the real workspace and the real Docker runner: the Amazon discount example as a Maven project, the
baseline fails on the 5% bug, the approved fix passes the tests, the run succeeds. Numbers: 50 runs paused at approval
leave 0 running tasks and use about 19 KB of saved state each (B9-L01, B9-L04); 20 runs at the same time keep the event
loop delay at 16 ms at most (B9-L02); 100 runs in a row leave no tasks, snapshots or folders behind (B9-L03).

Fixed during this phase (found by the tests):
1. B9-X11: after an error inside a step, a LangGraph save running in the background could land after the snapshot was
   deleted. The graph now saves every checkpoint before going on (`durability="sync"`).
2. The AgentRunner forgot a finished task before deleting its snapshot, so for a moment a run looked idle while its
   snapshot still existed. It now deletes first, then forgets the task.

**Not run yet (manual):** B9-E01 (the same happy path with the real Groq), for the same reason as B8-M01: `api.groq.com`
is blocked here and a key is needed.

Decisions made where the design docs were silent (please check):
1. Stop reasons for a baseline that cannot run: `baseline_could_not_run` when the test command was not found or the
   tests ran too long, `test_runner_error` when Docker could not run them. A baseline whose tests FAIL is normal (that is
   usually the bug) and the run goes on.
2. How old_code is compared (section 6.7): line by line without the line endings (so Windows files work), everything
   else exactly (spaces and tabs count); one newline at the end of old_code is ignored. New lines keep the file's own
   line ending. "Changed lines" = lines removed + lines added. Files in generated folders cannot be edited.
3. A proposal that cannot be read counts as one failed check (3 in a row: `proposal_invalid`), like a proposal that
   fails the checks. The problems, with the real lines of the file, go back to the LLM.
4. The edits of an attempt are saved per file from the bottom up (section 6.4 step 6).
5. Gaps #10 is built in its simplest form: `AGENT_TEST_MODE=fake` with `AGENT_FAKE_SCRIPT` (a JSON file with the LLM
   answers and the test results, used in order and then from the top again) and `MAILER_MODE=file` with `MAIL_FILE`.
   Both are refused in deployed mode. The browser tests (FE7) may need more from them.
6. The run detail events for steps use the existing event types: `tool_called` for each tool call, `tool_result` for
   "found N places", "a fix was drafted" and "applied N edits in M files", `error` for clear failure messages.

### B10: Finish, package, download, apply to my folder, cleanup (notes)

Built:
- `app/runners/finalizer.py`: builds the download package of a finished run. It holds `summary.md` (from a template:
  status, the reason in plain words, the bug, the tests before and after each attempt, the attempts) and the full test
  logs (`logs/baseline.log`, `logs/attempt_<n>.log`). A succeeded run also gets `changes.patch` (git diff of the copy)
  and `fixed_files/<path>` (only the changed files). Other endings get `attempts.md` (every attempt with its edits,
  decision and result). Never the whole project. Zip names are built only from fixed names and the edits' relative
  paths; a name with `..`, an absolute part, a backslash or a null byte is never written, and a source file outside the
  copy is never read. The zip is written to a temporary file and renamed.
- `app/services/package_service.py`: builds the package and saves its row (expires 7 days later). At most one package
  per run. If building fails, the run still finishes with its final status, an `error` event says the package is
  missing, and the changes stay visible in the run detail.
- The finish order (04_backend_design.md 6.4 step 11): undo what is still changed (unless the run succeeded), build the
  package, save the final status (its `status_changed` event is the last event), delete the copy, delete the LangGraph
  snapshot. A cancel saves the stopped status first (as `cancel_run` says), then stops the agent, undoes, builds the
  package and deletes the copy. A per-run lock makes a cancel and the graph's own finish at the same moment end the run
  once, with one package.
- Startup recovery also completes finishes that were cut off: a finished run whose copy still exists gets its missing
  package and its copy deleted.
- `GET /runs/{id}/package`: streams the file in 64 KB chunks with `Content-Disposition` and `Content-Length`. The file
  is opened before the answer starts, so a cleanup that deletes it during a download cannot break that download.
- `POST /runs/{id}/apply-to-source`: local mode and local folder projects only, succeeded runs only, once. The run is
  claimed first with one conditional update, so two clicks at once write once. The same edits are checked again against
  the user's folder (the exact old lines must still be there). All edits are checked before anything is written; if
  one file then cannot be written, the files already written are put back.
- Cleanup jobs (every `CLEANUP_INTERVAL_SECONDS`, default 1 hour):
  - expired packages, in batches (file deleted, then `deleted_at` set);
  - copies of finished runs older than 24 hours (`workspace_deleted_at` set), and run folders older than 24 hours that
    belong to no active run (left behind by a deleted run);
  - package files that no row points to any more, at least 1 hour old;
  - email tokens that expired more than 24 hours ago.
  A file that cannot be deleted is logged and tried again next time; one failing job does not stop the others.

Test results: `tests/test_b10_package.py`, 36 tests, all pass. B5-S01 now also checks the package and apply routes
(another user gets 404). Full suite: 577 of 577 pass (B0 to B10).
Numbers: 10,000 expired packages cleaned in about 1.1 s in batches of 500, `/health` at most 13 ms during it
(B10-L01); a 50 MB download takes 0.17 s and the server's peak memory grows by about 1 MB (B10-L02); 20 parallel 2 MB
downloads in 0.27 s (B10-L03); 50 runs finishing at the same moment give 50 packages, each in its user's folder with
its own summary, total disk use equal to the stored sizes (B10-L04).

Fixed during this phase (found by the tests):
1. B10-S02: an edit with an absolute path (`/etc/passwd`) passed the zip-name check (it became
   `fixed_files/etc/passwd`) and the file would have been read from outside the copy. Absolute parts are now refused
   and the source file must be inside the copy.
2. B10-L01: the first version deleted and marked one package at a time (18 s for 10,000). Each batch now deletes its
   files in one thread call and marks them with one statement.
3. B9-L02 (20 runs at the same time) failed in the full run at 104 ms (limit 100 ms; it was 16 ms in B9). The cause was
   a full garbage-collector pass of about 100 ms, not our code. B10 creates more objects, so a pass now fell inside the
   measured window. The app now calls `gc.freeze()` once startup is done: the objects loaded at startup stay
   until shutdown anyway, so the collector stops scanning them. The worst delay is back to 15 to 27 ms. Packages are
   also built in a small pool of their own (2 threads), so many runs finishing at once wait their turn instead of
   filling the shared thread pool.

Decisions made where the design docs were silent (agreed by the owner):
1. Package files are stored at `PACKAGES_DIR/<user_id>/run_<run_id>.zip`; the download name is `sangra-run-<id>.zip`.
2. A normal finish builds the package before the final status is saved (as 6.4 step 11 lists it); a cancel saves the
   final status first (as `cancel_run` lists it).
3. B10-R02: if the server stops after the package was built but before the final status, startup recovery ends the run
   as `failed` (`server_restarted`) like any interrupted run, and keeps the package already built (no second package).
   That package's summary then still describes the run as it was about to end.
4. A new error code `SOURCE_NOT_WRITABLE` (409) for "Apply to my folder" when a file is read-only or cannot be written;
   nothing is changed. A deleted original file is reported as `SOURCE_CHANGED`. Windows line endings are kept.
5. If applying fails (`SOURCE_CHANGED` or `SOURCE_NOT_WRITABLE`), `applied_to_source_at` is cleared again, so the user
   can fix the folder and try once more.
6. "Apply to my folder" also needs the package to be available (`PACKAGE_EXPIRED` after 7 days), as B10-X04 asks.
7. Cleanup ages and sizes: copies older than `WORKSPACE_MAX_AGE_HOURS` (24); orphan package files at least 1 hour old
   (so a package being built is never taken); email tokens removed 24 hours after they expired
   (`TOKEN_RETENTION_HOURS`); batches of 500 (`CLEANUP_BATCH_SIZE`). The run detail shows a package as not available
   once it is expired, even before the cleanup job has deleted it.

### B11: End to end, security sweep, soak and chaos tests (notes)

Nothing new was built in this phase except fixes (below) and this README. New test files: `tests/test_b11_e2e.py`,
`test_b11_security.py`, `test_b11_chaos.py`, `test_b11_load.py` and `test_b11_deploy.py`.
All 30 B11 tests pass; the full suite passes 607 of 607 (B0 to B11), and the full-length load tests pass too.

**Journeys (E01 to E07).** The plan marks these manual with the real Groq, which cannot be reached from the build
machine. They run automatically through the HTTP API with everything real except the model (a scripted fake): routes,
database, the LangGraph agent with its Postgres checkpointer, the workspace manager, and, for E01, E02 and E07, the
real Docker test runner.
- **E01:** a Java zip project (the Amazon discount example): register, verify by the emailed link, log in, create
  the project, upload the zip, approve, success (baseline failed, after the fix passed), download the package, read
  the live stream (16 events, numbered 1..16).
- **E02:** a Python project with real Docker. It uses a local folder, then "Apply to my folder". There is no public
  GitHub repo with this seeded bug; cloning public GitHub repos is proven in B6. The GitHub variant stays manual.
- **E03:** reject with feedback, then accept. The feedback reaches the model.
- **E04:** nothing found, then a hint, then success.
- **E05:** a bug that cannot be fixed, three ways: tests fail 3 times, 3 rejects, 3 hints without a match. Each
  ends inside the limits. The scripted runs used 5, 5 and 8 model calls (500 to 800 tokens). With the real model the
  hard caps bound the cost: at most 15 tool calls per search, at most 4 searches (the first and 3 hints), and at most
  3 proposals that fail or are rejected.
- **E06:** cancel while searching, waiting for a hint, waiting for approval, during the baseline tests and during the
  tests after the fix. Each time: status `stopped`, one package, nothing left behind (no folder, no LangGraph rows,
  no running task), the user's folder untouched.
- **E07:** server restart while waiting for approval, approve after the restart, the run succeeds with real Docker.

**Security sweep (S01 to S07).**
- **S01:** all 24 routes are read from the app's own route table. The 18 protected ones answer 401 without a JWT,
  with a broken one and with "Basic"; the 6 public ones never do.
- **S02:** all 12 routes with an ID, called by user B with user A's IDs, answer 404. Each answer is identical to the
  one for an ID that does not exist, and A's data is unchanged.
- **S03:** 396 requests with 18 nasty values in every text field and query: SQL, script, template, 10,000
  characters, Unicode and right-to-left marks, null bytes, shell syntax. No 500, no internal detail; odd names are
  stored exactly as sent.
- **S04:** every response, every log line and every row of every table (LangGraph's included) after a full journey:
  no secret and no raw email token anywhere.
- **S05:** pip-audit: 59 runtime packages and 65 with the test tools, no known vulnerabilities.
- **S06:** the rate limits answer 429 at the 11th login, 6th register, 4th forgot-password, 2nd resend within
  60 s and 6th change-password, with `Retry-After`.
- **S07:** a 51 MB upload is refused before a run is created. A zip with 100,000 files (9.3 MB) and a zip bomb
  (597 KB that unpacks to 600 MB) are refused while unpacking: the run fails with `clone_failed` and a clear
  message, and nothing is left on disk.

**Load (L01 to L03), full lengths** (`SANGRA_LONG_TESTS=1`; one server process on this machine):
- **L01:** 50 users for 10 minutes, mixed actions with 0.2 to 1 s of thinking time between clicks: 41,657 requests,
  0 errors.

  | Action | Calls | p50 | p95 |
  |---|---|---|---|
  | List projects | 12,251 | 5 ms | 10 ms |
  | List runs | 12,655 | 6 ms | 12 ms |
  | Run detail | 10,517 | 8 ms | 16 ms |
  | Login (real bcrypt cost 12) | 2,125 | 287 ms | 323 ms |
  | Live stream of a finished run | 4,109 | 1,009 ms | 1,017 ms |

  The stream stays open until the server's next check sees the run is finished (`STREAM_POLL_SECONDS`, 1 s), so
  it is not counted as a "normal route". All 50 users come from one address (this machine), so the per-address
  login limit was raised for this test; the per-email limit stayed.
- **L02:** 10 users ran 10,246 scripted runs in 30 minutes. No run was stuck. Tasks (12 when idle), threads (9 to
  11), open files (21 to 27), database connections (10) and work folders (0 when idle) did not grow. Memory went from
  121 MB to 143 MB (about 2.5 KB per run). That growth was the fake model and fake test runner keeping a record of
  every call (test bookkeeping). With those records cleared, a 10-minute soak (3,267 runs) stayed flat: 115.8 MB to
  116.3 MB, +0.3 MB over the last 2,641 runs.
- **L03:** runs in parallel in one process (fake model 0.2 s per call, fake tests 0.2 s), doubled each step.
  Nothing broke up to 320 runs at once.

  | Runs at once | All finished in | Worst event-loop delay | Slowest run detail | Memory |
  |---|---|---|---|---|
  | 10 | 2.6 s | 10 ms | 35 ms | 144 MB |
  | 20 | 3.8 s | 16 ms | 21 ms | 145 MB |
  | 40 | 6.8 s | 24 ms | 29 ms | 147 MB |
  | 80 | 13.3 s | 51 ms | 28 ms | 151 MB |
  | 160 | 27.9 s | 114 ms | 33 ms | 162 MB |
  | 320 | 55.2 s | 182 ms | 180 ms | 182 MB |

  So the backend itself is not the limit. With real tests the limit is Docker: at most
  `MAX_PARALLEL_TEST_CONTAINERS` (2 by default) test containers run at once and the others wait their turn. On this
  machine a Maven test run takes 5 to 15 s and a Python one 3 to 10 s. With real Groq, its rate limits per key decide
  how many runs can search at the same time.

**Chaos (R01 to R05).**
- **R01:** PostgreSQL was stopped for 60 s in the middle of 5 runs. While it was down, `/health` answered 503 and
  other routes 500, all in our error shape, none slower than 2 s. The app was healthy 0.4 s after the database came
  back. All 5 runs ended as `failed` (`internal_error`), and a new run worked normally.
- **R02:** Docker was stopped while the tests after a fix were running. The run ended 0.2 s later as `failed`
  (`test_runner_error`) with an error event, and the copy was deleted.
- **R03:** the workspace disk was full (a 3 MB disk, an 8 MB project). The run failed in 0.1 s with `clone_failed`
  ("No space left on device"). The half copy was removed, and the next run worked.
- **R04:** Groq could not be reached. The client tried 4 times, then the run failed with `llm_error` (4 s with a
  0.5 s retry base; about 7 s with the default 1 s). The user sees "The AI service did not answer after 4 tries" and
  "The run failed. The AI service did not answer." A 2-minute outage therefore fails the run rather than waiting.
- **R05:** the email service was down (SMTP refused). Register still answered 201 with a JWT, forgot-password still
  answered 200 with the same answer as for an unknown email, and each failed email was logged.

**Fresh machine (D01, D02).**
- **D01:** the test reads the commands of "Running the backend" from this README and follows them in a fresh folder:
  a new virtual environment with every package installed, `.env` made from `.env.example` with real values, migrate,
  start. Then the first journey runs (scripted agent, `AGENT_TEST_MODE=fake`): register, verify with the link the
  console mailer printed, create a project, run, approve, download.
- **D02:** the migration command on a database one file behind applies only the new file;
  `python -m app.migrate --status` lists it as pending before.

Fixed during this phase (found by the tests):
1. B11-S03: a null character in any text field (for example a project name) reached PostgreSQL, which cannot
   store it: 500. Every request body now refuses it with 422 (a shared base for the request models).
2. B11-S07: a run whose code could not be copied (zip refused, clone failed, cancel before the copy) left its run
   folder behind, with the uploaded zip, until the 24-hour cleanup. The finish step now deletes that folder at once.
3. B11-R01: if the database went away while the agent worked, the agent failed, and saving "failed" failed too. The
   run then stayed active with nothing working on it until the next server restart. The runner now keeps trying to
   save the end (1 s, 2 s, 4 s ... up to 30 s apart) until the database is back.
4. Test helper (not app code): the fake test runner shared one list of answers across parallel runs, so one run
   could take another run's answer. Answers are now decided per call.

Decisions made where the design docs were silent (please check):
1. B11-E01 to E07 ran with a scripted model instead of the real Groq (not reachable here), and E02 with a local
   folder instead of a GitHub repo. The real-Groq versions stay in "Open items".
2. Long load tests: the full lengths run with `SANGRA_LONG_TESTS=1`; the normal suite runs them for 60 to 90 s.
3. L01 "p95 under 500 ms for normal routes": the live stream is not counted, since it stays open by design; login
   is counted (323 ms).
4. A null character is refused in every text field (422), as no field needs one.

### FE0: Frontend project setup (notes)

The screen designs (19 screens, given before the frontend started) are kept in `design/screens/`. They follow the design
documents (the same pages, routes, components, stop-reason texts and error codes) and add the look that
05_frontend_design.md left open.

Built (in `frontend/`):
- Vite + React 19, React Router (the route table of F1, `src/App.jsx`), TanStack Query (`src/queryClient.js`), started
  in `src/main.jsx`.
- `src/config.js`: the API address from `VITE_API_URL`; missing or empty means the documented default
  `http://localhost:8000/api/v1`; a trailing slash is dropped.
- The folder layout of F5: `src/api`, `src/auth`, `src/hooks`, `src/pages`, `src/components`, `src/styles`.
- `src/styles/tokens.css`: every colour, radius and size from the foundations sheet ("Phosphor Amber", dark only).
  `base.css` holds the fonts (Inter and JetBrains Mono, served with the app), base text and the button styles.
- The "page not found" page as in its screen. The pages of later phases are stand-ins that name their phase; each is
  replaced in that phase.
- Lint (ESLint), unit tests (Vitest with jsdom and Testing Library), browser tests (Playwright, against both
  `npm run dev` and the built app), and `scripts/check-build.mjs` (scans the built files for secrets).

Test results:
- Unit (`tests/unit/fe0_setup.test.jsx`): 22 of 22 pass. They cover F01 (every F1 route has its page; `/` goes to
  /projects), F03 and X01 (the address from the env file, and the default when it is missing or blank), and X02
  (three unknown URLs show the 404 page).
- Browser (`tests/e2e/fe0_setup.spec.js`): 6 of 6 pass in Chromium against `npm run dev` (F01) and against the
  production build through `npm run preview` (F02). They check the first page, the 404 page and its link, that the
  design colours and both fonts load, and that the console has no errors.
- Lint passes with no problems (F04).
- Build: it builds in under 1 s; the main script is 285 KB (89 KB zipped). The secret scan of the built files finds
  nothing (S01).
- Backend suite: 607 of 607 still pass. The first run had 2 failures because the container had restarted and Docker was down; after Docker was started, the whole suite was run again and passed.

Not done here:
- **FE0-B01 in Firefox.** Only Chromium is installed on the build machine, and downloading browsers is not allowed
  there. The same tests run in Firefox with `E2E_FIREFOX=1 npm run test:e2e` on a machine that has it
  (`npx playwright install firefox`).
- **FE0-F03 on the built files.** The address could not be checked in the bundle yet: nothing sends requests in FE0,
  so the bundler leaves the unused setting out. `check-build.mjs` takes the expected address and checks the bundle once
  the API client exists (FE1).

Decisions made where the design docs were silent (please check):
1. Styling: plain CSS with the design tokens as CSS variables, with no component or CSS library. The screens
   define every colour and size, so a library would add weight without helping.
2. Fonts: Inter and JetBrains Mono are packaged with the app (`@fontsource`), not loaded from Google: no request to
   another site, and they also work offline.
3. JavaScript (`.js`/`.jsx`), not TypeScript, as the file names in 05 (client.js, App.jsx) show.
4. Exact versions are pinned: React 19.3.1, React Router 7.18.4, TanStack Query 5.104.5, Vite 8.3.5, Vitest 5.0.5,
   MSW 2.15.0 (for FE1), ESLint 10. Playwright is 1.56.1, the version of the Chromium installed here. jsdom is
   29.1.1, because jsdom 30 needs Node 22.22.2 or newer.
5. Ports: dev server 5173 (the FRONTEND_URL and CORS default of the backend), preview 4173.
6. The screen designs are kept in the repository (`design/screens/`), so every later phase can be checked against them.

### FE1: Frontend API layer (notes)

Built (in `frontend/src/api/`, as F2 describes):
- `token.js`: the JWT in localStorage (`sangra_token`), with every access in try/catch. When storage is blocked, the
  token lives in memory for the session.
- `client.js`: the only place that calls `fetch`. It adds the base URL and the JWT header; sends objects as JSON and
  FormData as it is; returns what is inside `{ data }`; and throws one `ApiError { status, code, message }`:
  - `NETWORK_ERROR` when no answer arrives;
  - `UNKNOWN_ERROR` for a reply that is not our JSON;
  - `TIMEOUT` after 30 s (10 minutes for uploads).

  The 401 rule: a 401 on a protected route clears the token and calls the auth state's function once, even when
  several requests fail together; the public auth routes are exempt and never send the token. Only paths inside the
  API are accepted, so the token cannot be sent anywhere else.
- `auth.js`, `projects.js`, `runs.js`: the 22 route functions of F2.2. `startRun` sends FormData when there is a zip.
  `downloadPackage` fetches the file with the JWT and saves it under the server's file name, or
  `sangra-run-<id>.zip` if that name is missing or odd.
- `stream.js`: `openRunStream(runId, onEvent, { onEnd, onError, onReconnecting, onBadEvent })` returns `stop()`.
  - It reads the event stream with `fetch` and joins pieces cut anywhere, even inside a multi-byte character or
    between `\r` and `\n`.
  - It skips pings and numbers it has already seen.
  - It ends with `onEnd` at the final `status_changed` event.
  - After a drop or a close without the final event, it reconnects after 2, 4, 8, 16 and 32 s with
    `Last-Event-ID`; after 5 failed tries it calls `onError` once.
  - A 404 (or another 4xx) stops at once; a 401 follows the 401 rule.
  - After `stop()`, no callback is called.

Test results:
- Unit tests: 161 of 161 pass (Vitest; MSW as the fake backend for the client; a scripted stream backend whose chunks,
  drops and closes the test controls).
  - `tests/unit/api/fe1_client.test.js` (34): F01-F06, X01-X07, S01, S02, blocked storage, a request stopped by
    the page (ABORTED).
  - `tests/unit/api/fe1_routes.test.js` (76): F10 (all 22 functions: method, path, body, auth header), F12, F13,
    X10, X11 (every function with a 500 and with a network failure).
  - `tests/unit/api/fe1_stream.test.js` (28): F20-F28, X20-X25, S20, L01-L03.
  - `tests/unit/api/fe1_stream_react.test.jsx` (1): X26, React's development mode; then switching run 5 to run 6,
    then closing.
- Against the real backend (`npm run test:contract`), 2 of 2 pass:
  - **F11:** the backend's own route list (`/openapi.json`) and our functions plus the stream match exactly
    (23 routes; `/health` has no function, as in F2.2).
  - **F30:** register, verify with the link from the mail file, log in, me, create a project, start a run (scripted
    agent), stream it to the end while approving the fix: 16 events numbered 1..16, ending with `succeeded`. Then the
    package downloads (`sangra-run-1.zip`, 1,235 bytes).
- Numbers: 10,000 events in 108 ms (limit 2 s, L01). One 1 MB event in 1-byte chunks: 1.9 s, correct (L02). 100
  open/close cycles leave no connection, reader or timer (L03).
- Lint clean; FE0's tests still pass (6 browser tests, 22 unit tests); build scan clean. Backend: 607 of 607 pass (after the pool change below).

Fixed during this phase (found by the tests):
1. FE1-L02: the first stream parser rebuilt the whole unfinished text for every chunk (quadratic time: a 1 MB event
   in 1-byte chunks never finished). It now keeps the pieces in a list and joins them once per line: 1 MB takes 91 ms
   in the parser.
2. FE1-F30: the project list from the backend is `{ items, total }`, not a plain list. The contract test caught it
   before any page used it.

Backend change during this phase (B3-L01 started failing; the target was not changed):
- **What happened:** the backend suite run for FE1 failed B3-L01 ("`/health` under 200 ms while 50 users log in at
  once"). Run alone it still failed: 179 to 286 ms. It measured 60 ms in B3 and passed in every full run up to
  B11; the container had restarted since.
- **Cause:** the `/health` database ping waited for a free pool connection. The pool started with 1 connection
  (my B1 choice, `DB_POOL_MIN_SIZE=1`). The burst of 50 logins came right after startup, while the pool was still
  opening the other 9, and that is slow while bcrypt keeps every core busy. The garbage collector and the event loop
  were checked and ruled out. Neither the bcrypt hash time (291 ms, the same as in B2) nor the health code had
  changed. A stray test process from my own first FE1 run also used one core for a while; it was stopped, and the
  test still failed without it.
- **Fix:** the whole pool is opened at startup (`DB_POOL_MIN_SIZE` defaults to 10, the pool size of the design; a
  minimum larger than the maximum is capped). B3-L01 now measures 92 to 109 ms (3 runs). **Please check this
  change.** The other option is to keep 1 and decide the target again.

Not done here:
- FE0-F03 on the built files: the pages do not use the API layer yet, so the bundler still leaves it out. F30 shows
  that VITE_API_URL decides where requests go (the tests pointed it at a backend on a random port). The check of the
  built files follows in FE2, when the first page sends requests.

Decisions made where the design docs were silent (please check):
1. New client error codes: `TIMEOUT` (no answer within 30 s, or 10 minutes for uploads and downloads), `ABORTED`
   (stopped by the page, for example when leaving during an upload) and `BAD_PATH` (a path outside the API, never
   sent).
2. The public auth routes (register, login, verify, forgot, reset) never send the token, as they do not need it.
3. Stream: the reconnect count starts again when an event arrives, so a long run with a few short drops does not
   give up. A 5xx answer is retried like a drop; any other error answer (404, 403 ...) stops at once.
4. Stream: an event whose data is not JSON is skipped and passed to `onBadEvent` (or written to the console as a
   warning when the page gives no callback).
5. Downloads accept only a plain file name from the server (letters, digits, `.`, `_`, `-`), otherwise
   `sangra-run-<id>.zip`.
6. The API tests run in Vitest's Node environment, whose `fetch`, `FormData` and streams behave like a browser's
   (jsdom's FormData does not work with Node's fetch). The React test runs in jsdom.

### FE2: Auth state, route guards and auth pages (notes)

Built (05_frontend_design.md F1, F3, F5; the screens login, register, verify-email (3 states), forgot-password
(2 states), reset-password, account and the layout):
- `src/auth/AuthProvider.jsx` and `useAuth.js`: the login state. It holds the token (via `api/token.js`), the user
  and a status: loading, loggedIn, loggedOut, or unreachable (the backend could not be reached at start). It offers
  login, register (logged in at once), logout (the backend is not called), refreshUser and retry. It also registers
  the 401 function, which shows "Session ended, please log in again" on the login page.
- When the app opens:
  - no token: logged out, with no request;
  - a token that is not JWT-shaped: removed;
  - otherwise a spinner while `getMe` runs (no flash of the login page): a 401 removes the token quietly, and a
    network error keeps it and shows "Cannot reach the server" with a retry button.
- `src/auth/guards.jsx`:
  - `ProtectedRoute`: a logged-out user goes to `/login?returnTo=<page>`, and back there after login; after an
    explicit logout, plain `/login`.
  - `PublicOnlyRoute`: a logged-in user goes to the `returnTo` page if it is inside the app, else `/projects`.
  - `AnyAuthRoute`: used by `/verify-email`.
- Pages: Login, Register, VerifyEmail, ForgotPassword, ResetPassword, Account. Each form checks the backend's rules
  before sending (`src/validation.js`: name 1-100, email shape, password 8+ characters with a letter and a digit and
  at most 72 bytes, confirm field). It focuses the first wrong field, links each error to its field, and keeps its
  button disabled while sending (one request). Name and email are trimmed. Backend messages are shown as text
  (`src/errors.js`).
  - **Verify:** calls the backend exactly once (also in React's development mode), removes the token from the
    address bar, then shows success, or "This link is invalid or expired".
  - **Reset:** has a confirm field; success goes to the login page; a bad or missing link shows the error with a
    "get a new link" link.
  - **Account:** shows the email, verified or not, resend (only when not verified), change password (success logs
    out with "Password changed, please log in again"; a wrong old password stays on the page) and log out.
- `src/components/Layout.jsx`: the top bar (brand, projects, history, account) and the verify banner with a resend
  button on every logged-in page. The "run in progress" banner comes in FE7, once runs exist (FE4).
- Shared pieces: `Form.jsx` (Field, PasswordField with show/hide, SubmitButton), `Status.jsx` (Spinner, Notice,
  full-page states), `AuthLayout.jsx`, the styles in `src/styles/components.css`. Screenshots were compared with
  the designs and match.

Test results:
- Unit tests: 234 of 234 pass (74 new FE2 tests; the FE0 route tests now go through the guards).
  - `tests/unit/fe2_auth_state.test.jsx` (27): F01-F07, X01-X07, S01, S02, L01 (50 app openings with a valid
    token leave no handler behind and the heap does not grow).
  - `tests/unit/fe2_pages.test.jsx` (47): F10-F17, X10-X17, S10-S12, U02.
- Browser tests (Playwright, Chromium, dev server and built app): 11 pass (1 is skipped by design: the built-files
  check does not run on the dev server). New:
  - FE2-U01: register, log out and log in using only the keyboard; the focus order is name, email, password,
    show, and on login email, "Forgot password?", password.
  - Login with a wrong password.
  - FE0-F03 on the built app: requests go to the address from the env file.
- FE0-F03 is also checked in the built files: a build with `VITE_API_URL=http://api.example.test:9999/api/v1`
  contains that address, and `npm run check:build` now always checks the address (from the environment, `.env`,
  or the default).
- Contract tests against the real backend: 2 of 2 still pass. Lint: no problems. Backend: 607 of 607 pass.

Fixed during this phase (found by the tests):
1. FE2-X06: when another tab had logged out, this tab's next request went without a token, and the 401 rule ignored
   that answer, so the tab stayed "logged in". The rule now also applies to a request sent without a token; the auth
   state ignores a second call.
2. FE2-U01: after "log out" on the account page, logging in again went back to the account page instead of
   /projects. Logout now goes to plain /login, as F3 says; only a session that ended by itself remembers the page.
3. Looks (compared with the screens): the "log out" card is one row, the banner's resend button is cyan, the
   account page's password hint is in the mono font.

Decisions made where the design docs were silent (please check):
1. The reset page has a "Confirm new password" field, as the plan (FE2-F15) asks, although its screen shows one
   field.
2. The verify page when logged out and the link is bad: "Log in, then send a new link from your account page." with
   a "log in" button (the screen shows the logged-in version with "go to account").
3. After a reset the login page says "Password changed, please log in"; after a change on the account page, "Password
   changed, please log in again" (F1).
4. `returnTo` is kept only for a path inside the app (starting with one `/`, no `//`, `://`, backslash or control
   characters).
5. A saved token that is not shaped like a JWT (three dot-separated parts) is removed without asking the backend.
6. Password fields have a "show/hide" text button inside the field (FE2-S12); the screens do not show one.

### FE3: Projects pages (notes)

Built (05_frontend_design.md F4, F5; the screens "projects · ProjectsPage", "projects · empty + unverified user" and
"projects new · projects id edit · ProjectFormPage"):
- `src/pages/ProjectsPage.jsx`: the list as a table: name, language, source (type, then the URL or path; zip shows
  "zip uploaded per run"), branch ("default" when empty), test command, and run / edit / delete. It shows a spinner
  while loading, an error with a retry button, and the empty state "No projects yet - create one".
  - **Run** opens `/projects/:id/run`. It is disabled with the reason beside it when the email is not verified
    ("Verify your email to start runs") or a run is already in progress.
  - **Delete** opens a confirm box ("Delete project?", "<name> and all its runs will be deleted."). Errors such as
    PROJECT_HAS_ACTIVE_RUN are shown above the list.
  - Long names, URLs, paths and commands are cut with "…", and the full text is in a tooltip.
- `src/pages/ProjectFormPage.jsx`: one form for new and edit.
  - Fields: name; language and source type as choice chips; the GitHub URL or folder path (hidden for zip); the
    branch (github only, optional); the test command.
  - It checks the backend's own rules before sending (`src/validation.js`, the same as `project_rules.py`).
  - Edit fills the form and sends only the fields that changed: a cleared branch is sent as null, and switching to
    zip clears the reference.
  - Opening the edit page of a project that is not yours, or does not exist, shows the "page not found" page.
    Saving a project deleted meanwhile goes back to the list with a message.
- `src/hooks/queries.js`: `useProjects`, `useProject`, and `useActiveRun` (GET /runs?status=active, refreshed every 30
  s; FE7 adds the banner on top of it), with the keys of F4. Lists refresh after create, edit and delete.
- Shared: `ConfirmDialog` (the focus starts on "cancel", Tab stays inside, Escape closes, the focus returns to the
  button that opened it) and `EmptyState`.

Test results:
- Unit tests: 282 of 282 pass (48 new in `tests/unit/fe3_projects.test.jsx`: F01-F05, X01-X07, S01, S02, U01, U02,
  L01).
- Browser tests (Chromium, dev and built app): 17 pass, 1 skipped by design. New:
  - FE3-U01 at 1280 and 768 px: 100-character names, a long GitHub URL, a long Windows path and a 500-character
    command do not cause sideways scrolling.
  - FE3-U02: the delete box by keyboard, including deleting with Enter.
- FE3-L01: 20 projects render in 86 ms (limit 200 ms; measured in the test DOM, which is slower than a browser).
- Screenshots of the list, the form and the delete box were compared with the designs and match.
- Lint: no problems. Build scan clean. Contract tests: 2 of 2 pass. Backend: 607 of 607 pass. (The container restarted during the first backend run, which then failed with PostgreSQL and Docker down. After they were started again the whole suite was run again and passed.)

Not done here:
- **Firefox** (FE0-B01 and the later browser checks): installing it failed because this environment's network policy
  blocks `cdn.playwright.dev` and `playwright.download.prss.microsoft.com` (403). After that host is allowed in the
  environment's network settings, `npx playwright install firefox` and `E2E_FIREFOX=1 npm run test:e2e` run every
  browser test in Firefox.

Decisions made where the design docs were silent (please check):
1. The branch field shows only for GitHub projects: a local folder or a zip has no branch to choose.
2. When Run is blocked by a run in progress, the reason reads "A run is already in progress"; the "run in progress,
   open it" banner with the link comes in FE7.
3. Help text per source type: github "The agent clones the repo for each run." (from the screen); local_path "The
   agent copies this folder for each run (only when the backend runs on your own machine)."; zip "You upload a .zip
   of the code with each run."
4. Saving an edit with no change sends nothing and goes back to the list.
5. Below 900 px wide the list hides the branch and test-command columns so the actions stay visible.

### FE4: Start run page and History page (notes)

Built (05_frontend_design.md F1, F4, F5; the screens "projects id run · StartRunPage (zip project)" and
"runs · HistoryPage"):
- `src/pages/StartRunPage.jsx`, at `/projects/:id/run`:
  - The project's name, language, source type and test command as tags, then the bug text with a live counter
    ("0 / 2000 · min 10").
  - For zip projects only, the zip picker: drop or browse, then "name · size" with remove. The file is checked
    before any upload (`.zip`, at most 50 MB, not empty).
  - Start is disabled while the text is shorter than 10 or longer than 2,000 characters (after trimming), the zip
    is missing or wrong, or the email is not verified. It sends JSON, or FormData with the zip, and opens
    `/runs/:id`.
  - ACTIVE_RUN_EXISTS shows "You already have a run in progress" with "open it" (the link comes from the "active"
    filter). Other errors (EMAIL_NOT_VERIFIED, ZIP_REQUIRED, NOT_FOUND …) are shown on the page.
  - Leaving the page during an upload stops it. The page of a project that is not yours shows "page not found".
- `src/pages/HistoryPage.jsx`, at `/runs`:
  - The runs as a table (run, project, status badge, date as "22 Oct 2026, 14:05" in the browser's time zone,
    "open →"), 20 per page with prev / next and "page X of Y".
  - Filters for project and status (all, active, or one status). Filters and page are kept in the address
    (`?project=2&status=failed&page=2`); odd values (page=abc, -1, 0, 1.5, status=foo) fall back to the defaults.
  - A click on a row opens the run.
  - Empty states: "No runs yet - start one from a project" (with "start a run" → projects), "No runs match these
    filters", and a page past the end ("There are no runs on this page" with "show all runs").
- `src/components/StatusBadge.jsx` and `src/runStatus.js`: the badge colours of the foundations sheet (cyan = agent
  working, amber = waiting for you, green = succeeded, red = failed, grey = stopped) and the date format.
  `src/runRules.js`: the shared rules and texts.
- After a start, the history and the active run are refreshed (F4 keys).

Test results:
- Unit tests: 317 of 317 pass (35 new in `tests/unit/fe4_runs.test.jsx`: F01-F04, X01-X08, S01, L02).
- Browser tests (Chromium, dev and built app): 23 pass, 1 skipped by design. New:
  - The zip upload in a real browser sends `zip_file` with the file name, then opens the run page.
  - FE4-L01: 500 runs. On the built app the first page shows in 295 ms (limit 1 s, including the start of the
    app) and a filter change takes 146 ms (limit 500 ms). On the dev server: 639 ms and 164 ms.
  - FE4-L02: a 2,000-character bug text is sent whole and causes no sideways scrolling.
- Screenshots of both pages were compared with the designs and match.
- Lint: no problems. Build scan clean. Contract tests: 2 of 2 pass. Backend: 607 of 607 pass (after the test fix below).

Decisions made where the design docs were silent (please check):
1. The bug text limits (10 to 2,000) are counted after trimming spaces, as the backend does; the counter shows that
   count.
2. The history keeps the filters in the address as `project`, `status` and `page` (page 1 and empty filters are
   left out). The status filter offers "active" too (the backend's special value).
3. Dates are shown in the browser's time zone.
4. A page past the end shows an empty state with "show all runs"; a page number is never corrected silently.
5. The start button reads "uploading…" while a zip is sent and "starting…" otherwise.

Backend test fixed during this phase:
- **B10-F06** ("the cleanup jobs are scheduled") failed once in the full run, and 1 time in 5 when run alone. The
  test waited only until the file was deleted, then stopped the app. The job deletes the file first and marks the row
  (`deleted_at`) right after, so a stop in between left the row unmarked; the next cleanup marks it, since a missing
  file counts as deleted. The app was right. The test now waits for both steps: 8 of 8 runs pass.

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
