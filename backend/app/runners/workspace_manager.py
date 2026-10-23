"""WorkspaceManager (04_backend_design.md 6.5, item 2): builds the temporary copy of the user's code.

    workspaces/<user_id>/<run_id>/
        code/        the temporary copy (always a git repository, so undo is one git command)
        artifacts/   logs, patch (these go into the package)
        source.zip   the uploaded zip of a zip project (saved by RunService)

Safety rules:
- The user's original is never touched.
- Every path the agent uses goes through safe_path(): it must stay inside code/ (and never inside .git/).
- git always runs with a locked-down setup (Gaps #5): no system or global config, no hooks, no fsmonitor,
  only https, no password prompts. A local folder or a zip never brings its own .git folder: the copy gets a
  fresh repository of ours, so no foreign git config or hook can ever run.
- Commands are argument lists, never shell strings (file names with ; $() or spaces are just names).
- Size and file-count limits for every source; a zip is checked entry by entry while it is unpacked.
"""

import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

from app.core.config import Settings
from app.validators.project_rules import check_branch, check_github_url

# Big generated folders that are never copied or searched.
SKIP_DIRS = frozenset({"target", "build", "node_modules", "__pycache__", ".venv", ".git"})
MAX_PATH_LENGTH = 4096
MAX_NAME_LENGTH = 255
GIT_IDENTITY = ("SANGRA_AI", "agent@sangra-ai.local")
# Environment variables passed on to git: only what it needs to reach the network (proxy, CA bundle).
GIT_PASSTHROUGH_ENV = ("PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
                       "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "LANG", "TZ")


class WorkspaceError(Exception):
    """A workspace step failed. `reason` is the run's stop reason (clone_failed, undo_failed ...)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class PathRefused(ValueError):
    """A path that is not allowed (outside code/, inside .git/, or malformed)."""


@dataclass(frozen=True)
class Workspace:
    run_dir: Path
    code_dir: Path
    artifacts_dir: Path
    source_commit: str | None  # GitHub: the exact commit that was cloned
    base_commit: str           # the commit undo goes back to


def safe_path(code_dir: Path, relative: str) -> Path:
    """The path guard. Returns the real path inside code_dir, or raises PathRefused."""
    if not isinstance(relative, str) or not relative.strip():
        raise PathRefused("the path is empty")
    if len(relative) > MAX_PATH_LENGTH:
        raise PathRefused("the path is too long")
    decoded = unquote(relative)
    for text in (relative, decoded):
        if "\x00" in text:
            raise PathRefused("the path contains a null byte")
        if "\\" in text:
            raise PathRefused("use / in paths, not \\")
        if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute() or PureWindowsPath(text).drive:
            raise PathRefused("the path must be relative to the project folder")
        parts = PurePosixPath(text).parts
        if ".." in parts:
            raise PathRefused("the path must not contain '..'")
        if ".git" in parts:
            raise PathRefused("files inside .git are not allowed")
        if any(len(part) > MAX_NAME_LENGTH for part in parts):
            raise PathRefused("a name in the path is too long")
    root = code_dir.resolve()
    target = (root / relative).resolve()  # follows symlinks, so a link that leads out is caught below
    if target != root and not target.is_relative_to(root):
        raise PathRefused("the path leads outside the project folder")
    if ".git" in target.relative_to(root).parts:
        raise PathRefused("files inside .git are not allowed")
    return target


class WorkspaceManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.workspaces_dir.resolve()

    # --- folders ---------------------------------------------------------------------------------

    def run_dir(self, user_id: int, run_id: int) -> Path:
        return self.root / str(int(user_id)) / str(int(run_id))

    def prepare(self, user_id: int, run_id: int, source_type: str, source_reference: str | None,
                branch: str | None) -> Workspace:
        """Builds code/ for a GitHub, local or zip source and makes sure it is a git repository.
        Raises WorkspaceError("clone_failed", ...) with a clear message; the half-built code/ is deleted."""
        run_dir = self.run_dir(user_id, run_id)
        code_dir, artifacts_dir = run_dir / "code", run_dir / "artifacts"
        if code_dir.exists():
            self._remove_tree(code_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(exist_ok=True)
        try:
            source_commit = None
            if source_type == "github":
                self._clone(source_reference or "", branch, code_dir, run_dir)
                source_commit = self._git(["rev-parse", "HEAD"], code_dir, run_dir).stdout.strip()
                base_commit = source_commit
            elif source_type == "local_path":
                self._copy_local(Path(source_reference or ""), code_dir)
                base_commit = self._init_repo(code_dir, run_dir)
            elif source_type == "zip":
                self._unzip(run_dir / "source.zip", code_dir)
                base_commit = self._init_repo(code_dir, run_dir)
            else:
                raise WorkspaceError("clone_failed", f"Unknown source type {source_type!r}.")
        except WorkspaceError:
            self._remove_tree(code_dir)
            raise
        except OSError as exc:
            self._remove_tree(code_dir)
            raise WorkspaceError("clone_failed", f"The code could not be copied ({exc.strerror or type(exc).__name__}).")
        return Workspace(run_dir, code_dir, artifacts_dir, source_commit, base_commit)

    # --- git -------------------------------------------------------------------------------------

    def _git_env(self, run_dir: Path) -> dict[str, str]:
        home = run_dir / ".git-home"
        home.mkdir(parents=True, exist_ok=True)
        env = {key: os.environ[key] for key in GIT_PASSTHROUGH_ENV if key in os.environ}
        env.update({
            "HOME": str(home), "XDG_CONFIG_HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_AUTHOR_NAME": GIT_IDENTITY[0], "GIT_AUTHOR_EMAIL": GIT_IDENTITY[1],
            "GIT_COMMITTER_NAME": GIT_IDENTITY[0], "GIT_COMMITTER_EMAIL": GIT_IDENTITY[1],
        })
        return env

    @staticmethod
    def _git_args(args: list[str]) -> list[str]:
        safe = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
                "-c", "core.safecrlf=false", "-c", "commit.gpgsign=false", "-c", "core.pager=cat",
                "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "credential.helper="]
        return ["git", *safe, *args]

    def _git(self, args: list[str], cwd: Path, run_dir: Path, timeout: float = 120.0) -> subprocess.CompletedProcess:
        return subprocess.run(self._git_args(args), cwd=cwd, env=self._git_env(run_dir), capture_output=True,
                              text=True, timeout=timeout, check=True, stdin=subprocess.DEVNULL)

    def _init_repo(self, code_dir: Path, run_dir: Path) -> str:
        """git init + first commit of everything that was copied (ignored files too, so undo restores them)."""
        try:
            self._git(["init", "-q"], code_dir, run_dir)
            self._git(["add", "-A", "--force", "."], code_dir, run_dir, timeout=600)
            self._git(["commit", "-q", "--no-verify", "-m", "SANGRA_AI: the original code"], code_dir, run_dir)
            return self._git(["rev-parse", "HEAD"], code_dir, run_dir).stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError("clone_failed", f"git could not record the original code ({_git_error(exc)}).")

    # --- GitHub ----------------------------------------------------------------------------------

    def _clone(self, url: str, branch: str | None, code_dir: Path, run_dir: Path) -> None:
        try:  # checked again here, before git ever sees them (no "-..." options, no ext::)
            check_github_url(url)
            if branch:
                check_branch(branch)
        except ValueError as exc:
            raise WorkspaceError("clone_failed", f"Refused before running git: {exc}.")
        args = ["clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags"]
        if branch:
            args += ["--branch", branch]
        args += ["--", url, str(code_dir)]
        process = subprocess.Popen(self._git_args(args), cwd=run_dir, env=self._git_env(run_dir),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                   stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + self.settings.clone_timeout_seconds
        problem = None
        while process.poll() is None:
            if time.monotonic() > deadline:
                problem = f"the clone took longer than {self.settings.clone_timeout_seconds:.0f} seconds"
            elif _folder_size(code_dir) > self.settings.workspace_max_bytes:
                problem = f"the repository is larger than {_mb(self.settings.workspace_max_bytes)}"
            if problem:
                process.kill()
                process.communicate()
                raise WorkspaceError("clone_failed", f"Clone stopped: {problem}.")
            time.sleep(0.2)
        stderr = process.communicate()[1] or ""
        if process.returncode != 0:
            raise WorkspaceError("clone_failed", _clone_reason(stderr, branch))
        files, size = _count_tree(code_dir, skip_git=False)
        if size > self.settings.workspace_max_bytes:
            raise WorkspaceError("clone_failed", f"The repository is larger than {_mb(self.settings.workspace_max_bytes)}.")
        if files > self.settings.workspace_max_files + 10_000:  # .git holds files too
            raise WorkspaceError("clone_failed", f"The repository has more than {self.settings.workspace_max_files} files.")

    # --- local folder ----------------------------------------------------------------------------

    def _copy_local(self, source: Path, code_dir: Path) -> None:
        if not source.is_absolute():
            raise WorkspaceError("clone_failed", "The project folder must be an absolute path.")
        try:
            if not source.is_dir() or not os.access(source, os.R_OK | os.X_OK):
                raise WorkspaceError("clone_failed", "The project folder does not exist, is not a folder, or cannot be read.")
        except OSError:
            raise WorkspaceError("clone_failed", "The project folder cannot be read.") from None
        files = size = 0
        code_dir.mkdir(parents=True)
        for folder, dirnames, filenames in os.walk(source, followlinks=False, onerror=_raise):
            here = Path(folder)
            # Never follow or copy symlinked folders, and skip the big generated folders and .git.
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (here / d).is_symlink()]
            target_dir = code_dir / here.relative_to(source)
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in filenames:
                path = here / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):  # symlinks, sockets, devices: never copied
                    continue
                files += 1
                size += info.st_size
                if files > self.settings.workspace_max_files:
                    raise WorkspaceError("clone_failed", f"The folder has more than {self.settings.workspace_max_files} files.")
                if size > self.settings.workspace_max_bytes:
                    raise WorkspaceError("clone_failed", f"The folder is larger than {_mb(self.settings.workspace_max_bytes)}.")
                shutil.copyfile(path, target_dir / name, follow_symlinks=False)
                shutil.copymode(path, target_dir / name, follow_symlinks=False)
        if files == 0:
            raise WorkspaceError("clone_failed", "The project folder is empty.")

    # --- zip -------------------------------------------------------------------------------------

    def _unzip(self, zip_path: Path, code_dir: Path) -> None:
        if not zip_path.is_file():
            raise WorkspaceError("clone_failed", "The uploaded zip file is missing.")
        if not zipfile.is_zipfile(zip_path):
            raise WorkspaceError("clone_failed", "The uploaded file is not a zip file.")
        try:
            with zipfile.ZipFile(zip_path) as archive:
                entries = archive.infolist()
                files = [e for e in entries if not e.is_dir()]
                if len(files) > self.settings.workspace_max_files:
                    raise WorkspaceError("clone_failed", f"The zip has more than {self.settings.workspace_max_files} files.")
                for entry in entries:
                    _check_zip_entry(entry)
                kept = [e for e in files if ".git" not in PurePosixPath(e.filename.replace("\\", "/")).parts]
                if not kept:
                    raise WorkspaceError("clone_failed", "The zip is empty.")
                if sum(e.file_size for e in kept) > self.settings.workspace_max_bytes:
                    raise WorkspaceError("clone_failed", f"The zip unpacks to more than {_mb(self.settings.workspace_max_bytes)}.")
                code_dir.mkdir(parents=True)
                written = 0
                prefix = _single_top_folder(kept)
                for entry in kept:
                    relative = entry.filename.replace("\\", "/")
                    target = code_dir / (relative[len(prefix):] if prefix else relative)
                    if not target.resolve().is_relative_to(code_dir.resolve()):
                        raise WorkspaceError("clone_failed", "The zip contains a path that leads outside the folder.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open("wb") as out:
                        # Counted while writing: the sizes written in a zip can lie (zip bombs).
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            if written > self.settings.workspace_max_bytes:
                                raise WorkspaceError("clone_failed",
                                                     f"The zip unpacks to more than {_mb(self.settings.workspace_max_bytes)}.")
                            out.write(chunk)
        except WorkspaceError:
            raise
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
            raise WorkspaceError("clone_failed", "The zip file is damaged or uses an unsupported format.") from None

    # --- undo, diff, delete ----------------------------------------------------------------------

    def undo(self, code_dir: Path) -> None:
        """Puts every file back as it was in the base commit and checks the copy is clean.
        Raises WorkspaceError("undo_failed") when that is not possible (for example a locked file)."""
        run_dir = code_dir.parent
        try:
            self._git(["reset", "-q", "--hard", "HEAD"], code_dir, run_dir)
            self._git(["clean", "-q", "-fd"], code_dir, run_dir)
            status = self._git(["status", "--porcelain"], code_dir, run_dir).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError("undo_failed", f"The changes could not be undone ({_git_error(exc)}).")
        if status.strip():
            raise WorkspaceError("undo_failed", "The changes could not be fully undone (files are still changed).")

    def diff(self, code_dir: Path) -> str:
        """The patch of every change since the base commit (git diff, no external tools)."""
        result = self._git(["diff", "--no-color", "--no-ext-diff", "--no-textconv", "HEAD"], code_dir, code_dir.parent)
        return result.stdout

    def delete(self, run_dir: Path) -> None:
        """Deletes a run's whole folder. Only folders inside WORKSPACES_DIR are ever deleted."""
        run_dir = run_dir.resolve()
        if not run_dir.is_relative_to(self.root) or run_dir == self.root:
            raise PathRefused("only folders inside the workspaces folder can be deleted")
        self._remove_tree(run_dir)

    def delete_older_than(self, max_age_seconds: float, now: float | None = None) -> list[Path]:
        """Workspace cleanup: removes run folders (workspaces/<user>/<run>) not changed for max_age_seconds."""
        now = time.time() if now is None else now
        removed = []
        if not self.root.is_dir():
            return removed
        for user_dir in self.root.iterdir():
            if not user_dir.is_dir() or user_dir.is_symlink():
                continue
            for run_dir in user_dir.iterdir():
                try:
                    if run_dir.is_dir() and not run_dir.is_symlink() and run_dir.stat().st_mtime < now - max_age_seconds:
                        self._remove_tree(run_dir)
                        removed.append(run_dir)
                except OSError:
                    continue  # tried again at the next cleanup
        return removed

    @staticmethod
    def _remove_tree(path: Path) -> None:
        def make_writable_and_retry(func, failed_path, _exc):
            os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(failed_path)

        if path.exists() or path.is_symlink():
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=make_writable_and_retry)
            else:  # Python 3.10 and 3.11
                shutil.rmtree(path, onerror=make_writable_and_retry)


# --- helpers -------------------------------------------------------------------------------------

def _check_zip_entry(entry: zipfile.ZipInfo) -> None:
    name = entry.filename.replace("\\", "/")
    if name.startswith("/") or PureWindowsPath(entry.filename).drive or ".." in PurePosixPath(name).parts:
        raise WorkspaceError("clone_failed", "The zip contains an unsafe path (for example ../ or an absolute path).")
    if stat.S_ISLNK(entry.external_attr >> 16):
        raise WorkspaceError("clone_failed", "The zip contains a symbolic link, which is not allowed.")
    if entry.flag_bits & 0x1:
        raise WorkspaceError("clone_failed", "The zip is password protected.")


def _single_top_folder(entries: list[zipfile.ZipInfo]) -> str:
    """A zip made by GitHub's "Download ZIP" puts everything in one top folder (for example repo-main/). When
    EVERY file sits inside the same single top folder, that folder is removed while unpacking, so the test
    command runs in the project's root. Returns the prefix to remove ("repo-main/"), or "" to keep the paths."""
    tops = set()
    for entry in entries:
        parts = PurePosixPath(entry.filename.replace("\\", "/")).parts
        if len(parts) < 2:
            return ""  # a file at the top: the zip already starts at the project root
        tops.add(parts[0])
        if len(tops) > 1:
            return ""
    return f"{tops.pop()}/" if tops else ""


def _folder_size(path: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def _count_tree(path: Path, skip_git: bool) -> tuple[int, int]:
    files = size = 0
    for folder, dirs, names in os.walk(path, followlinks=False):
        if skip_git:
            dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            files += 1
            try:
                size += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return files, size


def _raise(error: OSError) -> None:
    raise error


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB" if size >= 1024 * 1024 else f"{size} bytes"


def _git_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "git took too long"
    lines = ((getattr(exc, "stderr", "") or "").strip().splitlines() or ["git failed"])
    return lines[-1][:200]


def _clone_reason(stderr: str, branch: str | None) -> str:
    text = stderr.lower()
    if "could not read username" in text or "repository not found" in text or "authentication" in text:
        return "Clone failed: the repository does not exist or is private (only public repositories are supported)."
    if branch and ("remote branch" in text and "not found" in text):
        return f"Clone failed: the branch {branch!r} does not exist."
    if "could not resolve host" in text or "unable to access" in text or "failed to connect" in text:
        return "Clone failed: GitHub could not be reached (network problem)."
    last = (stderr.strip().splitlines() or ["unknown error"])[-1]
    return f"Clone failed: {last[:200]}"
