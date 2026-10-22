# SANGRA_AI - Step 1: Brainstorm

(Written in simple language. Date: 2026-10-20)

## 1. What is SANGRA_AI?

A coding agent that fixes bugs. You tell it the bug in plain English, and it finds the
wrong code, shows you the fix, waits for your approval, and then tests the fix.

Simple example: A junior developer at Amazon is told "The summer sale shows 5% discount,
it should be 10%." He does not read the whole codebase. He searches for the word
"discount", opens only those files, finds the wrong line, asks his senior "Can I change
this?", changes it after approval, and runs the tests. SANGRA_AI does the same thing.

## 2. The flow (step by step)

1. User gives: bug in English + code (folder / GitHub link / zip) + language + test command.
2. Agent makes a safe COPY of the code (original is never touched).
3. Agent runs the tests once BEFORE any change (baseline), so it knows what was already failing.
4. Tool 1 searches the codebase and returns files that may have the bug.
5. LLM picks which files to look at.
6. Tool 2 searches inside those files and returns the lines related to the bug.
7. LLM prepares the fix (a list of changes: file, lines, old code, new code). Nothing is written yet.
8. Graph PAUSES and asks the human: Accept or Reject (all changes shown together).
9. If Accept: Tool 3 applies the changes to the copy.
10. Tool 5 runs the tests inside Docker.
11. If tests pass: done. If tests fail: undo the changes, remember what failed, try again (max 3 tries).
12. If the user clicks Reject: agent asks for feedback, reads it, and proposes a different fix.

## 3. Important design corrections (from our discussion)

- Tools do NOT call other tools. The graph (LangGraph) controls the order. Tools stay simple.
- Human approval is NOT a tool. It is a "pause" in the graph. The graph saves its state,
  waits for the human, and continues when the human answers.
- Tool 3 is split in two ideas: "prepare the fix" (no writing) and "apply the fix" (writes
  only after approval).
- On failure we do NOT start again from the codebase. All memory (bug, files found, lines
  found, fix tried, test errors) is kept in the graph state, and the LLM uses it for the next try.

## 4. Decisions we made

| Topic | Decision |
|---|---|
| Name | SANGRA_AI |
| Agent language | Python |
| Agent framework | LangGraph |
| LLM provider | Groq (we must check which Groq model handles tool calling well) |
| Code languages supported | Java and Python |
| Code input | Local path (only when running on your own machine), GitHub URL (public only), Zip upload (for deployed mode) |
| Frontend | Web app |
| Backend style | Router -> Controller -> Service -> Repository -> DB (async). AI layer kept separate. |
| Database | Yes. App database (users, runs, history) + LangGraph checkpointer |
| Login | Yes. User accounts and login are required |
| Run place | Your machine first, deploy later |
| Test running | Inside Docker from the start |
| Test command | Given by the user |
| Original code | Never touched. Agent works on a copy |
| Undo | If the project uses git, undo with git. If not, take a backup before applying changes |
| Fix size | Fix can be in many places (many files / many lines), shown as one change set |
| Tries | Max 3 tries after test failure, then stop and report |
| Reject | Agent asks for feedback and proposes a new fix |
| Baseline tests | Yes, run tests before the fix |
| Git push | Agent never pushes by itself. Only if the user asks (separate optional tool, with approval) |
| Bugs per run | One bug per run |
| Runs at the same time | One active run per user. Different users can run at the same time. If a user starts a second run while one is active, the app says "wait, your first run is still going". We design for real-world use (many users) from the start |
| Live progress | Yes, user sees the agent's steps while it works |
| Past runs | History of old runs is stored and shown |
| After tests pass | User sees the final changes and clicks "Apply to my folder". Only then are the changes copied to the original. For zip / GitHub input, the user downloads the changed files |
| Bug not found | Agent asks the user for a better hint. Max 3 times, then stops |
| MCP server | Last phase. A thin wrapper around the finished agent |

## 5. Safety rules (simple version)

1. Work on a copy, like photocopying a notebook before someone writes on it.
2. Stay inside the project folder. Every tool refuses paths outside the workspace.
3. Hide secrets. The Groq key must not be visible to the tests.
4. Set limits: timeout for tests, cap on output size, cap on file size.
5. Tests run inside Docker, so even bad code can only damage the box, not the machine.

## 6. Known limits (honest list)

- Searching by keyword works for "discount", but many bugs cannot be found by a keyword.
  The LLM can try related words, but it will not catch everything. This is fine for the MVP.
- Docker Desktop must be installed on the Windows machine.
- Java (Maven) and Python (pip) need internet inside the container to download libraries.
  MVP: allow internet inside the container. Tighten later.
- Public GitHub repos only for now (no private repo tokens).

## 7. Things to decide in later steps

- Tech stack: exact Groq model, FastAPI, DB type, login method (e.g. JWT), live progress method.
- Docker details: images for Java and Python, how dependencies are cached.
- Optional global limit on total runs at once, if the machine struggles with many Docker containers.
- How the LangGraph checkpointer and the app database work together.
- Frontend screens (designed after the backend).
