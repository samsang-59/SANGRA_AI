"""LLM client (04_backend_design.md 6.5, item 6).

- GroqChatModel talks to Groq. It builds the Groq client from the settings for each call (no long-lived client
  object, nothing kept in the agent's state) and turns Groq's errors into two kinds: worth retrying (rate limit,
  server error, timeout, dropped connection) or not (wrong API key, a refused request).
- LLMClient is what the agent uses: it retries up to LLM_MAX_RETRIES times with waiting (1 s, 2 s, 4 s ...), then
  gives up with LLMError (the run ends as failed / llm_error). It adds up the tokens used.

Logs hold only metadata (model, tokens, attempt, error kind): never the prompts, the replies or the user's code,
and never the API key. The raw prompts and replies are not stored in the database either.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger("app.llm")


class LLMError(Exception):
    """The LLM could not be used (after the retries, or a problem retrying cannot fix)."""


class TransientLLMError(Exception):
    """Worth retrying: rate limit, server error, timeout, dropped connection."""


class PermanentLLMError(Exception):
    """Not worth retrying: wrong API key, a refused request."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model sent it


@dataclass
class ChatReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    total_tokens: int = 0

    def as_message(self) -> dict:
        """The reply as a message for the conversation history (OpenAI format)."""
        message: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return message


class ChatModel(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict] | None) -> ChatReply: ...


class GroqChatModel:
    def __init__(self, settings: Settings, base_url: str | None = None):
        self.settings = settings
        self.base_url = base_url

    async def complete(self, messages: list[dict], tools: list[dict] | None) -> ChatReply:
        import groq

        kwargs: dict = {"model": self.settings.groq_model, "messages": messages,
                        "max_completion_tokens": self.settings.llm_max_output_tokens}
        if tools:
            kwargs.update({"tools": tools, "tool_choice": "auto"})
        try:
            async with groq.AsyncGroq(api_key=self.settings.groq_api_key.get_secret_value(), base_url=self.base_url,
                                      max_retries=0, timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.chat.completions.create(**kwargs)
        except (groq.RateLimitError, groq.InternalServerError, groq.APITimeoutError, groq.APIConnectionError) as exc:
            raise TransientLLMError(_describe(exc)) from None
        except groq.APIStatusError as exc:
            if exc.status_code in (408, 409, 425) or exc.status_code >= 500:
                raise TransientLLMError(_describe(exc)) from None
            raise PermanentLLMError(_describe(exc)) from None
        except groq.GroqError as exc:
            raise PermanentLLMError(_describe(exc)) from None
        choice = response.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "") for c in (choice.tool_calls or [])]
        tokens = response.usage.total_tokens if response.usage else 0
        return ChatReply(choice.content, calls, tokens or 0)


def _describe(exc: Exception) -> str:
    """A short, safe description: the kind of error and the HTTP status, never the request (or the key)."""
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")


class LLMClient:
    def __init__(self, model: ChatModel, settings: Settings,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.model = model
        self.settings = settings
        self.sleep = sleep
        self.total_tokens = 0
        self.calls = 0
        self.retries = 0

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply:
        attempts = self.settings.llm_max_retries + 1
        for attempt in range(attempts):
            self.calls += 1
            try:
                reply = await self.model.complete(messages, tools)
            except TransientLLMError as exc:
                if attempt == attempts - 1:
                    logger.warning("LLM call failed after %s tries (%s)", attempts, exc)
                    raise LLMError(f"The AI service did not answer after {attempts} tries ({exc}).") from None
                self.retries += 1
                wait = self.settings.llm_retry_base_seconds * (2 ** attempt)
                logger.info("LLM call failed (%s); try %s of %s in %.1f s", exc, attempt + 2, attempts, wait)
                await self.sleep(wait)
                continue
            except PermanentLLMError as exc:
                logger.warning("LLM call refused (%s)", exc)
                raise LLMError(f"The AI service refused the request ({exc}).") from None
            self.total_tokens += reply.total_tokens
            logger.info("LLM call done (%s tokens, %s tool calls)", reply.total_tokens, len(reply.tool_calls))
            return reply
        raise LLMError("The AI service did not answer.")  # not reached


def estimate_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """A cautious token estimate (3 characters per token; real tokenizers average about 4 for English and code).
    Used to check that the largest possible prompt still fits the model's context."""
    text = json.dumps(messages, ensure_ascii=False) + (json.dumps(tools) if tools else "")
    return len(text) // 3 + 1
