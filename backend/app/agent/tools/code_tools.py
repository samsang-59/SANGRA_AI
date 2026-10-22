"""CodeTools: the three READ-ONLY tools the LLM may use (04_backend_design.md 6.1 and 6.5, item 3).

- search_files(query): case-insensitive search over file contents and names, with hit counts (config files such
  as .properties and .yml are included, because bugs often live there). At most 20 files.
- search_in_file(path, query): the matching lines with a little context.
- read_file_lines(path, start_line, end_line): the exact lines (at most 200 per call).

Rules: every path goes through the path guard (inside code/, never .git/); binary files, files over 1 MB and the
big generated folders are skipped; every answer is capped in size. The tools never raise for a bad request:
they answer {"error": "..."} so the LLM can correct itself. This module only reads: it has no function that
writes, deletes, moves or runs anything.
"""

import json
import os
from pathlib import Path

from app.runners.workspace_manager import SKIP_DIRS, PathRefused, safe_path

MAX_QUERY_LENGTH = 200
MAX_LINE_CHARS = 300
MAX_MATCHES = 50
CONTEXT_LINES = 2
BINARY_SNIFF_BYTES = 8192


class CodeTools:
    def __init__(self, code_dir: Path, *, max_files: int = 20, max_lines: int = 200,
                 file_cap_bytes: int = 1024 * 1024, output_max_chars: int = 16_000):
        self.code_dir = code_dir.resolve()
        self.max_files = max_files
        self.max_lines = max_lines
        self.file_cap_bytes = file_cap_bytes
        self.output_max_chars = output_max_chars

    # --- tool 1 ----------------------------------------------------------------------------------

    def search_files(self, query: str) -> dict:
        """Files whose contents or names contain the text (case-insensitive), most hits first."""
        problem = _check_query(query)
        if problem:
            return {"error": problem}
        needle = query.strip().lower()
        needle_bytes = needle.encode("utf-8")
        found = []
        try:
            for relative, full in self._files():
                name_match = needle in relative.lower()
                data = self._read_text_bytes(full)
                hits = data.lower().count(needle_bytes) if data is not None else 0
                if hits or name_match:
                    found.append({"path": relative, "hits": hits, "name_match": name_match})
        except OSError as exc:
            return {"error": f"The project folder cannot be read ({exc.strerror or type(exc).__name__})."}
        found.sort(key=lambda f: (-(f["hits"] + (1 if f["name_match"] else 0)), f["path"]))
        result = {"query": query.strip(), "files": found[: self.max_files], "total_files_matched": len(found),
                  "truncated": len(found) > self.max_files}
        return self._cap(result, "files")

    # --- tool 2 ----------------------------------------------------------------------------------

    def search_in_file(self, path: str, query: str) -> dict:
        """The lines of one file that contain the text (case-insensitive), each with 2 lines around it."""
        problem = _check_query(query)
        if problem:
            return {"error": problem}
        lines_or_error = self._lines(path)
        if isinstance(lines_or_error, dict):
            return lines_or_error
        lines = lines_or_error
        needle = query.strip().lower()
        hits = [i for i, line in enumerate(lines) if needle in line.lower()]
        matches = []
        for index in hits[:MAX_MATCHES]:
            first, last = max(0, index - CONTEXT_LINES), min(len(lines), index + CONTEXT_LINES + 1)
            matches.append({"line": index + 1,
                            "context": [{"line": n + 1, "text": _clip(lines[n])} for n in range(first, last)]})
        result = {"path": path, "query": query.strip(), "matches": matches, "total_matches": len(hits),
                  "truncated": len(hits) > MAX_MATCHES}
        return self._cap(result, "matches")

    # --- tool 3 ----------------------------------------------------------------------------------

    def read_file_lines(self, path: str, start_line: int, end_line: int) -> dict:
        """Lines start_line..end_line (1-based, both included), at most 200 per call."""
        if not _is_int(start_line) or not _is_int(end_line):
            return {"error": "start_line and end_line must be whole numbers."}
        if start_line < 1:
            return {"error": "start_line must be 1 or more."}
        if end_line < start_line:
            return {"error": "end_line must not be smaller than start_line."}
        wanted_end = end_line
        end_line = min(end_line, start_line + self.max_lines - 1)
        full = self._guarded_file(path)
        if isinstance(full, dict):
            return full
        lines = []
        total = 0
        try:
            # Reads line by line and stops at end_line: never more of the file than needed.
            with full.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for number, line in enumerate(handle, start=1):
                    total = number
                    if number >= start_line:
                        lines.append({"line": number, "text": _clip(line.rstrip("\r\n"))})
                    if number >= end_line:
                        break
        except OSError as exc:
            return {"error": f"The file cannot be read ({exc.strerror or type(exc).__name__})."}
        if total == 0:
            return {"error": "The file is empty."}
        if not lines:
            return {"error": f"The file has only {total} lines."}
        result = {"path": path, "start_line": start_line, "end_line": lines[-1]["line"], "lines": lines,
                  "truncated": wanted_end > lines[-1]["line"] and lines[-1]["line"] == end_line}
        return self._cap(result, "lines")

    # --- helpers ---------------------------------------------------------------------------------

    def _files(self):
        """Every regular, searchable file (relative path, full path). Never follows symlinks."""
        for folder, dirnames, filenames in os.walk(self.code_dir, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(folder, d)))
            for name in sorted(filenames):
                full = os.path.join(folder, name)
                if os.path.islink(full):
                    continue
                yield os.path.relpath(full, self.code_dir).replace(os.sep, "/"), full

    def _read_text_bytes(self, full: str) -> bytes | None:
        """The file's bytes, or None for binary files, files over the cap and files that vanished."""
        try:
            if os.path.getsize(full) > self.file_cap_bytes:
                return None
            with open(full, "rb") as handle:
                data = handle.read(self.file_cap_bytes + 1)
        except OSError:
            return None
        if b"\x00" in data[:BINARY_SNIFF_BYTES]:
            return None
        return data

    def _guarded_file(self, path: str) -> Path | dict:
        try:
            full = safe_path(self.code_dir, path)
        except PathRefused as exc:
            return {"error": f"Path refused: {exc}."}
        if any(part in SKIP_DIRS for part in full.relative_to(self.code_dir).parts):
            return {"error": "This folder holds generated files and is not searched."}
        try:
            if not full.is_file():
                return {"error": "The file does not exist."}
            if full.stat().st_size > self.file_cap_bytes:
                return {"error": f"The file is larger than {self.file_cap_bytes // 1024} KB and is not read."}
            with full.open("rb") as handle:
                if b"\x00" in handle.read(BINARY_SNIFF_BYTES):
                    return {"error": "This is a binary file."}
        except OSError as exc:
            return {"error": f"The file cannot be read ({exc.strerror or type(exc).__name__})."}
        return full

    def _lines(self, path: str) -> list[str] | dict:
        full = self._guarded_file(path)
        if isinstance(full, dict):
            return full
        try:
            return full.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"The file cannot be read ({exc.strerror or type(exc).__name__})."}

    def _cap(self, result: dict, list_key: str) -> dict:
        """Drops items from the end of the main list until the answer fits the size cap."""
        while len(json.dumps(result, ensure_ascii=False)) > self.output_max_chars and result[list_key]:
            result[list_key] = result[list_key][:-1]
            result["truncated"] = True
        return result


def _check_query(query: object) -> str | None:
    if not isinstance(query, str) or not query.strip():
        return "The search text must not be empty."
    if len(query) > MAX_QUERY_LENGTH:
        return f"The search text must be at most {MAX_QUERY_LENGTH} characters."
    return None


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + " ...(line cut)"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
