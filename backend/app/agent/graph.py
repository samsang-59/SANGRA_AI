"""The graph (04_backend_design.md 6.2): a FIXED flow. Only the investigate step lets the LLM move freely.

prepare_workspace -> run_baseline_tests -> investigate -> (found) propose_fix -> check_proposal -> request_approval
-> wait_for_approval -> (accepted) apply_edits -> run_tests_after_fix -> decide_next -> finish
investigate -> (not found) request_hint -> wait_for_hint -> investigate
check_proposal -> (check failed, under 3) propose_fix; wait_for_approval -> (rejected, under 3) propose_fix;
decide_next -> (tests failed, undone, under 3) propose_fix. Every "finish" arrow ends the run.
"""

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.steps import AgentSteps
from app.agent.state import AgentState


def _route(mapping: dict[str, str]):
    def route(state: AgentState) -> str:
        outcome = state.get("outcome", "end")
        if outcome == "finish":
            return "finish"
        if outcome == "end":
            return END
        return mapping[outcome]

    return route


def build_graph(steps: AgentSteps) -> StateGraph:
    graph = StateGraph(AgentState)
    for name in ["prepare_workspace", "run_baseline_tests", "investigate", "request_hint", "wait_for_hint",
                 "propose_fix", "check_proposal", "request_approval", "wait_for_approval", "apply_edits",
                 "run_tests_after_fix", "decide_next", "finish"]:
        graph.add_node(name, getattr(steps, name))

    graph.add_edge(START, "prepare_workspace")
    graph.add_conditional_edges("prepare_workspace", _route({"ok": "run_baseline_tests"}))
    graph.add_conditional_edges("run_baseline_tests", _route({"ok": "investigate"}))
    graph.add_conditional_edges("investigate", _route({"found": "propose_fix", "not_found": "request_hint"}))
    graph.add_conditional_edges("request_hint", _route({"wait": "wait_for_hint"}))
    graph.add_conditional_edges("wait_for_hint", _route({"ok": "investigate", "wait": "wait_for_hint"}))
    graph.add_conditional_edges("propose_fix", _route({"check": "check_proposal", "retry": "propose_fix"}))
    graph.add_conditional_edges("check_proposal", _route({"ok": "request_approval", "retry": "propose_fix"}))
    graph.add_conditional_edges("request_approval", _route({"wait": "wait_for_approval"}))
    graph.add_conditional_edges("wait_for_approval", _route({"accepted": "apply_edits", "rejected": "propose_fix",
                                                             "wait": "wait_for_approval"}))
    graph.add_conditional_edges("apply_edits", _route({"ok": "run_tests_after_fix"}))
    graph.add_conditional_edges("run_tests_after_fix", _route({"passed": "decide_next", "failed": "decide_next"}))
    graph.add_conditional_edges("decide_next", _route({"retry": "propose_fix"}))
    graph.add_edge("finish", END)
    return graph
