"""File and search tools never follow a link put on the way after their paths were checked (arch_v2).

Another process (a sandboxed command, another user's session) can swap a
directory for a link to someone else's files at any moment: during a
permission wait, between two checks, or while the operation itself runs.
Checks before and after only narrow that window; the tools close it by
going through directories opened without following a link.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from flocks.tool.code import grep as grep_module
from flocks.tool import nofollow
from flocks.tool.file import copy as copy_module, delete as delete_module, glob as glob_module, move as move_module
from flocks.tool.file.apply_patch import apply_patch_tool
from flocks.tool.file.copy import copy_tool
from flocks.tool.file.delete import delete_tool
from flocks.tool.file.edit import edit_tool
from flocks.tool.file.mkdir import mkdir_tool
from flocks.tool.file.move import move_tool
from flocks.tool.file.read import read_tool
from flocks.tool.file.write import write_tool
from flocks.tool.registry import ToolContext
from tests.helpers.filesystem_races import swap_after_listing, swap_for_link

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32", reason="links need privileges on Windows"),
]

FILE_TOOLS = ["write", "edit", "apply_patch", "mkdir", "delete", "copy", "move"]


@pytest.fixture
def places(tmp_path: Path) -> Path:
    """work/report.txt is mine; other_user/report.txt is theirs, with the same text."""
    root = tmp_path.resolve()
    (root / "work").mkdir()
    (root / "work" / "report.txt").write_text("mine\n", encoding="utf-8")
    (root / "other_user").mkdir()
    # Same text: comparing contents alone does not tell the two apart.
    (root / "other_user" / "report.txt").write_text("mine\n", encoding="utf-8")
    return root


def _ctx(root: Path, requests: list | None = None) -> ToolContext:
    async def approve(request) -> None:
        if requests is not None:
            requests.append(request)

    return ToolContext(
        session_id="ses_no_follow", message_id="msg_no_follow",
        permission_callback=approve, extra={"workspace_dir": str(root)},
    )


def _calls(ctx: ToolContext) -> dict:
    return {
        "write": lambda: write_tool(ctx=ctx, content="new\n", filePath="work/report.txt"),
        "edit": lambda: edit_tool(
            ctx=ctx, filePath="work/report.txt", edits=[{"oldString": "mine", "newString": "changed"}],
        ),
        "apply_patch": lambda: apply_patch_tool(ctx=ctx, patchText=(
            "*** Begin Patch\n*** Update File: work/report.txt\n@@\n-mine\n+changed\n*** End Patch\n"
        )),
        "mkdir": lambda: mkdir_tool(ctx=ctx, path="work/new_dir"),
        "delete": lambda: delete_tool(ctx=ctx, path="work/report.txt"),
        "copy": lambda: copy_tool(ctx=ctx, sourcePath="work/report.txt", targetPath="work/copy.txt"),
        "move": lambda: move_tool(ctx=ctx, sourcePath="work/report.txt", targetPath="work/moved.txt"),
    }


@pytest.mark.parametrize("tool", FILE_TOOLS)
async def test_a_link_put_on_the_way_after_the_last_check_is_not_followed(
    places: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """work/ becomes a link to their files right after the last check, as the operation starts.

    Every check the tool makes still passes; only not following the link
    keeps the operation out of their files.
    """
    module = importlib.import_module(f"flocks.tool.file.{tool}")

    def swap() -> None:
        swap_for_link(places, "work", places / "other_user")

    if hasattr(module, "ask_edit_permission"):
        real_ask = module.ask_edit_permission

        async def ask_then_swap(*args, **kwargs):
            unchanged = await real_ask(*args, **kwargs)
            swap()
            return unchanged

        monkeypatch.setattr(module, "ask_edit_permission", ask_then_swap)
    else:
        real_check = module.paths_changed

        def check_then_swap(identities):
            changed = real_check(identities)
            swap()
            return changed

        monkeypatch.setattr(module, "paths_changed", check_then_swap)

    result = await _calls(_ctx(places))[tool]()

    assert result.success is False
    assert "changed" in (result.error or "")
    assert sorted(entry.name for entry in (places / "other_user").iterdir()) == ["report.txt"]
    assert (places / "other_user" / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert (places / "work_parked" / "report.txt").read_text(encoding="utf-8") == "mine\n"


async def test_a_directory_swapped_while_a_tree_is_copied_brings_nothing_of_theirs(
    places: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """src/b is a real directory when the copy lists src, and a link to their files when it gets there."""
    src = places / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "one.txt").write_text("one\n", encoding="utf-8")
    (src / "b").mkdir()
    (src / "b" / "two.txt").write_text("two\n", encoding="utf-8")
    (places / "other_user" / "their-private-name.txt").write_text("theirs\n", encoding="utf-8")
    copying = {"started": False}
    real_ask = copy_module.ask_edit_permission

    async def ask_then_copy(*args, **kwargs):
        unchanged = await real_ask(*args, **kwargs)
        copying["started"] = True
        return unchanged

    monkeypatch.setattr(copy_module, "ask_edit_permission", ask_then_copy)
    swap_after_listing(
        monkeypatch, src, "b",
        lambda: copying["started"] and swap_for_link(src, "b", places / "other_user"),
    )

    result = await copy_tool(ctx=_ctx(places), sourcePath="src", targetPath="dest")

    assert result.success is False
    copied = {path.name for path in (places / "dest").rglob("*")}
    assert "their-private-name.txt" not in copied and "report.txt" not in copied
    assert (places / "other_user" / "their-private-name.txt").read_text(encoding="utf-8") == "theirs\n"


@pytest.mark.parametrize("tool", ["copy", "delete", "move"])
async def test_an_entry_put_in_the_tree_after_the_approval_is_left_alone(
    places: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """The approval listed the tree; a file put in it after the last check is not copied, removed or moved.

    Path rules saw only what was listed: one denying the new file's path
    would otherwise be passed by.
    """
    tree = places / "work" / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a\n", encoding="utf-8")
    module = {"copy": copy_module, "delete": delete_module, "move": move_module}[tool]
    real_ask = module.ask_edit_permission

    async def ask_then_add(*args, **kwargs):
        unchanged = await real_ask(*args, **kwargs)
        (tree / "protected.txt").write_text("theirs\n", encoding="utf-8")
        return unchanged

    monkeypatch.setattr(module, "ask_edit_permission", ask_then_add)
    requests: list = []
    ctx = _ctx(places, requests)
    calls = {
        "copy": lambda: copy_tool(ctx=ctx, sourcePath="work/tree", targetPath="dest"),
        "delete": lambda: delete_tool(ctx=ctx, path="work/tree"),
        "move": lambda: move_tool(ctx=ctx, sourcePath="work/tree", targetPath="moved"),
    }

    result = await calls[tool]()

    assert "protected.txt" not in repr([request.patterns for request in requests])
    assert (tree / "protected.txt").read_text(encoding="utf-8") == "theirs\n"
    if tool == "copy":
        assert result.success is True, result.error
        assert (places / "dest" / "a.txt").exists() and not (places / "dest" / "protected.txt").exists()
    else:
        # Removing or moving the tree would take the new file along: the tool
        # stops and says the tree changed.
        assert result.success is False and "changed" in (result.error or "")
        assert not (places / "moved").exists()


async def test_copy_onto_or_into_the_source_keeps_it(places: Path) -> None:
    ctx = _ctx(places)

    into_own_directory = await copy_tool(ctx=ctx, sourcePath="work/report.txt", targetPath="work")
    into_itself = await copy_tool(ctx=ctx, sourcePath="work", targetPath="work/backup")

    assert into_own_directory.success is False
    assert (places / "work" / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert into_itself.success is True, into_itself.error
    assert (places / "work" / "backup" / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert not (places / "work" / "backup" / "backup").exists()


async def test_read_does_not_follow_a_link_put_on_the_way_after_the_check(
    places: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.tool.file import read as read_module

    (places / "other_user" / "report.txt").write_text("their private line\n", encoding="utf-8")
    real_resolve = read_module.resolve_tool_path

    async def resolve_then_swap(*args, **kwargs):
        resolution = await real_resolve(*args, **kwargs)
        swap_for_link(places, "work", places / "other_user")
        return resolution

    monkeypatch.setattr(read_module, "resolve_tool_path", resolve_then_swap)

    result = await read_tool(_ctx(places), "work/report.txt")

    assert result.success is False
    assert "changed after it was checked" in (result.error or "")
    assert "their private" not in f"{result.output}{result.error}"


@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch"])
async def test_a_link_on_the_way_only_while_the_file_is_opened_shows_nothing_of_theirs(
    places: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """work/ is a link to their files just while the tool opens the file, and back before it looks again.

    A check before and a check after both pass; what was read in between
    must still be the file the path was checked for.
    """
    import builtins

    (places / "other_user" / "report.txt").write_text("mine\ntheir private line\n", encoding="utf-8")
    module = importlib.import_module(f"flocks.tool.file.{tool}")
    open_by_path, open_descriptor = builtins.open, os.open

    def swapped_while(opener, file, *args, **kwargs):
        if not str(file).endswith("report.txt"):
            return opener(file, *args, **kwargs)
        swap_for_link(places, "work", places / "other_user")
        try:
            return opener(file, *args, **kwargs)
        finally:
            (places / "work").unlink()
            (places / "work_parked").rename(places / "work")

    # By path (builtins.open) or through the directory's descriptor (os.open).
    monkeypatch.setattr(module, "open", lambda f, *a, **k: swapped_while(open_by_path, f, *a, **k), raising=False)
    monkeypatch.setattr(os, "open", lambda f, *a, **k: swapped_while(open_descriptor, f, *a, **k))
    requests: list = []

    result = await _calls(_ctx(places, requests))[tool]()

    shown = repr([(request.patterns, request.metadata) for request in requests]) + repr(result)
    assert "their private line" not in shown
    assert (places / "other_user" / "report.txt").read_text(encoding="utf-8") == "mine\ntheir private line\n"


async def test_a_cancelled_search_leaves_no_ripgrep_running() -> None:
    """Even with its output pipe full, the process is killed and reaped."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    waiting = asyncio.create_task(grep_module._communicate(proc))
    await asyncio.sleep(0.2)
    waiting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, timeout=10)
    assert proc.returncode is not None


def _html(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<html><body><h1>{text}</h1><p>{text}</p></body></html>\n", encoding="utf-8")
    return path


def _sandbox_ctx(root: Path, requests: list | None = None, access: str = "rw") -> ToolContext:
    async def approve(request) -> None:
        if requests is not None:
            requests.append(request)

    return ToolContext(
        session_id="ses_doc", message_id="msg_doc", permission_callback=approve,
        extra={"sandbox": {"workspace_dir": str(root), "workspace_access": access}},
    )


async def test_doc_parser_stays_inside_the_sandbox(places: Path) -> None:
    """Neither the document read nor the markdown written may be outside the sandbox workspace."""
    from flocks.tool.file.doc_parser import doc_parser

    sandbox = places / "work"
    _html(sandbox / "doc.html", "MINEDOCUMENT")
    _html(places / "other_user" / "secret.html", "THEIRSECRET")
    requests: list = []
    ctx = _sandbox_ctx(sandbox, requests)

    read_outside = await doc_parser(ctx, input_path=str(places / "other_user" / "secret.html"))
    write_outside = await doc_parser(
        ctx, input_path=str(sandbox / "doc.html"), output_path=str(places / "other_user" / "out.md"),
    )
    default_output = await doc_parser(ctx, input_path=str(sandbox / "doc.html"))

    assert read_outside.success is False and "escapes sandbox" in (read_outside.error or "")
    assert write_outside.success is False and "escapes sandbox" in (write_outside.error or "")
    assert sorted(entry.name for entry in (places / "other_user").iterdir()) == ["report.txt", "secret.html"]
    assert default_output.success is True, default_output.error
    written = Path(default_output.output["output_path"])
    assert written.parent.parent == sandbox / "outputs" and "MINEDOCUMENT" in written.read_text(encoding="utf-8")
    assert [request.metadata["tool"] for request in requests] == ["doc_parser"]


async def test_doc_parser_asks_before_writing_and_respects_a_read_only_sandbox(places: Path) -> None:
    from flocks.tool.file.doc_parser import doc_parser

    _html(places / "work" / "doc.html", "MINEDOCUMENT")
    requests: list = []

    async def deny(request) -> None:
        requests.append(request)
        raise PermissionError("Permission denied: edit")

    ctx = ToolContext(
        session_id="ses_doc", message_id="msg_doc", permission_callback=deny,
        extra={"workspace_dir": str(places)},
    )
    with pytest.raises(PermissionError):
        await doc_parser(ctx, input_path=str(places / "work" / "doc.html"), output_path=str(places / "work" / "doc.md"))
    read_only = await doc_parser(
        _sandbox_ctx(places / "work", access="ro"), input_path=str(places / "work" / "doc.html"),
        output_path=str(places / "work" / "doc.md"),
    )

    assert [(request.permission, request.metadata["tool"]) for request in requests] == [("edit", "doc_parser")]
    assert read_only.success is False and "read-only" in (read_only.error or "")
    assert not (places / "work" / "doc.md").exists()


async def test_doc_parser_checks_the_path_as_given_against_the_policy_binding(places: Path) -> None:
    from flocks.tool.file.doc_parser import doc_parser

    # Given in a form that is not canonical: the binding names it as given.
    given = str(places / "work") + "/./doc.html"
    _html(places / "work" / "doc.html", "MINEDOCUMENT")
    ctx = _ctx(places)
    ctx.extra["filesystem_path_binding"] = {
        "source_path": given,
        "source_canonical": {"realpath": "/elsewhere", "parent_realpath": "/", "final_target": "/elsewhere"},
    }

    result = await doc_parser(ctx, input_path=given, output_path=str(places / "out.md"))

    assert result.success is False and "changed after policy approval" in (result.error or "")
    assert not (places / "out.md").exists()


async def test_doc_parser_reads_a_relative_path_in_the_flocks_workspace(
    places: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.tool.file.doc_parser import doc_parser
    from flocks.workspace.manager import WorkspaceManager

    workspace = places / "flocks_workspace"
    _html(workspace / "uploads" / "doc.html", "MINEDOCUMENT")
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)

    result = await doc_parser(_ctx(places), input_path="uploads/doc.html", output_path="notes/doc.md")

    assert result.success is True, result.error
    assert result.output["input_path"] == str(workspace / "uploads" / "doc.html")
    assert "MINEDOCUMENT" in (workspace / "notes" / "doc.md").read_text(encoding="utf-8")


async def test_doc_parser_does_not_follow_a_link_put_on_the_way(places: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.tool.file import doc_parser as doc_parser_module

    _html(places / "work" / "doc.html", "MINEDOCUMENT")
    _html(places / "other_user" / "doc.html", "THEIRSECRET")
    real_resolve = doc_parser_module.resolve_tool_path

    async def resolve_then_swap(*args, **kwargs):
        resolution = await real_resolve(*args, **kwargs)
        swap_for_link(places, "work", places / "other_user")
        return resolution

    monkeypatch.setattr(doc_parser_module, "resolve_tool_path", resolve_then_swap)

    result = await doc_parser_module.doc_parser(
        _ctx(places), input_path=str(places / "work" / "doc.html"), output_path=str(places / "out" / "doc.md"),
    )

    assert result.success is False
    assert "THEIRSECRET" not in f"{result.output}{result.error}"
    assert not (places / "out" / "doc.md").exists()


async def _search(tool: str, root: Path):
    if tool == "grep":
        return await grep_module.grep_tool(_ctx(root), pattern="THEIR_SECRET|mine", path="work")
    return await glob_module.glob_tool(_ctx(root), pattern="*.txt", path="work")


@pytest.mark.parametrize("tool", ["grep", "glob"])
@pytest.mark.parametrize("ripgrep", [True, False], ids=["ripgrep", "python"])
async def test_search_does_not_follow_links_in_the_tree(
    places: Path, monkeypatch: pytest.MonkeyPatch, tool: str, ripgrep: bool,
) -> None:
    if ripgrep and shutil.which("rg") is None:
        pytest.skip("ripgrep is not installed")
    if not ripgrep:
        monkeypatch.setattr(grep_module if tool == "grep" else glob_module, "find_ripgrep", lambda: None)
    (places / "other_user" / "their-private-name.txt").write_text("THEIR_SECRET line\n", encoding="utf-8")
    (places / "work" / "theirs").symlink_to(places / "other_user", target_is_directory=True)
    (places / "work" / "their_file.txt").symlink_to(places / "other_user" / "their-private-name.txt")

    result = await _search(tool, places)

    assert result.success is True, result.error
    assert "report.txt" in result.output
    for theirs in ("THEIR_SECRET", "their-private-name", "their_file"):
        assert theirs not in result.output


@pytest.mark.parametrize("tool", ["grep", "glob"])
@pytest.mark.parametrize("descriptors", [True, False], ids=["openat", "by-path"])
async def test_search_results_reached_through_a_link_are_dropped(
    places: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory,
    tool: str, descriptors: bool,
) -> None:
    """ripgrep walks by path: one racing a swap of work/sub prints their names and lines.

    By path (Windows) each line shown is looked for again in the file as
    opened, read by ripgrep on its standard input.
    """
    real_rg = shutil.which("rg")
    if real_rg is None:
        pytest.skip("ripgrep is not installed")
    if not descriptors:
        monkeypatch.setattr(nofollow, "SUPPORTED", False)
    (places / "work" / "sub").mkdir()
    (places / "work" / "sub" / "report.txt").write_text("mine\n", encoding="utf-8")
    fake = tmp_path_factory.mktemp("bin") / "rg"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if any(arg.startswith('/dev/fd/') for arg in args) or '-' in args:\n"
        f"    os.execv({real_rg!r}, [{real_rg!r}, *args])\n"
        "if '--files' in args:\n"
        "    print('report.txt')\n"
        "    print('sub/their-private-name.txt')\n"
        "else:\n"
        "    print('./report.txt|1|mine')\n"
        "    print('./sub/report.txt|1|THEIR_SECRET line')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr(grep_module if tool == "grep" else glob_module, "find_ripgrep", lambda: str(fake))

    result = await _search(tool, places)

    assert result.success is True, result.error
    assert "report.txt" in result.output
    assert "THEIR_SECRET" not in result.output and "their-private-name" not in result.output
