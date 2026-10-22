# SANGRA_AI: working rules for Claude

## How the work is done
- Build strictly from the design documents (01 to 05) and the phase plan (06_implementation_plan.md). Do not invent
  features or assume behaviour; where a document is silent, choose the simplest option and list it under
  "Decisions made where the design docs were silent" in the README for review.
- Order: all backend phases (B0 to B11) first, then the frontend (FE0 to FE7). One phase at a time.
- A phase is done only when every test of that phase passes AND every earlier test still passes.
- A failing load (L) target is never silently changed: report the numbers and let the owner decide.

## After EVERY phase (always, without being asked)
1. Run the whole test suite (`cd backend && .venv/bin/pytest -q`), not only the new phase.
2. Update README.md: the progress table, a "Bx: ... (notes)" section with what was built, test results and numbers,
   and the decisions made where the docs were silent.
3. Commit (message starts with the phase id, e.g. "B6: ...") and push to GitHub right away.
4. If the owner merged the working branch into `main`, move the unmerged commits onto the new `main` first
   (rebase), then push, so the branch always sits on top of `main`.
5. Report to the owner what was built and which tests passed, then wait for approval before the next phase.

## Environment notes
- Tests need PostgreSQL: `pg_ctlcluster 16 main start` if it is down (the container may have restarted).
- Test database: `TEST_DATABASE_URL` (default in `backend/tests/conftest.py`).
