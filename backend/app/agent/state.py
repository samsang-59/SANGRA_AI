"""The agent's state ("the backpack", 04_backend_design.md 6.3). Only small plain data, no secrets, and one source of
truth: when our tables hold a fact, the state keeps only a pointer.

NOT in the state: the counters (rejects, failed tests, hints live in the runs table), the LLM chat, whole files,
full test logs (saved as files in artifacts/), the Groq client.
thread_id (the run id as text) is the only link to LangGraph's tables; run_id inside the state is a label for our
own steps.
"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # Setup
    run_id: int
    user_id: int
    bug_description: str
    language: str
    test_command: str
    workspace_path: str            # workspaces/<user_id>/<run_id> (code/ and artifacts/ are inside)

    # Investigation
    hints: list[str]
    search_terms_tried: list[str]
    findings: list[dict]           # file, line range, a few numbered lines of code, a note
    investigate_steps_used: int

    # Baseline
    baseline_summary: str          # passed / failed + a short excerpt
    baseline_passed: bool

    # Draft (not in the database until it passes check_proposal)
    draft_edits: list[dict]
    draft_explanation: str
    proposal_checks_used: int
    check_problems: list[str]
    next_reason: str               # first | after_reject | after_test_failure (for the next attempt)

    # Current try
    current_attempt_id: int
    reject_feedback: str
    last_test_failure: str

    # Flow control: a small word that tells the graph which arrow to follow
    outcome: str

    # Ending
    final_status: str
    stop_reason: str
    final_summary: str
