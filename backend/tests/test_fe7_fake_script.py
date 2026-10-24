"""The scripted agent of the test setups (Gaps #10), as the FE7 browser tests use it: steps in order, a step may
take some seconds (to hold a run in a phase, e.g. "searching", long enough to cancel it from the browser), and the
list starts again from the top when it is used up."""

import json
import time

from app.agent.fakes import fake_parts


async def test_scripted_llm_steps_in_order_with_seconds(tmp_path):
    script = tmp_path / "agent.json"
    script.write_text(json.dumps({
        "llm": [{"tool": "search_files", "args": {"query": "discount"}, "seconds": 0.3},
                {"answer": {"outcome": "not_found", "findings": []}}],
        "tests": [{"status": "passed", "exit_code": 0, "log": "ok", "seconds": 0.2}]}))
    llm, runner = fake_parts(script)

    started = time.monotonic()
    first = await llm.complete([], [])
    assert time.monotonic() - started >= 0.3
    assert first.tool_calls[0].name == "search_files"
    assert json.loads(first.tool_calls[0].arguments) == {"query": "discount"}

    started = time.monotonic()
    second = await llm.complete([], [])
    assert time.monotonic() - started < 0.2  # no "seconds": at once
    assert json.loads(second.content) == {"outcome": "not_found", "findings": []}
    assert (await llm.complete([], [])).tool_calls[0].name == "search_files"  # used up: from the top again

    started = time.monotonic()
    outcome = await runner.run(run_id=1, user_id=1, code_dir=tmp_path, language="java", test_command="mvn test")
    assert time.monotonic() - started >= 0.2
    assert (outcome.status, outcome.exit_code) == ("passed", 0)
