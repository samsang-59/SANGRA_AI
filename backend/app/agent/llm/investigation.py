"""The investigate step's LLM loop (04_backend_design.md 6.4 step 3).

The model may call the three read-only tools, at most `max_tool_calls` times in total per pass (all three tools
together). Any other tool name, or bad arguments, gets an error back as the tool's answer and the loop continues.
When the model stops calling tools it answers with a fixed JSON (found / not_found and the places). If it is still
calling tools when the limit is reached, it gets one last call WITHOUT tools to give that answer; if that answer
cannot be read, the outcome is not_found.

The chat itself is not kept anywhere (design decision 6.1.5): only a compact result comes back: the findings (file,
lines, a few lines of code, a note), the search words tried, the number of tool calls and the tokens used.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.agent.llm.client import ChatReply, LLMClient, ToolCall
from app.agent.llm.parsing import ParseError, extract_json_object
from app.agent.llm.prompts import as_list, render
from app.agent.llm.tool_schemas import TOOL_NAMES, TOOL_SCHEMAS
from app.agent.tools.code_tools import CodeTools

MAX_FINDINGS = 5
MAX_FINDING_LINES = 60

# Called after every tool call with (tool name, arguments, a SHORT summary of the result) so the agent can add a
# run event. The summary never holds file contents.
ToolObserver = Callable[[str, dict, dict], Awaitable[None]]


@dataclass
class InvestigationContext:
    bug_description: str
    language: str
    hints: list[str] = field(default_factory=list)
    baseline_summary: str = "(not available)"
    search_terms_tried: list[str] = field(default_factory=list)


@dataclass
class Finding:
    file_path: str
    start_line: int
    end_line: int
    note: str
    code: str  # the current lines, numbered, so propose_fix sees the real code

    def as_dict(self) -> dict:
        return {"file_path": self.file_path, "start_line": self.start_line, "end_line": self.end_line,
                "note": self.note, "code": self.code}


@dataclass
class InvestigationResult:
    outcome: str  # found | not_found
    findings: list[Finding]
    search_terms_tried: list[str]
    tool_calls_used: int
    tokens: int
    summary: str = ""


async def investigate(llm: LLMClient, tools: CodeTools, context: InvestigationContext, max_tool_calls: int,
                      on_tool: ToolObserver | None = None) -> InvestigationResult:
    """Raises LLMError when the model cannot be reached (after the client's retries)."""
    tokens_before = llm.total_tokens
    terms = list(context.search_terms_tried)
    messages = [
        {"role": "system", "content": render("investigate_system", language=context.language,
                                             max_tool_calls=max_tool_calls)},
        {"role": "user", "content": render(
            "investigate_user", bug_description=context.bug_description, hints=as_list(context.hints),
            baseline_summary=context.baseline_summary, search_terms_tried=as_list(context.search_terms_tried))},
    ]
    used = 0
    reply: ChatReply | None = None
    while used < max_tool_calls:
        reply = await llm.chat(messages, TOOL_SCHEMAS)
        if not reply.tool_calls:
            break
        messages.append(reply.as_message())
        for call in reply.tool_calls:
            used += 1
            if used > max_tool_calls:
                result = {"error": "The tool call limit is reached. Give your final JSON answer now."}
            else:
                result = await _run_tool(tools, call, terms)
                if on_tool is not None:
                    await on_tool(call.name, _safe_args(call), _summary(result))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
        reply = None
    if reply is None:  # the limit was reached while the model was still calling tools: one last call, no tools
        messages.append({"role": "user", "content": "The tool call limit is reached. Answer now with only the JSON."})
        reply = await llm.chat(messages, None)

    final = await _read_final_answer(llm, messages, reply)
    findings = await _verify_findings(tools, final.get("findings", [])) if final else []
    outcome = "found" if final and final.get("outcome") == "found" and findings else "not_found"
    return InvestigationResult(outcome, findings if outcome == "found" else [], terms, min(used, max_tool_calls),
                               llm.total_tokens - tokens_before, str((final or {}).get("summary", ""))[:500])


async def _read_final_answer(llm: LLMClient, messages: list[dict], reply: ChatReply) -> dict | None:
    """The final JSON. One second chance if it cannot be read; None after that."""
    for second_chance in (False, True):
        try:
            answer = extract_json_object(reply.content or "")
            if answer.get("outcome") in ("found", "not_found") and isinstance(answer.get("findings", []), list):
                return answer
            raise ParseError('it must have "outcome" ("found" or "not_found") and a "findings" list')
        except ParseError as exc:
            if second_chance:
                return None
            messages.append(reply.as_message())
            messages.append({"role": "user", "content": f"Your answer could not be read: {exc}. Answer again with "
                                                        "only the JSON object described in the instructions."})
            reply = await llm.chat(messages, None)
    return None


async def _run_tool(tools: CodeTools, call: ToolCall, terms: list[str]) -> dict:
    if call.name not in TOOL_NAMES:
        return {"error": f"Unknown tool {call.name!r}. The only tools are: search_files, search_in_file, "
                         "read_file_lines."}
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "The tool arguments are not valid JSON."}
    if not isinstance(args, dict):
        return {"error": "The tool arguments must be a JSON object."}
    try:
        if call.name == "search_files":
            terms.append(str(args.get("query", ""))[:200])
            return await asyncio.to_thread(tools.search_files, args["query"])
        if call.name == "search_in_file":
            terms.append(str(args.get("query", ""))[:200])
            return await asyncio.to_thread(tools.search_in_file, args["path"], args["query"])
        return await asyncio.to_thread(tools.read_file_lines, args["path"], args["start_line"], args["end_line"])
    except KeyError as exc:
        return {"error": f"Missing argument {exc.args[0]!r}."}
    except TypeError:
        return {"error": "Wrong argument types."}


def _safe_args(call: ToolCall) -> dict:
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(args, dict):
        return {}
    return {key: (value[:200] if isinstance(value, str) else value) for key, value in args.items()
            if key in ("query", "path", "start_line", "end_line")}


def _summary(result: dict) -> dict:
    """A short description of a tool result for the run's diary: counts and names, never file contents."""
    if "error" in result:
        return {"error": str(result["error"])[:200]}
    if "files" in result:
        return {"files": len(result["files"]), "total_files_matched": result.get("total_files_matched")}
    if "matches" in result:
        return {"matches": result.get("total_matches")}
    if "lines" in result:
        return {"lines": len(result["lines"])}
    return {}


async def _verify_findings(tools: CodeTools, raw: list) -> list[Finding]:
    """Keeps only findings that point at real lines, and attaches those lines (numbered)."""
    findings: list[Finding] = []
    for item in raw[:MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        path, start, end = item.get("file_path"), item.get("start_line"), item.get("end_line")
        if not isinstance(path, str) or not _is_int(start) or not _is_int(end) or start < 1 or end < start:
            continue
        end = min(end, start + MAX_FINDING_LINES - 1)
        lines = await asyncio.to_thread(tools.read_file_lines, path, start, end)
        if "error" in lines:
            continue
        code = "\n".join(f"{line['line']}: {line['text']}" for line in lines["lines"])
        findings.append(Finding(path, start, lines["end_line"], str(item.get("note", ""))[:500], code))
    return findings


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
