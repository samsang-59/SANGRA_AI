"""Phase B6: WorkspaceManager (copy, git, path guard, safe zip) and the 3 read-only code tools."""

import ast
import json
import os
import shutil
import stat
import struct
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.agent.tools.code_tools import CodeTools
from app.runners.workspace_manager import PathRefused, WorkspaceError, WorkspaceManager, safe_path
from tests.conftest import BACKEND_DIR

HELLO_WORLD = "https://github.com/octocat/Hello-World"
HELLO_WORLD_MASTER = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
HELLO_WORLD_PATCH_BRANCH = ("octocat-patch-1", "b1b3f9723831141a31a1a7252a213e216ea76e56")

JAVA = """package shop;

public class Discount {
    double rate = 0.05; // summer sale
    double apply(double price) { return price * (1 - rate); }
}
"""


@pytest.fixture
def manager(settings) -> WorkspaceManager:
    return WorkspaceManager(settings)


def make_project(folder: Path) -> Path:
    """A small Java-like project with config files, generated folders and a CRLF file."""
    (folder / "src/main/java/shop").mkdir(parents=True)
    (folder / "src/main/java/shop/Discount.java").write_text(JAVA)
    (folder / "src/main/resources").mkdir(parents=True)
    (folder / "src/main/resources/app.properties").write_text("discount.summer=5\nshop.name=Amazon\n")
    (folder / "config.yml").write_text("discount:\n  summer: 5\n")
    (folder / "windows.txt").write_bytes(b"line one\r\nline two\r\n")
    (folder / "target").mkdir()
    (folder / "target/Discount.class").write_bytes(b"\xca\xfe\xba\xbe discount")
    (folder / "node_modules/x").mkdir(parents=True)
    (folder / "node_modules/x/discount.js").write_text("discount")
    (folder / ".gitignore").write_text("*.log\n")
    (folder / "debug.log").write_text("ignored by git, but part of the project\n")
    return folder


def git(code_dir: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=code_dir, capture_output=True, text=True, check=True).stdout


def zip_folder(source: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    return target


def put_zip(manager, user_id: int, run_id: int, zip_path: Path) -> None:
    run_dir = manager.run_dir(user_id, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(zip_path, run_dir / "source.zip")


def tools_for(code_dir: Path, **limits) -> CodeTools:
    return CodeTools(code_dir, **limits)


# --- Functional ---------------------------------------------------------------------------------------

def test_B6_F01_clone_a_small_public_repo(manager):
    workspace = manager.prepare(1, 1, "github", HELLO_WORLD, None)
    assert (workspace.code_dir / "README").is_file()
    assert workspace.source_commit == HELLO_WORLD_MASTER == workspace.base_commit
    assert workspace.artifacts_dir.is_dir() and workspace.code_dir == manager.run_dir(1, 1) / "code"


def test_B6_F02_clone_a_given_branch(manager):
    branch, commit = HELLO_WORLD_PATCH_BRANCH
    workspace = manager.prepare(1, 2, "github", HELLO_WORLD, branch)
    assert workspace.source_commit == commit
    assert git(workspace.code_dir, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch


def test_B6_F03_local_copy_of_a_folder_that_is_not_a_git_repo(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    workspace = manager.prepare(1, 3, "local_path", str(source), None)
    code = workspace.code_dir
    assert (code / "src/main/java/shop/Discount.java").read_text() == JAVA
    assert (code / "debug.log").exists()  # ignored by .gitignore, still copied and recorded
    assert not (code / "target").exists() and not (code / "node_modules").exists()
    assert git(code, "log", "--format=%s").strip() == "SANGRA_AI: the original code"
    assert "debug.log" in git(code, "ls-files")
    assert git(code, "status", "--porcelain") == ""
    assert workspace.source_commit is None and len(workspace.base_commit) == 40
    assert {p: p.read_bytes() for p in source.rglob("*") if p.is_file()} == before  # the original is untouched
    assert not (source / ".git").exists()


def test_B6_F04_zip_unpack(manager, tmp_path):
    zip_path = zip_folder(make_project(tmp_path / "shop"), tmp_path / "shop.zip")
    put_zip(manager, 1, 4, zip_path)
    workspace = manager.prepare(1, 4, "zip", None, None)
    assert (workspace.code_dir / "src/main/java/shop/Discount.java").read_text() == JAVA
    assert git(workspace.code_dir, "status", "--porcelain") == "" and len(workspace.base_commit) == 40


def test_B6_F04_github_download_zip_loses_its_single_top_folder(manager, tmp_path):
    # Decided after review: GitHub's "Download ZIP" puts everything in one top folder (shop-main/).
    source = make_project(tmp_path / "shop")
    with zipfile.ZipFile(tmp_path / "github.zip", "w") as archive:
        archive.writestr("shop-main/", "")
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, "shop-main/" + path.relative_to(source).as_posix())
    put_zip(manager, 1, 12, tmp_path / "github.zip")
    code = manager.prepare(1, 12, "zip", None, None).code_dir
    assert (code / "src/main/java/shop/Discount.java").read_text() == JAVA and (code / "config.yml").exists()
    assert not (code / "shop-main").exists()


def test_B6_F04_zip_paths_stay_when_there_is_no_single_top_folder(manager, tmp_path):
    with zipfile.ZipFile(tmp_path / "two_tops.zip", "w") as archive:
        archive.writestr("src/A.java", "class A {}")
        archive.writestr("test/ATest.java", "class ATest {}")
    put_zip(manager, 1, 13, tmp_path / "two_tops.zip")
    code = manager.prepare(1, 13, "zip", None, None).code_dir
    assert (code / "src/A.java").exists() and (code / "test/ATest.java").exists()
    with zipfile.ZipFile(tmp_path / "root_file.zip", "w") as archive:
        archive.writestr("pom.xml", "<project/>")
        archive.writestr("src/A.java", "class A {}")
    put_zip(manager, 1, 14, tmp_path / "root_file.zip")
    code = manager.prepare(1, 14, "zip", None, None).code_dir
    assert (code / "pom.xml").exists() and (code / "src/A.java").exists()


def test_B6_F05_edit_then_undo_restores_byte_for_byte(manager, tmp_path):
    workspace = manager.prepare(1, 5, "local_path", str(make_project(tmp_path / "shop")), None)
    code = workspace.code_dir
    original_crlf = (code / "windows.txt").read_bytes()
    (code / "windows.txt").write_bytes(b"changed\r\n")
    (code / "src/main/java/shop/Discount.java").write_text(JAVA.replace("0.05", "0.10"))
    (code / "src/new_file.txt").write_text("new")
    manager.undo(code)
    assert (code / "windows.txt").read_bytes() == original_crlf == b"line one\r\nline two\r\n"
    assert (code / "src/main/java/shop/Discount.java").read_text() == JAVA
    assert not (code / "src/new_file.txt").exists()
    assert git(code, "status", "--porcelain") == ""


def test_B6_F06_diff_is_a_valid_patch(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    workspace = manager.prepare(1, 6, "local_path", str(source), None)
    (workspace.code_dir / "src/main/java/shop/Discount.java").write_text(JAVA.replace("0.05", "0.10"))
    patch = manager.diff(workspace.code_dir)
    assert "-    double rate = 0.05; // summer sale" in patch and "+    double rate = 0.10; // summer sale" in patch
    fresh = manager.prepare(1, 7, "local_path", str(source), None)
    (tmp_path / "change.patch").write_text(patch)
    subprocess.run(["git", "apply", "--check", str(tmp_path / "change.patch")], cwd=fresh.code_dir, check=True)


def test_B6_F07_search_files(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    for n in range(30):
        (source / f"src/Promo{n:02d}.java").write_text("// discount " * (n % 3 + 1))
    workspace = manager.prepare(1, 8, "local_path", str(source), None)
    result = tools_for(workspace.code_dir).search_files("DISCOUNT")
    paths = [f["path"] for f in result["files"]]
    assert len(paths) == 20 and result["total_files_matched"] == 33 and result["truncated"] is True  # 30 + 3
    everything = tools_for(workspace.code_dir, max_files=100).search_files("discount")
    all_paths = [f["path"] for f in everything["files"]]
    assert "src/main/resources/app.properties" in all_paths and "config.yml" in all_paths  # config files included
    assert "src/main/java/shop/Discount.java" in all_paths
    discount = next(f for f in everything["files"] if f["path"] == "src/main/java/shop/Discount.java")
    assert discount["name_match"] is True and discount["hits"] == 1
    assert not any(p.startswith(("target/", "node_modules/")) for p in all_paths)
    hits = [f["hits"] + f["name_match"] for f in everything["files"]]
    assert hits == sorted(hits, reverse=True)


def test_B6_F08_search_in_file(manager, tmp_path):
    workspace = manager.prepare(1, 9, "local_path", str(make_project(tmp_path / "shop")), None)
    result = tools_for(workspace.code_dir).search_in_file("src/main/java/shop/Discount.java", "RATE")
    assert result["total_matches"] == 2 and [m["line"] for m in result["matches"]] == [4, 5]
    context = result["matches"][0]["context"]
    assert [c["line"] for c in context] == [2, 3, 4, 5, 6]
    assert context[2]["text"] == "    double rate = 0.05; // summer sale"


def test_B6_F09_read_file_lines(manager, tmp_path):
    workspace = manager.prepare(1, 10, "local_path", str(make_project(tmp_path / "shop")), None)
    result = tools_for(workspace.code_dir).read_file_lines("src/main/java/shop/Discount.java", 3, 5)
    assert [l["line"] for l in result["lines"]] == [3, 4, 5]
    assert [l["text"] for l in result["lines"]] == JAVA.splitlines()[2:5]
    crlf = tools_for(workspace.code_dir).read_file_lines("windows.txt", 1, 2)
    assert [l["text"] for l in crlf["lines"]] == ["line one", "line two"]


def test_B6_F10_delete_the_workspace(manager, tmp_path):
    workspace = manager.prepare(1, 11, "local_path", str(make_project(tmp_path / "shop")), None)
    manager.delete(workspace.run_dir)
    assert not workspace.run_dir.exists()
    with pytest.raises(PathRefused):
        manager.delete(tmp_path)  # never outside the workspaces folder
    assert tmp_path.exists()


# --- Failure: sources -----------------------------------------------------------------------------

def test_B6_X01_repo_missing_private_or_network_down(manager, monkeypatch):
    with pytest.raises(WorkspaceError) as error:
        manager.prepare(1, 20, "github", "https://github.com/octocat/this-repo-does-not-exist-sangra", None)
    assert error.value.reason == "clone_failed" and "does not exist or is private" in error.value.message
    assert not (manager.run_dir(1, 20) / "code").exists()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    with pytest.raises(WorkspaceError) as error:
        manager.prepare(1, 21, "github", HELLO_WORLD, None)
    assert error.value.reason == "clone_failed" and "could not be reached" in error.value.message


def test_B6_X02_branch_does_not_exist(manager):
    with pytest.raises(WorkspaceError) as error:
        manager.prepare(1, 22, "github", HELLO_WORLD, "no-such-branch-sangra")
    assert error.value.reason == "clone_failed" and "no-such-branch-sangra" in error.value.message


@pytest.mark.settings(WORKSPACE_MAX_BYTES="2000")
def test_B6_X03_repo_larger_than_the_limit(manager):
    with pytest.raises(WorkspaceError) as error:
        manager.prepare(1, 23, "github", HELLO_WORLD, None)
    assert "larger than" in error.value.message
    assert not (manager.run_dir(1, 23) / "code").exists()  # the partial folder is deleted


@pytest.mark.settings(CLONE_TIMEOUT_SECONDS="0.01")
def test_B6_X04_clone_takes_too_long(manager):
    with pytest.raises(WorkspaceError) as error:
        manager.prepare(1, 24, "github", HELLO_WORLD, None)
    assert "took longer than" in error.value.message
    assert not (manager.run_dir(1, 24) / "code").exists()


def test_B6_X05_bad_local_paths(manager, tmp_path, monkeypatch):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    empty = tmp_path / "empty"
    empty.mkdir()
    for path, words in [(tmp_path / "deleted", "does not exist"), (a_file, "is not a folder"), (empty, "is empty"),
                        (Path("relative/path"), "absolute")]:
        with pytest.raises(WorkspaceError) as error:
            manager.prepare(1, 25, "local_path", str(path), None)
        assert error.value.reason == "clone_failed" and words in error.value.message
    # Not readable (the tests run as root, so the operating system's refusal is simulated).
    locked = make_project(tmp_path / "locked")
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda p, mode: False if Path(p) == locked else real_access(p, mode))
    with pytest.raises(WorkspaceError, match="cannot be read"):
        manager.prepare(1, 26, "local_path", str(locked), None)


def test_B6_X06_symlinks_pointing_outside_are_not_followed(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    (source / "etc_link").symlink_to("/etc")
    (source / "passwd_link").symlink_to("/etc/passwd")
    (source / "src/inner_link").symlink_to(tmp_path)
    workspace = manager.prepare(1, 27, "local_path", str(source), None)
    code = workspace.code_dir
    assert not (code / "etc_link").exists() and not (code / "etc_link").is_symlink()
    assert not (code / "passwd_link").exists() and not (code / "src/inner_link").exists()
    assert not any(p.is_symlink() for p in code.rglob("*"))


def test_B6_X07_not_a_zip_corrupt_or_empty(manager, tmp_path):
    cases = {}
    (tmp_path / "text.zip").write_text("this is a text file renamed to .zip")
    cases["text.zip"] = "not a zip"
    good = zip_folder(make_project(tmp_path / "shop"), tmp_path / "good.zip")
    data = bytearray(good.read_bytes())
    data[60:400] = b"\x00" * 340  # damage the compressed data, keep the directory
    (tmp_path / "corrupt.zip").write_bytes(bytes(data))
    cases["corrupt.zip"] = "damaged"
    with zipfile.ZipFile(tmp_path / "empty.zip", "w"):
        pass
    cases["empty.zip"] = "not a zip|empty"
    with zipfile.ZipFile(tmp_path / "only_dirs.zip", "w") as archive:
        archive.writestr("folder/", "")
    cases["only_dirs.zip"] = "empty"
    for run_id, (name, words) in enumerate(cases.items(), start=30):
        put_zip(manager, 1, run_id, tmp_path / name)
        with pytest.raises(WorkspaceError, match=words):
            manager.prepare(1, run_id, "zip", None, None)
        assert not (manager.run_dir(1, run_id) / "code").exists()


@pytest.mark.settings(WORKSPACE_MAX_BYTES=str(10 * 1024 * 1024))
def test_B6_X08_zip_bomb_and_too_many_files(manager, tmp_path, settings):
    with zipfile.ZipFile(tmp_path / "bomb.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.bin", b"\x00" * (50 * 1024 * 1024))  # 50 MB of zeros, about 50 KB zipped
    assert (tmp_path / "bomb.zip").stat().st_size < 200_000
    put_zip(manager, 1, 40, tmp_path / "bomb.zip")
    with pytest.raises(WorkspaceError, match="unpacks to more than"):
        manager.prepare(1, 40, "zip", None, None)

    # A zip that lies about its size in the directory: never unpacked beyond what it claims.
    with zipfile.ZipFile(tmp_path / "liar.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.bin", b"\x00" * (20 * 1024 * 1024))
    data = bytearray((tmp_path / "liar.zip").read_bytes())
    central = data.find(b"PK\x01\x02")
    data[central + 24:central + 28] = struct.pack("<I", 1000)  # claims 1,000 bytes
    local = data.find(b"PK\x03\x04")
    data[local + 22:local + 26] = struct.pack("<I", 1000)
    (tmp_path / "liar.zip").write_bytes(bytes(data))
    put_zip(manager, 1, 41, tmp_path / "liar.zip")
    with pytest.raises(WorkspaceError):
        manager.prepare(1, 41, "zip", None, None)
    assert not (manager.run_dir(1, 41) / "code").exists()

    with zipfile.ZipFile(tmp_path / "many.zip", "w", zipfile.ZIP_STORED) as archive:
        for n in range(100_000):
            archive.writestr(f"f/{n}.txt", b"")
    put_zip(manager, 1, 42, tmp_path / "many.zip")
    with pytest.raises(WorkspaceError, match="more than 50000 files"):
        manager.prepare(1, 42, "zip", None, None)


@pytest.mark.parametrize("bad_name", ["../../evil.txt", "/etc/sangra_evil", "C:\\sangra_evil.txt", "..\\evil.txt",
                                      "ok/../../evil.txt"])
def test_B6_X09_zip_slip_entries_are_refused(manager, tmp_path, bad_name):
    with zipfile.ZipFile(tmp_path / "slip.zip", "w") as archive:
        archive.writestr("good.txt", "fine")
        archive.writestr(zipfile.ZipInfo(bad_name), "evil")
    put_zip(manager, 1, 50, tmp_path / "slip.zip")
    with pytest.raises(WorkspaceError, match="unsafe path"):
        manager.prepare(1, 50, "zip", None, None)
    for place in [manager.root, manager.root.parent, Path("/etc"), tmp_path]:
        assert not list(place.rglob("*evil*")) if place != Path("/etc") else not Path("/etc/sangra_evil").exists()


def test_B6_X09_symlink_entry_is_refused(manager, tmp_path):
    with zipfile.ZipFile(tmp_path / "link.zip", "w") as archive:
        archive.writestr("good.txt", "fine")
        link = zipfile.ZipInfo("passwd")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    put_zip(manager, 1, 51, tmp_path / "link.zip")
    with pytest.raises(WorkspaceError, match="symbolic link"):
        manager.prepare(1, 51, "zip", None, None)


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "..\\x", "%2e%2e/x", "a/%2e%2e/%2e%2e/x", "a\x00b",
                                  "x" * 5000, "a/" + "y" * 300, "link_out/passwd", ".git/config", "src/../.git/HEAD",
                                  "C:/Windows/win.ini", "", "   "])
def test_B6_X10_path_guard(manager, tmp_path, path):
    workspace = manager.prepare(1, 60, "local_path", str(make_project(tmp_path / "shop")), None)
    (workspace.code_dir / "link_out").symlink_to("/etc")
    with pytest.raises(PathRefused):
        safe_path(workspace.code_dir, path)
    result = tools_for(workspace.code_dir).read_file_lines(path, 1, 1)
    assert "error" in result


def test_B6_X10_path_guard_allows_normal_paths(manager, tmp_path):
    workspace = manager.prepare(1, 61, "local_path", str(make_project(tmp_path / "shop")), None)
    target = safe_path(workspace.code_dir, "src/main/java/shop/Discount.java")
    assert target == (workspace.code_dir / "src/main/java/shop/Discount.java").resolve()
    assert safe_path(workspace.code_dir, "./config.yml").name == "config.yml"


def test_B6_X11_odd_search_text(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    (source / "regex.txt").write_text("price ( [ * \\ special\n")
    tools = tools_for(manager.prepare(1, 62, "local_path", str(source), None).code_dir)
    assert "error" in tools.search_files("")
    assert "error" in tools.search_files("   ")
    assert "error" in tools.search_files("x" * 10_000)
    assert "error" in tools.search_files(None)  # type: ignore[arg-type]
    for special in ["( [ *", "\\", "*", "["]:
        result = tools.search_files(special)
        assert "error" not in result
    assert [f["path"] for f in tools.search_files("( [ * \\")["files"]] == ["regex.txt"]


def test_B6_X12_skipped_and_capped_files(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    (source / "image.png").write_bytes(b"\x89PNG\x00\x00discount\x00")
    for folder in ["build", "__pycache__", ".venv/lib"]:
        (source / folder).mkdir(parents=True)
        (source / folder / "discount.txt").write_text("discount")
    (source / "big.txt").write_bytes(b"discount\n" + b"x" * (1024 * 1024 + 10))
    (source / "one_long_line.txt").write_bytes(b"discount" + b"y" * (10 * 1024 * 1024))
    (source / "latin1.txt").write_bytes("Prix réduit: discount 5% ¤\n".encode("latin-1"))
    workspace = manager.prepare(1, 63, "local_path", str(source), None)
    tools = tools_for(workspace.code_dir, max_files=100)
    paths = [f["path"] for f in tools.search_files("discount")["files"]]
    assert "latin1.txt" in paths  # a non-UTF8 file is still searched
    for skipped in ["image.png", "build/discount.txt", "__pycache__/discount.txt", ".venv/lib/discount.txt",
                    "big.txt", "one_long_line.txt"]:
        assert skipped not in paths
    assert "binary" in tools.read_file_lines("image.png", 1, 1)["error"]
    assert "larger than" in tools.read_file_lines("one_long_line.txt", 1, 1)["error"]
    assert "generated" in tools.read_file_lines("build/discount.txt", 1, 1)["error"]
    assert tools.read_file_lines("latin1.txt", 1, 1)["lines"][0]["text"].startswith("Prix r")


def test_B6_X13_read_file_lines_edges(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    (source / "empty.txt").write_text("")
    (source / "long.txt").write_text("\n".join(f"line {n}" for n in range(1, 1001)))  # no newline at the end
    tools = tools_for(manager.prepare(1, 64, "local_path", str(source), None).code_dir)
    assert "1 or more" in tools.read_file_lines("long.txt", 0, 5)["error"]
    assert "1 or more" in tools.read_file_lines("long.txt", -3, 5)["error"]
    assert "smaller" in tools.read_file_lines("long.txt", 10, 5)["error"]
    assert "only 1000 lines" in tools.read_file_lines("long.txt", 1001, 1005)["error"]
    capped = tools.read_file_lines("long.txt", 1, 500)
    assert len(capped["lines"]) == 200 and capped["end_line"] == 200 and capped["truncated"] is True
    tail = tools.read_file_lines("long.txt", 995, 2000)
    assert [l["text"] for l in tail["lines"]][-1] == "line 1000" and tail["truncated"] is False
    assert "empty" in tools.read_file_lines("empty.txt", 1, 1)["error"]
    assert "whole numbers" in tools.read_file_lines("long.txt", "1", 2)["error"]  # type: ignore[arg-type]
    assert "whole numbers" in tools.read_file_lines("long.txt", True, 2)["error"]  # type: ignore[arg-type]
    assert "does not exist" in tools.read_file_lines("missing.txt", 1, 1)["error"]


def test_B6_X14_tool_output_is_always_capped(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    (source / "wide.txt").write_text("\n".join("discount " + "w" * 2000 for _ in range(500)))
    for n in range(200):
        (source / f"{'d' * 150}_{n}_discount.txt").write_text("discount")
    tools = tools_for(manager.prepare(1, 65, "local_path", str(source), None).code_dir, max_files=500)
    for result in [tools.read_file_lines("wide.txt", 1, 200), tools.search_in_file("wide.txt", "discount"),
                   tools.search_files("discount")]:
        assert len(json.dumps(result, ensure_ascii=False)) <= 16_000
        assert result["truncated"] is True
    line = tools.read_file_lines("wide.txt", 1, 1)["lines"][0]["text"]
    assert len(line) < 400 and line.endswith("(line cut)")


def test_B6_X15_undo_with_a_locked_file(manager, tmp_path):
    workspace = manager.prepare(1, 66, "local_path", str(make_project(tmp_path / "shop")), None)
    target = workspace.code_dir / "config.yml"
    target.write_text("changed\n")
    lock = subprocess.run(["chattr", "+i", str(target)], capture_output=True)
    assert lock.returncode == 0, "this test needs chattr (Linux) to lock a file"
    try:
        with pytest.raises(WorkspaceError) as error:
            manager.undo(workspace.code_dir)
        assert error.value.reason == "undo_failed"
    finally:
        subprocess.run(["chattr", "-i", str(target)], check=True)


# --- Security -------------------------------------------------------------------------------------

def hostile_git(folder: Path, marker: Path) -> None:
    """A .git folder whose config and hooks would run a command (writing `marker`) if git trusted it."""
    git_dir = folder / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    command = f"touch {marker}"
    (git_dir / "config").write_text(
        "[core]\n"
        f"\tfsmonitor = \"{command}\"\n"
        f"\tsshCommand = \"{command}\"\n"
        f"\tpager = \"{command}\"\n"
        "[filter \"evil\"]\n"
        f"\tclean = \"{command}\"\n"
        f"\tsmudge = \"{command}\"\n"
        "\trequired = true\n"
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    for hook in ["pre-commit", "post-commit", "post-checkout", "pre-auto-gc"]:
        (git_dir / "hooks" / hook).write_text(f"#!/bin/sh\n{command}\n")
        (git_dir / "hooks" / hook).chmod(0o755)
    (folder / ".gitattributes").write_text("* filter=evil\n")


def assert_never_ran(manager, workspace, marker: Path) -> None:
    code = workspace.code_dir
    (code / "config.yml").write_text("changed\n")
    manager.diff(code)
    manager.undo(code)
    assert not marker.exists()
    config = (code / ".git/config").read_text()
    assert "fsmonitor" not in config and "evil" not in config  # the .git is ours, not the user's


def test_B6_S01_zip_with_a_hostile_git_folder(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    marker = tmp_path / "PWNED_zip"
    hostile_git(source, marker)
    with zipfile.ZipFile(tmp_path / "hostile.zip", "w") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    put_zip(manager, 1, 70, tmp_path / "hostile.zip")
    workspace = manager.prepare(1, 70, "zip", None, None)
    assert_never_ran(manager, workspace, marker)


def test_B6_S02_local_folder_with_a_hostile_git_folder(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    marker = tmp_path / "PWNED_local"
    hostile_git(source, marker)
    workspace = manager.prepare(1, 71, "local_path", str(source), None)
    assert_never_ran(manager, workspace, marker)


def test_B6_S03_the_code_tools_only_read(manager, tmp_path):
    source_text = (BACKEND_DIR / "app/agent/tools/code_tools.py").read_text()
    tree = ast.parse(source_text)
    forbidden_calls = {"remove", "unlink", "rmdir", "rmtree", "rename", "move", "copy", "copyfile",
                       "write", "write_text", "write_bytes", "chmod", "mkdir", "makedirs", "system", "popen",
                       "run", "Popen", "call", "check_call", "check_output", "symlink_to", "touch", "truncate"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in forbidden_calls, f"code_tools calls {name}"
            owner = node.func.value if isinstance(node.func, ast.Attribute) else None
            if isinstance(owner, ast.Name) and owner.id == "os":  # os.replace, os.rename ... (str.replace is fine)
                assert name not in {"replace", "rename", "renames", "truncate", "link", "symlink"}, f"os.{name}"
            if name == "open" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert set(node.args[1].value) <= {"r", "b"}, "code_tools opens a file for writing"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not {"subprocess", "shutil"} & set(names)
    public = {name for name in dir(CodeTools) if not name.startswith("_")}
    assert public == {"search_files", "search_in_file", "read_file_lines"}

    workspace = manager.prepare(1, 72, "local_path", str(make_project(tmp_path / "shop")), None)
    for path in workspace.code_dir.rglob("*"):
        if ".git" not in path.parts:
            path.chmod(path.stat().st_mode & ~0o222)
    tools = tools_for(workspace.code_dir)
    assert tools.search_files("discount")["files"]
    assert tools.search_in_file("config.yml", "summer")["matches"]
    assert tools.read_file_lines("config.yml", 1, 2)["lines"]


def test_B6_S04_odd_file_names_are_never_run(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    names = ["with space.txt", "quote'\"s.txt", "semi;touch PWNED_names;.txt", "$(touch PWNED_names).txt",
             "`touch PWNED_names`.txt", "new\nline.txt", "-rf.txt", "--help.txt"]
    for name in names:
        (source / name).write_text("discount")
    workspace = manager.prepare(1, 73, "local_path", str(source), None)
    for name in names:
        (workspace.code_dir / name).write_text("changed discount")
    assert manager.diff(workspace.code_dir).count("+changed discount") == len(names)
    manager.undo(workspace.code_dir)
    for place in [workspace.code_dir, workspace.run_dir, source, Path.cwd()]:
        assert not (place / "PWNED_names").exists()
    assert len(tools_for(workspace.code_dir, max_files=100).search_files("discount")["files"]) >= len(names)


@pytest.mark.parametrize("url", ["-https://github.com/a/b", "--upload-pack=touch /tmp/x", "ext::sh -c touch% /tmp/x",
                                 "https://github.com/a/b --config=x", "file:///etc"])
def test_B6_S05_bad_clone_urls_never_reach_git(manager, url, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
    with pytest.raises(WorkspaceError, match="Refused before running git"):
        manager.prepare(1, 74, "github", url, None)
    with pytest.raises(WorkspaceError, match="Refused before running git"):
        manager.prepare(1, 74, "github", HELLO_WORLD, "--upload-pack=x")
    assert calls == []


# --- Concurrency ----------------------------------------------------------------------------------

def test_B6_C01_C03_parallel_builds_stay_separate(manager, tmp_path):
    source = make_project(tmp_path / "shop")

    def build(run_id):
        return manager.prepare(1, 100 + run_id, "local_path", str(source), None)

    with ThreadPoolExecutor(5) as pool:
        workspaces = list(pool.map(build, range(5)))
    assert len({w.code_dir for w in workspaces}) == 5
    for n, workspace in enumerate(workspaces):
        (workspace.code_dir / "config.yml").write_text(f"run {n}\n")
    for n, workspace in enumerate(workspaces):  # B6-C03: same source, independent copies
        assert (workspace.code_dir / "config.yml").read_text() == f"run {n}\n"
        assert manager.diff(workspace.code_dir).count(f"+run {n}") == 1


def test_B6_C02_delete_while_a_tool_reads(manager, tmp_path):
    source = make_project(tmp_path / "shop")
    for n in range(3000):
        (source / f"f{n}.txt").write_text("discount " * 20)
    workspace = manager.prepare(1, 110, "local_path", str(source), None)
    tools = tools_for(workspace.code_dir)
    results = []
    reader = threading.Thread(target=lambda: results.extend(tools.search_files("discount") for _ in range(5)))
    reader.start()
    time.sleep(0.05)
    manager.delete(workspace.run_dir)
    reader.join(timeout=30)
    assert len(results) == 5 and all(isinstance(r, dict) for r in results)
    gone = tools.read_file_lines("config.yml", 1, 1)
    assert "error" in gone and "does not exist" in gone["error"]


# --- Load -----------------------------------------------------------------------------------------

@pytest.mark.slow
def test_B6_L01_search_over_50000_files(manager, tmp_path):
    source = tmp_path / "huge"
    for folder in range(100):
        (source / f"pkg{folder}").mkdir(parents=True)
        for n in range(500):
            text = "class Item {}\n" * 5 + ("double discount = 0.05;\n" if n % 997 == 0 else "")
            (source / f"pkg{folder}" / f"Item{n}.java").write_text(text)
    workspace = manager.prepare(1, 120, "local_path", str(source), None)
    tools = tools_for(workspace.code_dir)
    started = time.perf_counter()
    result = tools.search_files("discount")
    seconds = time.perf_counter() - started
    print(f"\nB6-L01: search_files over 50,000 files took {seconds:.2f} s ({result['total_files_matched']} matches)")
    assert result["total_files_matched"] == 100
    assert seconds < 3


@pytest.mark.slow
def test_B6_L02_read_lines_of_a_1mb_file(manager, tmp_path):
    source = tmp_path / "one"
    source.mkdir()
    line = "x" * 99 + "\n"
    (source / "big.txt").write_text(line * 10_000)  # 1,000,000 bytes
    (source / "typical.txt").write_text(("    int total = price * quantity; // ok\n") * 300)
    tools = tools_for(manager.prepare(1, 121, "local_path", str(source), None).code_dir)
    timings = []
    for start in (1, 5000, 9801):
        t = time.perf_counter()
        result = tools.read_file_lines("big.txt", start, start + 199)
        timings.append((time.perf_counter() - t) * 1000)
        # 200 lines of 100 characters are more than the 16,000-character answer cap, so the answer is cut
        # (from the end) and says so. The cap number is checked against the model's context in B8.
        numbers = [l["line"] for l in result["lines"]]
        assert numbers == list(range(start, start + len(numbers))) and result["truncated"] is True
        assert len(json.dumps(result)) <= 16_000
    typical = tools_for(source).read_file_lines("typical.txt", 1, 200)
    assert len(typical["lines"]) == 200  # 200 lines of usual width (40 characters) fit
    print(f"\nB6-L02: read 200 lines of a 1 MB file: start {timings[0]:.1f} ms, middle {timings[1]:.1f} ms, "
          f"end {timings[2]:.1f} ms")
    assert max(timings) < 50


def _disk_used(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


@pytest.mark.slow
def test_B6_L03_10_parallel_builds_of_a_100mb_repo(manager, tmp_path):
    source = tmp_path / "big_repo"
    source.mkdir()
    for n in range(100):
        (source / f"blob{n}.bin").write_bytes(os.urandom(1024 * 1024))
    start_use = _disk_used(manager.root) if manager.root.exists() else 0
    started = time.perf_counter()
    with ThreadPoolExecutor(10) as pool:
        workspaces = list(pool.map(lambda n: manager.prepare(2, 200 + n, "local_path", str(source), None), range(10)))
    seconds = time.perf_counter() - started
    used = _disk_used(manager.root) - start_use
    for workspace in workspaces:
        manager.delete(workspace.run_dir)
    after = _disk_used(manager.root)
    print(f"\nB6-L03: 10 parallel builds of a 100 MB folder took {seconds:.1f} s and used {used / 1024 ** 3:.2f} GB "
          f"(copy + git objects); after deleting: {after - start_use} bytes")
    assert all((w.code_dir.parent).exists() is False for w in workspaces)
    assert after == start_use


def test_B6_L04_cleanup_of_200_old_folders(manager):
    now = time.time()
    old, new = [], []
    for n in range(200):
        run_dir = manager.run_dir(3 + n % 7, 1000 + n)
        (run_dir / "code").mkdir(parents=True)
        (run_dir / "code" / "file.txt").write_text("x")
        age_hours = 25 if n < 150 else 23
        os.utime(run_dir, (now - age_hours * 3600, now - age_hours * 3600))
        (old if n < 150 else new).append(run_dir)
    removed = manager.delete_older_than(24 * 3600, now=now)
    assert sorted(removed) == sorted(old)
    assert all(not p.exists() for p in old) and all(p.exists() for p in new)
    assert manager.delete_older_than(24 * 3600, now=now) == []  # running it again changes nothing
