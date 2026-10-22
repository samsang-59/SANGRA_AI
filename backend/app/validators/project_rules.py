"""Shape rules for project fields. Used by the validators (one field at a time) and by ProjectService
(to check a merged project after a PATCH). Pure functions: no database, no disk."""

import re
from pathlib import PurePosixPath, PureWindowsPath

LANGUAGES = ("java", "python")
SOURCE_TYPES = ("github", "local_path", "zip")

# https://github.com/<owner>/<repo>: owner = letters, digits and single hyphens (not first, at most 39);
# repo = letters, digits, ".", "_" and "-". Nothing before or after (no "; rm -rf /", no "ext::", no "-x").
_GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_BRANCH_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")
MAX_BRANCH_LENGTH = 255


def check_github_url(value: str) -> str:
    if not _GITHUB_URL.match(value):
        raise ValueError("must look like https://github.com/owner/repo")
    repo = value.rsplit("/", 1)[1]
    if repo in (".", "..") or repo.startswith("-"):
        raise ValueError("must look like https://github.com/owner/repo")
    return value


def check_local_path(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("must be an absolute folder path")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if not (posix.is_absolute() or windows.is_absolute()):
        raise ValueError("must be an absolute folder path")
    if ".." in posix.parts or ".." in windows.parts:
        raise ValueError("must not contain '..'")
    return value


def check_branch(value: str) -> str:
    """Branch names with git's usual characters only, e.g. main, feature/x-1, release-2.0."""
    if (
        len(value) > MAX_BRANCH_LENGTH
        or not _BRANCH_CHARS.match(value)
        or value.startswith(("-", "/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
        or "/." in value
    ):
        raise ValueError("is not a valid branch name")
    return value


def check_reference(source_type: str, source_reference: str | None) -> str | None:
    """The source_reference rule for a whole project: required unless zip, and in the right shape."""
    if source_type == "zip":
        return None
    if not source_reference:
        raise ValueError("source_reference is required unless source_type is zip")
    if source_type == "github":
        return check_github_url(source_reference)
    return check_local_path(source_reference)
