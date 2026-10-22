# SANGRA_AI - Step 2: Tech Stack

(Written in simple language. Date: 2026-10-20)

## 1. The stack at a glance

| Part | Choice | Simple reason |
|---|---|---|
| Backend language | Python | LangGraph is Python, so the agent lives inside the backend. One service, not two |
| Backend framework | FastAPI (async) | Async fits how you work. Layers fit well |
| Agent framework | LangGraph | Controls the flow, pauses for human approval |
| LLM provider | Groq | Your choice |
| Main model | openai/gpt-oss-120b (kept in a config / .env, never hardcoded) | See section 2 |
| Optional cheaper model | openai/gpt-oss-20b (for easy steps like picking search keywords) | Cheaper, faster |
| Database | PostgreSQL | Real-world choice. Handles many users. Runs easily in Docker |
| DB driver | psycopg 3 (async) | LangGraph's Postgres checkpointer uses psycopg 3. One driver for both |
| DB access style | Raw SQL, written inside repositories | You are learning SQL, and repositories own all SQL |
| Table creation / changes | Plain numbered .sql files (001_users.sql, 002_runs.sql ...) run by a small script | Fits raw SQL. You can read exactly what changes |
| Checkpointer | LangGraph AsyncPostgresSaver (langgraph-checkpoint-postgres) | Saves agent state so it can pause and resume |
| Validation | Pydantic (comes with FastAPI) | This is the "validators" layer |
| Settings / secrets | .env file read by pydantic-settings | Groq key, DB URL, JWT secret stay out of code |
| Login | JWT (token after email + password login) | Like a wristband at an event |
| Password storage | Hashed with bcrypt | Never plain text |
| Live progress | Server-Sent Events (SSE) | Server pushes steps to the browser. Accept / Reject uses a normal request |
| Running tests | Docker (Docker SDK for Python) | Tests run in a locked box |
| Code search (Tool 1 and 2) | Plain Python file walking + text matching | No extra program. Can swap to ripgrep later if slow |
| Git (undo, branch) | The git command, called from Python | Simple and predictable |
| Sending emails | An email service (SMTP or a provider, to be chosen). In development the link can be printed in the console | Needed for email verification and password reset |
| Backend tests | pytest | Standard |
| Frontend | React + Vite (your choice, 2026-10-22). Written by Claude; designs from Claude Design | Backend first, frontend after |

## 2. About the Groq model (checked 2026-10-20 from Groq docs)

- Groq says its hosted models support tool calling.
- llama-3.3-70b-versatile and llama-3.1-8b-instant were scheduled to shut down on
  16 Aug 2026 (Groq recommends openai/gpt-oss-120b and openai/gpt-oss-20b instead).
  The Groq models page still lists them, so Groq's own pages disagree. We do not depend on them.
- openai/gpt-oss-120b and openai/gpt-oss-20b are listed as Production models.
  qwen/qwen3.8-27b is Preview ("may be discontinued at short notice").
- The gpt-oss models do not support parallel tool calls. This is fine, because our flow is one step at a time.
- Groq retires models often. So the model name must be easy to change (one config line).
- HONEST CAVEAT: we confirmed tool calling is supported, NOT how good the model is at fixing code.
  We will find that out by testing, so keep the model easy to swap.

## 3. Where code copies and results live (storage plan)

Idea: the temporary copy exists only while the run is going. After that, only a small
download package is kept.

For each run:
1. The agent makes a TEMPORARY COPY of the code (original is never touched).
2. Tests run in Docker on that copy.
3. When the run ends, the agent builds a DOWNLOAD PACKAGE.
4. The temporary copy is deleted right away.
5. The package is kept for 7 days (or until the user deletes the run), then deleted.

The package contains (small, not the whole codebase):
- the changed files (fixed versions)
- a patch / diff file
- test results and logs: baseline run, final run, and any failed tries

Failed runs: if all 3 tries fail, we STILL make a package with the last attempted change
and all test logs, so the user can see why it failed.

Two modes:
- Local mode (backend on the user's machine): "Apply to my folder" copies the files
  from the package into the original folder, only when the user clicks it.
- Deployed mode (backend on a server): the user downloads the package.

The database never stores code copies. It stores only small records: users, runs, bug text,
diffs, approvals, test results, and the package location. Because diffs are saved in the
database, the History page can still show what changed after the copy is deleted.

## 4. What the user needs to install (for development)

- Python (3.10 or newer; the LangGraph Postgres package needs 3.10+)
- Docker Desktop (for Postgres and for running tests)
- Git

## 5. Things we still must check or decide later

- Exact versions of packages. We check them when we start implementation.
- Docker images for Java (JDK + Maven) and Python, and how libraries are cached.
- For the MVP, internet is allowed inside the test container (needed for Maven / pip downloads).
  We tighten this later.
- Frontend libraries (router, styling, etc.) are decided in the frontend design step.
- Optional global limit on total runs at once, if the machine struggles.

## 6. Sources

- Groq tool use: https://console.groq.com/docs/tool-use/overview.md
- Groq models: https://console.groq.com/docs/models.md
- Groq deprecations: https://console.groq.com/docs/deprecations.md
- LangGraph Postgres checkpointer: https://pypi.org/project/langgraph-checkpoint-postgres
