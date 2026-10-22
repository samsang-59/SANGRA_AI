# SANGRA_AI

**An AI coding agent that fixes bugs, with a human in the loop.**

You describe a bug in plain English. SANGRA_AI searches your code, finds the wrong lines, shows you the fix,
**waits for your approval**, applies it to a safe copy, and runs your tests inside Docker. If the tests fail,
it undoes the change and tries again (up to 3 times). Your original code is never touched.

> **Status:** design phase complete. This repository currently holds the full system design and the
> phase-wise implementation plan. Code is built next, phase by phase (see [the plan](06_implementation_plan.md)).

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

## Known limits (MVP)

- Keyword search does not catch every bug; the LLM tries related words but it will miss some.
- Public GitHub repos only (no private repo tokens yet).
- Docker Desktop must be installed.
- Internet is allowed inside the test container for Maven / pip downloads (to be tightened later).
- If the baseline tests already pass, a passing result is labelled *"tests passed, but no test covers this bug, please verify the fix yourself"*.

---

## Author

Sangram Ganta
