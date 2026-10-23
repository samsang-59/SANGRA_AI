"""Finalizer: builds the download package of a finished run (04_backend_design.md 6.4 step 11).

Package contents (small, never the whole project):
- summary.md            status, the reason in plain words, the bug, the attempts and the test results (from a template)
- changes.patch         succeeded runs: the patch (git diff of the temporary copy)
- fixed_files/<path>    succeeded runs: the changed files, as they are after the fix
- attempts.md           other endings: a readable list of what was tried (every attempt with its edits)
- logs/baseline.log, logs/attempt_<n>.log   the full test logs

Names inside the zip are built only from fixed names and the edits' relative paths; a path with "..", an absolute
path or a backslash is never written (no zip slip when the user unpacks it).
"""

import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.run_progress import NO_TEST_COVERS_LABEL, STOP_REASON_TEXT


@dataclass
class PackageInput:
    run: dict
    final_status: str
    stop_reason: str | None
    summary: str
    attempts: list[dict]        # with "edits"
    test_results: list[dict]
    patch: str | None           # succeeded runs only
    code_dir: Path | None       # the temporary copy (None if it was never made)
    artifacts_dir: Path | None


def safe_name(*parts: str) -> str | None:
    """A zip entry name, or None if any part could escape the folder."""
    joined = "/".join(parts)
    if "\\" in joined or "\x00" in joined or any(part.startswith("/") or not part for part in parts):
        return None
    path = PurePosixPath(joined)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path.as_posix()


def _summary_md(data: PackageInput) -> str:
    run = data.run
    reason = STOP_REASON_TEXT.get(data.stop_reason or "", "") if data.stop_reason else ""
    baseline = next((r for r in data.test_results if r["test_type"] == "baseline"), None)
    lines = [
        f"# SANGRA_AI run {run['id']}",
        "",
        f"- Status: **{data.final_status}**" + (f" ({data.stop_reason}: {reason})" if data.stop_reason else ""),
        f"- Project language: {run['language']}; test command: `{run['test_command']}`",
        f"- Source: {run['source_type']}" + (f" ({run['source_reference']})" if run.get("source_reference") else "")
        + (f", commit {run['source_commit']}" if run.get("source_commit") else ""),
        f"- Model: {run['llm_model']}; tokens used: {run.get('total_tokens') or 0}",
        "",
        "## Summary",
        "",
        data.summary,
        "",
        "## The bug",
        "",
        run["bug_description"],
        "",
        "## Tests",
        "",
        f"- Before any change (baseline): {baseline['status'] if baseline else 'not run'}",
    ]
    for attempt in data.attempts:
        result = next((r for r in data.test_results if r.get("attempt_id") == attempt["id"]), None)
        lines.append(f"- After attempt {attempt['attempt_number']}: {result['status'] if result else 'not tested'}")
    if data.final_status == "succeeded" and baseline and baseline["status"] == "passed":
        lines += ["", f"**{NO_TEST_COVERS_LABEL}**"]
    lines += ["", "## Attempts", ""]
    if not data.attempts:
        lines.append("No fix was proposed.")
    for attempt in data.attempts:
        decision = attempt["decision"] or "no answer"
        lines.append(f"{attempt['attempt_number']}. ({attempt['reason_started']}) {attempt['explanation'] or ''} "
                     f"- decision: {decision}; result: {attempt['status']}")
    return "\n".join(lines) + "\n"


def _attempts_md(data: PackageInput) -> str:
    lines = [f"# What was tried in run {data.run['id']}", ""]
    if not data.attempts:
        lines.append("No fix was proposed.")
    for attempt in data.attempts:
        lines += [f"## Attempt {attempt['attempt_number']} ({attempt['reason_started']})", "",
                  attempt["explanation"] or "", "",
                  f"Decision: {attempt['decision'] or 'no answer'}"
                  + (f" - feedback: {attempt['decision_feedback']}" if attempt.get("decision_feedback") else ""),
                  f"Result: {attempt['status']}" + (" (undone)" if attempt.get("reverted_at") else ""), ""]
        for edit in attempt["edits"]:
            lines += [f"### {edit['file_path']}, lines {edit['start_line']}-{edit['end_line']}", "",
                      "Old code:", "```", edit["old_code"], "```", "New code:", "```", edit["new_code"], "```", ""]
    return "\n".join(lines) + "\n"


def build_package(data: PackageInput, target: Path) -> int:
    """Writes the zip to `target` (through a temporary file and a rename). Returns its size in bytes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=target.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.md", _summary_md(data))
            if data.final_status == "succeeded":
                archive.writestr("changes.patch", data.patch or "")
                changed = {e["file_path"] for a in data.attempts if a["status"] == "tests_passed" for e in a["edits"]}
                for path in sorted(changed):
                    name = safe_name("fixed_files", path)
                    source = (data.code_dir / path) if data.code_dir else None
                    if (name and source is not None and source.is_file() and not source.is_symlink()
                            and source.resolve().is_relative_to(data.code_dir.resolve())):
                        archive.write(source, name)
            else:
                archive.writestr("attempts.md", _attempts_md(data))
            if data.artifacts_dir is not None and data.artifacts_dir.is_dir():
                for log in sorted(data.artifacts_dir.glob("*.log")):
                    name = safe_name("logs", log.name)
                    if name and log.is_file() and not log.is_symlink():
                        archive.write(log, name)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target.stat().st_size
