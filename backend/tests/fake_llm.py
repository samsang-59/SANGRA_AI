"""FakeLLM: a scripted model for the agent tests (no network, same answers every time)."""

import asyncio
import copy
import json
import uuid

from app.agent.llm.client import ChatReply, ToolCall


def tool_call(name: str, tokens: int = 100, **arguments) -> ChatReply:
    return ChatReply(None, [ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, json.dumps(arguments))], tokens)


def raw_tool_call(name: str, arguments: str, tokens: int = 100) -> ChatReply:
    return ChatReply(None, [ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, arguments)], tokens)


def answer(content, tokens: int = 100) -> ChatReply:
    return ChatReply(content if isinstance(content, str) else json.dumps(content), [], tokens)


class FakeLLM:
    """Plays the script in order: a ChatReply is returned, an exception is raised, a function(messages, tools) is
    called. `forever` (a ChatReply) is returned once the script is used up. Every request is recorded."""

    def __init__(self, script: list | None = None, latency: float = 0.0, forever: ChatReply | None = None):
        self.script = list(script or [])
        self.latency = latency
        self.forever = forever
        self.requests: list[tuple[list[dict], list[dict] | None]] = []

    async def complete(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        if self.latency:
            await asyncio.sleep(self.latency)
        if not self.script:
            if self.forever is not None:
                return self.forever
            raise AssertionError("the FakeLLM script is used up")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages, tools)
        return step

    def all_text(self) -> str:
        return json.dumps(self.requests, ensure_ascii=False)
