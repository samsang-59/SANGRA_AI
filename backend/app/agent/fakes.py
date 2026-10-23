"""Scripted stand-ins for the LLM and the Docker test runner (Gaps #10). Used ONLY when AGENT_TEST_MODE=fake (test
setups such as the browser tests); the settings refuse that mode in deployed mode.

The script is a JSON file (AGENT_FAKE_SCRIPT):
{
  "llm":   [ {"tool": "search_files", "args": {"query": "discount"}},
             {"answer": {"outcome": "found", "findings": [...]}},          (any JSON answer)
             {"answer_text": "free text"} ],
  "tests": [ {"status": "failed", "exit_code": 1, "log": "1 test failed"},
             {"status": "passed", "exit_code": 0, "log": "all tests passed"} ]
}
Each list is used in order; when it is used up it starts again from the top.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agent.llm.client import ChatReply, ToolCall
from app.runners.test_runner import TestOutcome


class _Script:
    def __init__(self, path: Path):
        self.path = path
        self.positions = {"llm": 0, "tests": 0}

    def next(self, kind: str) -> dict:
        steps = json.loads(self.path.read_text(encoding="utf-8")).get(kind) or []
        if not steps:
            raise RuntimeError(f"the fake agent script has no {kind!r} steps")
        step = steps[self.positions[kind] % len(steps)]
        self.positions[kind] += 1
        return step


class ScriptedLLM:
    def __init__(self, script: _Script):
        self.script = script

    async def complete(self, messages, tools) -> ChatReply:
        step = self.script.next("llm")
        if "tool" in step:
            return ChatReply(None, [ToolCall(f"call_{uuid.uuid4().hex[:8]}", step["tool"],
                                             json.dumps(step.get("args", {})))], 50)
        if "answer" in step:
            return ChatReply(json.dumps(step["answer"]), [], 50)
        return ChatReply(step.get("answer_text", ""), [], 50)


class ScriptedTestRunner:
    def __init__(self, script: _Script):
        self.script = script

    async def run(self, *, run_id, user_id, code_dir, language, test_command, log_path=None, timeout=None):
        step = self.script.next("tests")
        await asyncio.sleep(float(step.get("seconds", 0)))
        log = str(step.get("log", ""))
        now = datetime.now(timezone.utc)
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(log, encoding="utf-8")
        return TestOutcome(step.get("status", "passed"), step.get("exit_code", 0), log, log[-8000:], 1,
                           "fake-test-runner", now, now, step.get("note"), Path(log_path) if log_path else None)


def fake_parts(script_path: Path) -> tuple[ScriptedLLM, ScriptedTestRunner]:
    script = _Script(script_path)
    return ScriptedLLM(script), ScriptedTestRunner(script)
