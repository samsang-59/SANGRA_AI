"""The propose_fix step's LLM call (04_backend_design.md 6.4 step 5).

One call with NO tools. The answer must be the fixed JSON format: an explanation and a list of edits (file, line
range, old_code, new_code, short reason). The parser checks only the SHAPE; whether the file exists, the lines are in
range and old_code matches is check_proposal's job (B9). An answer that cannot be read gets one more try with the
reason; after that the proposal has failed (it never crashes the run).
"""

from dataclasses import dataclass, field

from app.agent.llm.client import LLMClient
from app.agent.llm.parsing import ParseError, extract_json_object
from app.agent.llm.prompts import as_list, render


@dataclass
class ProposalContext:
    bug_description: str
    language: str
    findings: list[dict]
    hints: list[str] = field(default_factory=list)
    baseline_summary: str = "(not available)"
    previous_attempts: list[str] = field(default_factory=list)
    reject_feedback: str | None = None
    last_test_failure: str | None = None
    check_problems: list[str] = field(default_factory=list)
    max_edits: int = 10
    max_changed_lines: int = 300


@dataclass
class ProposedEdit:
    file_path: str
    start_line: int
    end_line: int
    old_code: str
    new_code: str
    reason: str

    def as_dict(self) -> dict:
        return {"file_path": self.file_path, "start_line": self.start_line, "end_line": self.end_line,
                "old_code": self.old_code, "new_code": self.new_code, "explanation": self.reason}


@dataclass
class ProposalResult:
    ok: bool
    explanation: str = ""
    edits: list[ProposedEdit] = field(default_factory=list)
    tokens: int = 0
    error: str | None = None


def parse_proposal(text: str) -> tuple[str, list[ProposedEdit]]:
    """Reads the fixed answer format. Raises ParseError with a reason the model can act on."""
    answer = extract_json_object(text)
    explanation = answer.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ParseError('"explanation" must be a non-empty text')
    raw_edits = answer.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        raise ParseError('"edits" must be a non-empty list')
    edits = []
    for number, raw in enumerate(raw_edits, start=1):
        if not isinstance(raw, dict):
            raise ParseError(f"edit {number} must be an object")
        path, start, end = raw.get("file_path"), raw.get("start_line"), raw.get("end_line")
        old, new, reason = raw.get("old_code"), raw.get("new_code"), raw.get("reason", "")
        if not isinstance(path, str) or not path.strip():
            raise ParseError(f'edit {number}: "file_path" must be a non-empty text')
        if not _is_int(start) or not _is_int(end):
            raise ParseError(f'edit {number}: "start_line" and "end_line" must be whole numbers')
        if start < 1 or end < start:
            raise ParseError(f'edit {number}: lines must start at 1 or more and end_line must not be before start_line')
        if not isinstance(old, str) or not isinstance(new, str):
            raise ParseError(f'edit {number}: "old_code" and "new_code" must be texts')
        if not isinstance(reason, str):
            raise ParseError(f'edit {number}: "reason" must be a text')
        edits.append(ProposedEdit(path.strip(), start, end, old, new, reason.strip()[:500]))
    return explanation.strip(), edits


async def propose_fix(llm: LLMClient, context: ProposalContext) -> ProposalResult:
    """Raises LLMError when the model cannot be reached; a bad answer is a failed proposal, not an error."""
    tokens_before = llm.total_tokens
    messages = [
        {"role": "system", "content": render("propose_system", language=context.language, max_edits=context.max_edits,
                                             max_changed_lines=context.max_changed_lines)},
        {"role": "user", "content": render(
            "propose_user",
            bug_description=context.bug_description,
            hints=as_list(context.hints),
            findings=_findings_text(context.findings),
            baseline_summary=context.baseline_summary,
            previous_attempts=as_list(context.previous_attempts),
            reject_feedback=context.reject_feedback or "(none)",
            last_test_failure=context.last_test_failure or "(none)",
            check_problems=as_list(context.check_problems),
        )},
    ]
    reply = await llm.chat(messages, None)
    try:
        explanation, edits = parse_proposal(reply.content or "")
    except ParseError as first_error:
        messages.append(reply.as_message())
        messages.append({"role": "user", "content": f"Your answer could not be read: {first_error}. Answer again "
                                                    "with only the JSON object described in the instructions."})
        reply = await llm.chat(messages, None)
        try:
            explanation, edits = parse_proposal(reply.content or "")
        except ParseError as second_error:
            return ProposalResult(False, tokens=llm.total_tokens - tokens_before,
                                  error=f"The fix could not be read twice ({second_error}).")
    return ProposalResult(True, explanation, edits, llm.total_tokens - tokens_before)


def _findings_text(findings: list[dict]) -> str:
    if not findings:
        return "(none)"
    parts = []
    for finding in findings:
        parts.append(f"File {finding['file_path']}, lines {finding['start_line']}-{finding['end_line']}: "
                     f"{finding.get('note', '')}\n{finding.get('code', '')}")
    return "\n\n".join(parts)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
