"""Checking and applying replace-only edits (04_backend_design.md 6.4 steps 6 and 8, and "How edits behave").

One edit = "replace lines start_line..end_line of one file with new_code". Rules:
- the file is inside the folder (path guard), exists, and is UTF-8 text;
- the line range is inside the file, and old_code EXACTLY matches those lines;
- edits in the same file do not overlap;
- at most `max_edits` edits and `max_changed_lines` changed lines per attempt.

How old_code is compared (the exact rule the design left open): line by line, without the line endings, so "\\r\\n"
and "\\n" files both work; everything else (spaces, tabs, trailing spaces) must match exactly. One newline at the end of
old_code is ignored. New lines keep the file's own line ending.

Applying: every file's new content is computed IN MEMORY first, with the same checks; only when every edit passes are
the files written (each through a temporary file and an atomic rename). All or nothing.
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.runners.workspace_manager import SKIP_DIRS, PathRefused, safe_path

MAX_SHOWN_LINES = 40


class EditError(Exception):
    """The edits cannot be applied. `problems` says why, edit by edit."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Edit:
    file_path: str
    start_line: int
    end_line: int
    old_code: str
    new_code: str

    @staticmethod
    def from_dict(data: dict) -> "Edit":
        return Edit(data["file_path"], data["start_line"], data["end_line"], data["old_code"], data["new_code"])


def _lines_of(text: str) -> list[str]:
    """The lines of a code text without line endings (one final newline ignored)."""
    text = text.replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else [""]


def _new_lines(text: str) -> list[str]:
    """The lines of new_code; an empty new_code deletes the lines."""
    if text == "":
        return []
    return _lines_of(text)


def changed_line_count(edits: list[Edit]) -> int:
    """Lines removed plus lines added, over all edits."""
    return sum((e.end_line - e.start_line + 1) + len(_new_lines(e.new_code)) for e in edits)


def _read(root: Path, edit: Edit, problems: list[str], number: int) -> tuple[Path, list[str]] | None:
    try:
        path = safe_path(root, edit.file_path)
    except PathRefused as exc:
        problems.append(f"Edit {number} ({edit.file_path}): path refused ({exc}).")
        return None
    if any(part in SKIP_DIRS for part in path.relative_to(root.resolve()).parts):
        problems.append(f"Edit {number} ({edit.file_path}): files in generated folders cannot be edited.")
        return None
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        problems.append(f"Edit {number}: the file {edit.file_path} does not exist.")
        return None
    except IsADirectoryError:
        problems.append(f"Edit {number}: {edit.file_path} is a folder, not a file.")
        return None
    except OSError as exc:
        problems.append(f"Edit {number}: {edit.file_path} cannot be read ({exc.strerror}).")
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"Edit {number}: {edit.file_path} is not UTF-8 text and cannot be edited.")
        return None
    return path, text.splitlines(keepends=True)


def _show(lines: list[str], start: int, end: int) -> str:
    end = min(end, start + MAX_SHOWN_LINES - 1, len(lines))
    return "\n".join(f"{n}: {lines[n - 1].rstrip(chr(13) + chr(10))}" for n in range(start, end + 1))


def plan_edits(root: Path, edits: list[Edit], max_edits: int, max_changed_lines: int) -> dict[Path, bytes]:
    """Checks every edit and computes the new content of every file, in memory. Raises EditError listing every
    problem (with the real lines of the file, so the LLM can correct itself). Nothing is written here."""
    problems: list[str] = []
    if not edits:
        raise EditError(["The proposal has no edits."])
    if len(edits) > max_edits:
        problems.append(f"There are {len(edits)} edits; at most {max_edits} are allowed.")
    changed = changed_line_count(edits)
    if changed > max_changed_lines:
        problems.append(f"The edits change {changed} lines; at most {max_changed_lines} are allowed.")

    files: dict[str, tuple[Path, list[str]]] = {}
    by_file: dict[str, list[tuple[int, Edit]]] = {}
    for number, edit in enumerate(edits, start=1):
        if edit.file_path not in files:
            loaded = _read(root, edit, problems, number)
            if loaded is None:
                continue
            files[edit.file_path] = loaded
        _, lines = files[edit.file_path]
        if edit.start_line < 1 or edit.end_line < edit.start_line:
            problems.append(f"Edit {number} ({edit.file_path}): the line range {edit.start_line}-{edit.end_line} "
                            "is not valid.")
            continue
        if edit.end_line > len(lines):
            problems.append(f"Edit {number} ({edit.file_path}): lines {edit.start_line}-{edit.end_line} do not exist; "
                            f"the file has {len(lines)} lines.")
            continue
        actual = [line.rstrip("\r\n") for line in lines[edit.start_line - 1:edit.end_line]]
        if _lines_of(edit.old_code) != actual:
            problems.append(f"Edit {number} ({edit.file_path}): old_code does not match lines "
                            f"{edit.start_line}-{edit.end_line}. The real lines are:\n"
                            f"{_show(lines, edit.start_line, edit.end_line)}")
            continue
        by_file.setdefault(edit.file_path, []).append((number, edit))

    for file_path, numbered in by_file.items():
        ordered = sorted(numbered, key=lambda item: item[1].start_line)
        for (first_number, first), (second_number, second) in zip(ordered, ordered[1:]):
            if second.start_line <= first.end_line:
                problems.append(f"Edits {first_number} and {second_number} overlap in {file_path} "
                                f"(lines {first.start_line}-{first.end_line} and {second.start_line}-{second.end_line}).")
    if problems:
        raise EditError(problems)

    planned: dict[Path, bytes] = {}
    for file_path, numbered in by_file.items():
        path, lines = files[file_path]
        lines = list(lines)
        # Bottom-up, so the line numbers of the edits above stay right.
        for _, edit in sorted(numbered, key=lambda item: item[1].start_line, reverse=True):
            block = lines[edit.start_line - 1:edit.end_line]
            last = block[-1]
            ending = "\r\n" if last.endswith("\r\n") else ("\n" if last.endswith("\n") else "")
            style = "\r\n" if block[0].endswith("\r\n") else "\n"
            new = _new_lines(edit.new_code)
            replacement = [line + style for line in new]
            if replacement and not ending:  # the block was the end of a file without a final newline
                replacement[-1] = replacement[-1][: -len(style)]
            lines[edit.start_line - 1:edit.end_line] = replacement
        planned[path] = "".join(lines).encode("utf-8")
    return planned


def write_planned(planned: dict[Path, bytes]) -> None:
    """Writes the planned files. Each file is written to a temporary file next to it and renamed over the old one,
    so a file is never left half written."""
    for path, data in planned.items():
        mode = path.stat().st_mode
        handle, temporary = tempfile.mkstemp(prefix=".sangra-", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.chmod(temporary, mode & 0o7777)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def bottom_up_order(edits: list[dict]) -> list[dict]:
    """The order edits are saved in (04_backend_design.md 6.4 step 6): per file, from the bottom up."""
    return sorted(edits, key=lambda e: (e["file_path"], -e["start_line"]))


def write_planned_all_or_nothing(planned: dict[Path, bytes]) -> None:
    """Like write_planned, for a folder without git to undo with (the user's own folder): if writing one file fails,
    the files already written are put back as they were, then the error is raised."""
    originals = {path: path.read_bytes() for path in planned}
    written: list[Path] = []
    try:
        for path, data in planned.items():
            write_planned({path: data})
            written.append(path)
    except BaseException:
        for path in written:
            write_planned({path: originals[path]})
        raise
