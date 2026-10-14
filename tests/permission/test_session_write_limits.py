"""Session-level edit/write limits reach file tools and delegated subagents."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.permission.next import PermissionNext
from flocks.permission.rule import PermissionLevel, PermissionRule, PermissionScope
from flocks.session.runner import SessionRunner
from flocks.tool.file import copy as copy_module, delete as delete_module, move as move_module
from flocks.tool.file.apply_patch import apply_patch_tool
from flocks.tool.file.copy import copy_tool
from flocks.tool.file.delete import delete_tool
from flocks.tool.file.edit import edit_tool
from flocks.tool.file.mkdir import mkdir_tool
from flocks.tool.file.move import move_tool
from flocks.tool.file.write import write_tool
from flocks.tool.registry import ToolContext


def _ruleset(*rules: tuple[str, str]) -> list[PermissionRule]:
    return [
        PermissionRule(
            permission=permission,
            level=PermissionLevel(action),
            scope=PermissionScope.PATTERN,
            pattern="*",
        )
        for permission, action in rules
    ]


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ((("*", "allow"), ("edit", "deny"), ("write", "deny")), "deny"),
        ((("*", "allow"), ("write", "deny")), "deny"),
        ((("*", "allow"), ("edit", "ask")), "ask"),
        ((("edit", "deny"), ("write", "allow")), "allow"),
        ((("bash", "deny"),), None),
    ],
)
def test_file_tool_request_answers_to_edit_and_its_tool_name(rules, expected) -> None:
    assert PermissionNext.evaluate_request("edit", ["notes/a.md"], _ruleset(*rules), ["write"]) == expected


def _session(session_id: str, *, category: str = "user", parent_id=None, rules=()):
    return SimpleNamespace(
        id=session_id,
        agent="worker",
        category=category,
        parent_id=parent_id,
        permission=[
            SimpleNamespace(permission=permission, action=action, pattern="*")
            for permission, action in rules
        ],
    )


def _runner(monkeypatch: pytest.MonkeyPatch, session, sessions: dict, agent_rules=()) -> SessionRunner:
    monkeypatch.setattr(
        "flocks.session.runner.Session.get_by_id",
        AsyncMock(side_effect=lambda session_id: sessions.get(session_id)),
    )
    runner = object.__new__(SessionRunner)
    runner.session = session
    runner._step = 1
    runner.callbacks = SimpleNamespace(event_publish_callback=None, on_permission_request=None)
    runner._turn_permission_ruleset = runner._permission_ruleset_for_agent(
        SimpleNamespace(permission=_ruleset(*agent_rules)),
    )
    runner._turn_delegating_sessions = None
    return runner


def _write_request():
    return SimpleNamespace(
        permission="edit",
        patterns=["markers/deny.marker"],
        always=["*"],
        metadata={"tool": "write", "filepath": "/tmp/markers/deny.marker"},
        message_id="msg_1",
    )


PARENT_DENY = (("*", "allow"), ("edit", "deny"), ("write", "deny"))
PARENT_ASK = (("*", "allow"), ("edit", "ask"), ("write", "ask"))


@pytest.mark.asyncio
async def test_parent_session_write_deny_blocks_its_own_write(monkeypatch) -> None:
    parent = _session("ses_parent", rules=PARENT_DENY)
    runner = _runner(monkeypatch, parent, {})

    with pytest.raises(PermissionError, match="Permission denied: edit"):
        await runner._handle_permission(_write_request())


@pytest.mark.asyncio
async def test_delegated_child_inherits_parent_write_deny(monkeypatch) -> None:
    parent = _session("ses_parent", rules=PARENT_DENY)
    child = _session("ses_child", category="task", parent_id="ses_parent", rules=(("question", "deny"),))
    runner = _runner(monkeypatch, child, {"ses_parent": parent})

    with pytest.raises(PermissionError, match="Permission denied: edit"):
        await runner._handle_permission(_write_request())


@pytest.mark.asyncio
async def test_grandchild_inherits_through_the_delegation_chain(monkeypatch) -> None:
    parent = _session("ses_parent", rules=PARENT_DENY)
    child = _session("ses_child", category="task", parent_id="ses_parent", rules=(("question", "deny"),))
    grandchild = _session("ses_grandchild", category="task", parent_id="ses_child")
    runner = _runner(monkeypatch, grandchild, {"ses_parent": parent, "ses_child": child})

    with pytest.raises(PermissionError, match="Permission denied: edit"):
        await runner._handle_permission(_write_request())


@pytest.mark.parametrize(("reply", "allowed"), [("allow", True), ("deny", False)])
@pytest.mark.asyncio
async def test_delegated_child_asks_before_writing_when_parent_says_ask(
    monkeypatch, reply: str, allowed: bool,
) -> None:
    asked: list[dict] = []

    async def _ask(**kwargs):
        asked.append(kwargs)
        return reply

    monkeypatch.setattr("flocks.permission.next.PermissionNext.ask", _ask)
    parent = _session("ses_parent", rules=PARENT_ASK)
    child = _session("ses_child", category="task", parent_id="ses_parent")
    runner = _runner(monkeypatch, child, {"ses_parent": parent})

    if allowed:
        await runner._handle_permission(_write_request())
    else:
        with pytest.raises(PermissionError):
            await runner._handle_permission(_write_request())

    assert len(asked) == 1
    assert asked[0]["session_id"] == "ses_child"
    assert asked[0]["permission"] == "edit"
    assert asked[0]["tool"] == {"name": "write"}
    # The parent chat shows requests from the sessions it delegated to.
    assert asked[0]["metadata"]["delegationChain"] == ["ses_parent"]


@pytest.mark.asyncio
async def test_grandchild_request_names_its_whole_delegation_chain(monkeypatch) -> None:
    asked: list[dict] = []

    async def _ask(**kwargs):
        asked.append(kwargs)
        return "allow"

    monkeypatch.setattr("flocks.permission.next.PermissionNext.ask", _ask)
    root = _session("ses_root", rules=PARENT_ASK)
    user_branch = _session("ses_branch", category="user", parent_id="ses_root")
    child = _session("ses_child", category="task", parent_id="ses_branch")
    grandchild = _session("ses_grandchild", category="task", parent_id="ses_child")
    sessions = {"ses_root": root, "ses_branch": user_branch, "ses_child": child}
    runner = _runner(monkeypatch, grandchild, sessions, agent_rules=(("edit", "ask"),))

    await runner._handle_permission(_write_request())

    # The user branch is its own conversation: the chain stops there, and the
    # root session's ask rule is not inherited through it.
    assert asked[0]["metadata"]["delegationChain"] == ["ses_child", "ses_branch"]


@pytest.mark.asyncio
async def test_parent_allow_does_not_loosen_the_childs_own_limits(monkeypatch) -> None:
    parent = _session("ses_parent", rules=(("*", "allow"),))
    child = _session("ses_child", category="task", parent_id="ses_parent")
    runner = _runner(monkeypatch, child, {"ses_parent": parent}, agent_rules=(("edit", "deny"),))

    with pytest.raises(PermissionError):
        await runner._handle_permission(_write_request())


@pytest.mark.asyncio
async def test_session_a_user_started_from_a_parent_keeps_its_own_rules(monkeypatch) -> None:
    parent = _session("ses_parent", rules=PARENT_DENY)
    user_child = _session("ses_user_child", category="user", parent_id="ses_parent")
    runner = _runner(monkeypatch, user_child, {"ses_parent": parent})

    await runner._handle_permission(_write_request())


def _denying_ctx(requests: list) -> ToolContext:
    async def _deny(request) -> None:
        requests.append(request)
        raise PermissionError(f"Permission denied: {request.permission}")

    return ToolContext(session_id="ses_tool", message_id="msg_tool", permission_callback=_deny)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch", "delete", "move", "copy", "mkdir"])
async def test_file_tools_ask_for_edit_before_touching_disk(tmp_path: Path, tool: str) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("old\n", encoding="utf-8")
    target = tmp_path / "target.txt"
    requests: list = []
    ctx = _denying_ctx(requests)
    calls = {
        "write": lambda: write_tool(ctx=ctx, content="new", filePath=str(target)),
        "edit": lambda: edit_tool(
            ctx=ctx, filePath=str(existing), edits=[{"oldString": "old", "newString": "new"}],
        ),
        "apply_patch": lambda: apply_patch_tool(
            ctx=ctx, patchText=f"*** Begin Patch\n*** Add File: {target}\nnew\n*** End Patch\n",
        ),
        "delete": lambda: delete_tool(ctx=ctx, path=str(existing)),
        "move": lambda: move_tool(ctx=ctx, sourcePath=str(existing), targetPath=str(target)),
        "copy": lambda: copy_tool(ctx=ctx, sourcePath=str(existing), targetPath=str(target)),
        "mkdir": lambda: mkdir_tool(ctx=ctx, path=str(tmp_path / "new_dir")),
    }

    with pytest.raises(PermissionError):
        await calls[tool]()

    assert [request.permission for request in requests] == ["edit"]
    assert requests[0].metadata["tool"] == tool
    assert existing.read_text(encoding="utf-8") == "old\n"
    assert not target.exists()
    assert not (tmp_path / "new_dir").exists()


def _rule_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *rules: tuple[str, str, str]) -> ToolContext:
    """A tool context checked by the session runner against real path rules."""
    session = _session("ses_paths")
    session.permission = [
        SimpleNamespace(permission=permission, action=action, pattern=pattern)
        for permission, pattern, action in rules
    ]
    runner = _runner(monkeypatch, session, {})
    return ToolContext(
        session_id="ses_paths",
        message_id="msg_paths",
        permission_callback=runner._handle_permission,
        extra={"workspace_dir": str(tmp_path)},
    )


PROTECTED = (("edit", "*", "allow"), ("edit", "protected/*", "deny"))


@pytest.mark.asyncio
async def test_patch_moving_a_protected_file_out_is_denied(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "protected").mkdir()
    (tmp_path / "allowed").mkdir()
    source = tmp_path / "protected" / "a.txt"
    source.write_text("keep\n", encoding="utf-8")
    ctx = _rule_ctx(tmp_path, monkeypatch, *PROTECTED)

    with pytest.raises(PermissionError):
        await apply_patch_tool(ctx=ctx, patchText=(
            "*** Begin Patch\n*** Update File: protected/a.txt -> allowed/a.txt\n"
            "@@\n-keep\n+moved\n*** End Patch\n"
        ))

    assert source.read_text(encoding="utf-8") == "keep\n"
    assert not (tmp_path / "allowed" / "a.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["copy", "move"])
async def test_copy_or_move_into_a_protected_directory_is_denied(tmp_path: Path, monkeypatch, tool: str) -> None:
    (tmp_path / "protected").mkdir()
    source = tmp_path / "a.txt"
    source.write_text("new\n", encoding="utf-8")
    ctx = _rule_ctx(tmp_path, monkeypatch, *PROTECTED)
    call = copy_tool if tool == "copy" else move_tool

    with pytest.raises(PermissionError):
        await call(ctx=ctx, sourcePath="a.txt", targetPath="protected")

    assert source.exists()
    assert not (tmp_path / "protected" / "a.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete", "copy", "move"])
async def test_directory_operations_see_rules_on_their_descendants(tmp_path: Path, monkeypatch, tool: str) -> None:
    tree = tmp_path / "tree" / "sub"
    tree.mkdir(parents=True)
    (tree / "secret.txt").write_text("secret\n", encoding="utf-8")
    ctx = _rule_ctx(
        tmp_path, monkeypatch,
        ("edit", "*", "allow"), ("edit", "tree/sub/secret.txt", "deny"), ("edit", "copied/sub/secret.txt", "deny"),
    )

    with pytest.raises(PermissionError):
        if tool == "delete":
            await delete_tool(ctx=ctx, path="tree")
        elif tool == "copy":
            await copy_tool(ctx=ctx, sourcePath="tree", targetPath="copied")
        else:
            await move_tool(ctx=ctx, sourcePath="tree", targetPath="elsewhere")

    assert (tree / "secret.txt").exists()
    assert not (tmp_path / "copied").exists()
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["edit", "apply_patch"])
async def test_a_change_made_while_waiting_for_permission_is_not_overwritten(tmp_path: Path, tool: str) -> None:
    target = tmp_path / "config.txt"
    target.write_text("alpha=old\nbeta=old\n", encoding="utf-8")

    async def _approve_after_someone_else_edits(_request) -> None:
        target.write_text("alpha=old\nbeta=NEW\n", encoding="utf-8")

    ctx = ToolContext(
        session_id="ses_wait",
        message_id="msg_wait",
        permission_callback=_approve_after_someone_else_edits,
    )
    if tool == "edit":
        result = await edit_tool(
            ctx=ctx, filePath=str(target), edits=[{"oldString": "alpha=old", "newString": "alpha=new"}],
        )
    else:
        result = await apply_patch_tool(ctx=ctx, patchText=(
            f"*** Begin Patch\n*** Update File: {target}\n@@\n-alpha=old\n+alpha=new\n*** End Patch\n"
        ))

    assert result.success is False
    assert "changed while waiting for permission" in (result.error or "")
    assert target.read_text(encoding="utf-8") == "alpha=old\nbeta=NEW\n"


@pytest.mark.asyncio
async def test_copy_keeps_directory_links_in_the_source_as_links(tmp_path: Path, monkeypatch) -> None:
    # Nothing a link inside the source points at is read: the link is copied as a link.
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    ctx = _rule_ctx(tmp_path, monkeypatch, ("edit", "*", "allow"), ("edit", "copied/link/secret.txt", "deny"))

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")

    assert result.success is True, result.error
    assert (tmp_path / "copied" / "link").is_symlink()
    assert os.readlink(tmp_path / "copied" / "link") == str(tmp_path / "outside")


TWO_LINKS = {"shared": None, "src/l1": "shared", "src/l2": "shared"}
LINK_TO_SIBLING = {"src/data": None, "src/link": "src/data"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("layout", "denied", "written"),
    [
        (TWO_LINKS, "copied/l1/secret.txt", False),
        (TWO_LINKS, "copied/l2/secret.txt", False),
        (LINK_TO_SIBLING, "copied/data/secret.txt", True),
        (LINK_TO_SIBLING, "copied/link/secret.txt", False),
    ],
    ids=["two-links-first", "two-links-second", "sibling-directory", "sibling-link"],
)
async def test_copy_writes_only_what_the_source_really_holds(
    tmp_path: Path, monkeypatch, layout: dict, denied: str, written: bool,
) -> None:
    # A directory is copied; a path only a link reaches is not written, so its rule is not asked.
    for name, link_target in layout.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if link_target is None:
            path.mkdir()
            (path / "secret.txt").write_text("secret\n", encoding="utf-8")
        else:
            path.symlink_to(tmp_path / link_target, target_is_directory=True)
    ctx = _rule_ctx(tmp_path, monkeypatch, ("edit", "*", "allow"), ("edit", denied, "deny"))

    if written:
        with pytest.raises(PermissionError):
            await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")
        assert not (tmp_path / "copied").exists()
        return
    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")
    assert result.success is True, result.error
    for name, link_target in layout.items():
        if name.startswith("src/") and link_target is not None:
            assert (tmp_path / "copied" / name.removeprefix("src/")).is_symlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("link_target", ["src", "."], ids=["to-the-source", "above-the-source"])
async def test_copy_copies_a_link_back_into_the_source_as_a_link(tmp_path: Path, link_target: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "src" / "loop").symlink_to((tmp_path / link_target).resolve(), target_is_directory=True)

    async def _approve(_request) -> None:
        return None

    ctx = ToolContext(
        session_id="ses_loop", message_id="msg_loop",
        permission_callback=_approve,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")

    assert result.success is True, result.error
    assert (tmp_path / "copied" / "loop").is_symlink()
    assert (tmp_path / "copied" / "a.txt").read_text(encoding="utf-8") == "a\n"


def _names_under(root: Path) -> set[str]:
    """Every name under ``root`` without going through links."""
    found: set[str] = set()
    for _directory, directories, files in os.walk(root, followlinks=False):
        found.update(directories)
        found.update(files)
    return found


@pytest.mark.asyncio
async def test_an_inner_link_retargeted_while_listing_brings_nothing_of_theirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link inside the source is pointed at another user's directory as the tree is listed."""
    from flocks.tool import path_utils

    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "ok.txt").write_text("ok\n", encoding="utf-8")
    other = tmp_path / "other_user"
    other.mkdir()
    (other / "their-private-name.txt").write_text("theirs\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "link").symlink_to(safe, target_is_directory=True)
    list_tree = path_utils.tree_entries

    def retarget_then_list(*args, **kwargs):
        if os.readlink(src / "link") != str(other):
            (src / "link").unlink()
            (src / "link").symlink_to(other, target_is_directory=True)
        return list_tree(*args, **kwargs)

    monkeypatch.setattr(copy_module, "tree_entries", retarget_then_list)
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_inner_listing", message_id="msg_inner_listing",
        permission_callback=_approve, extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="dest")

    assert result.success is True, result.error
    assert "their-private-name" not in repr([(r.patterns, r.metadata) for r in requests])
    assert (tmp_path / "dest" / "link").is_symlink()
    assert "their-private-name.txt" not in _names_under(tmp_path / "dest")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["copy", "delete", "move"])
async def test_a_directory_replaced_by_a_link_as_it_is_listed_is_not_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """``src/sub`` is a real directory when queued and a link to their files when read."""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "mine.txt").write_text("mine\n", encoding="utf-8")
    other = tmp_path / "other_user"
    other.mkdir()
    (other / "their-private-name.txt").write_text("theirs\n", encoding="utf-8")
    list_directory = os.listdir

    def list_after_the_swap(path):
        if os.fspath(path) == str(src / "sub") and not (src / "sub").is_symlink():
            (src / "sub").rename(tmp_path / "parked")
            (src / "sub").symlink_to(other, target_is_directory=True)
        return list_directory(path)

    monkeypatch.setattr(os, "listdir", list_after_the_swap)
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_sub_swap", message_id="msg_sub_swap",
        permission_callback=_approve, extra={"workspace_dir": str(tmp_path)},
    )
    calls = {
        "copy": lambda: copy_tool(ctx=ctx, sourcePath="src", targetPath="copied"),
        "delete": lambda: delete_tool(ctx=ctx, path="src"),
        "move": lambda: move_tool(ctx=ctx, sourcePath="src", targetPath="moved"),
    }

    result = await calls[tool]()

    assert result.success is False
    assert requests == []
    assert (other / "their-private-name.txt").read_text(encoding="utf-8") == "theirs\n"


async def _approve_all(_request) -> None:
    return None


def _copy_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="ses_copy_again", message_id="msg_copy_again",
        permission_callback=_approve_all, extra={"workspace_dir": str(tmp_path)},
    )


@pytest.mark.asyncio
async def test_copying_a_tree_with_links_again_updates_the_copy(tmp_path: Path) -> None:
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("first\n", encoding="utf-8")
    (src / "link").symlink_to(tmp_path / "one", target_is_directory=True)
    assert (await copy_tool(ctx=_copy_ctx(tmp_path), sourcePath="src", targetPath="copied")).success

    (src / "a.txt").write_text("second\n", encoding="utf-8")
    (src / "link").unlink()
    (src / "link").symlink_to(tmp_path / "two", target_is_directory=True)
    result = await copy_tool(ctx=_copy_ctx(tmp_path), sourcePath="src", targetPath="copied")

    assert result.success is True, result.error
    assert (tmp_path / "copied" / "a.txt").read_text(encoding="utf-8") == "second\n"
    assert os.readlink(tmp_path / "copied" / "link") == str(tmp_path / "two")


@pytest.mark.asyncio
async def test_a_link_takes_the_place_of_a_file_but_not_of_a_directory(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("new\n", encoding="utf-8")
    (src / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    copied = tmp_path / "copied"
    copied.mkdir()
    (copied / "link").write_text("a file\n", encoding="utf-8")

    assert (await copy_tool(ctx=_copy_ctx(tmp_path), sourcePath="src", targetPath="copied")).success
    assert (copied / "link").is_symlink()

    other = tmp_path / "copied_dir"
    (other / "link").mkdir(parents=True)
    (other / "a.txt").write_text("old\n", encoding="utf-8")
    result = await copy_tool(ctx=_copy_ctx(tmp_path), sourcePath="src", targetPath="copied_dir")

    # Refused before anything was written: no half-merged copy.
    assert result.success is False
    assert "is a directory where the source has a link" in (result.error or "")
    assert (other / "link").is_dir() and not (other / "link").is_symlink()
    assert (other / "a.txt").read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["edit", "apply_patch"])
async def test_an_error_after_a_swap_while_reading_quotes_nothing_of_theirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """The path is swapped just as the file is opened; the edit then fails on their content."""
    import builtins
    import importlib

    work = tmp_path / "work"
    work.mkdir()
    other = tmp_path / "other_user"
    other.mkdir()
    (work / "report.txt").write_text("mine\n", encoding="utf-8")
    (other / "report.txt").write_text("their private line\n", encoding="utf-8")
    module = importlib.import_module(f"flocks.tool.file.{tool}")

    def open_after_the_swap(file, *args, **kwargs):
        if str(file).endswith("report.txt") and not work.is_symlink():
            work.rename(tmp_path / "parked")
            work.symlink_to(other, target_is_directory=True)
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr(module, "open", open_after_the_swap, raising=False)
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_read_swap", message_id="msg_read_swap",
        permission_callback=_approve, extra={"workspace_dir": str(tmp_path)},
    )
    calls = {
        "edit": lambda: edit_tool(
            ctx=ctx, filePath="work/report.txt", edits=[{"oldString": "mine", "newString": "changed"}],
        ),
        "apply_patch": lambda: apply_patch_tool(ctx=ctx, patchText=(
            "*** Begin Patch\n*** Update File: work/report.txt\n@@\n-mine\n+changed\n*** End Patch\n"
        )),
    }

    result = await calls[tool]()

    assert result.success is False
    assert "changed after it was checked" in (result.error or "")
    assert "their private" not in (result.error or "")
    assert requests == []
    assert (other / "report.txt").read_text(encoding="utf-8") == "their private line\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["copy", "move"])
async def test_landing_through_a_link_in_the_target_is_judged_where_it_writes(
    tmp_path: Path, monkeypatch, tool: str,
) -> None:
    (tmp_path / "protected").mkdir()
    (tmp_path / "dest").mkdir()
    (tmp_path / "dest" / "sub").symlink_to(tmp_path / "protected", target_is_directory=True)
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "sub" / "x.txt").write_text("new\n", encoding="utf-8")
    ctx = _rule_ctx(tmp_path, monkeypatch, *PROTECTED)

    with pytest.raises(PermissionError):
        if tool == "copy":
            # copytree merges src into dest and writes dest/sub/x.txt, i.e. protected/x.txt.
            await copy_tool(ctx=ctx, sourcePath="src", targetPath="dest")
        else:
            # dest/sub is an existing directory: the file lands in protected/.
            await move_tool(ctx=ctx, sourcePath="src/sub/x.txt", targetPath="dest/sub")

    assert not (tmp_path / "protected" / "x.txt").exists()
    assert (tmp_path / "src" / "sub" / "x.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete", "copy", "move"])
async def test_files_added_while_waiting_for_permission_are_not_covered(tmp_path: Path, tool: str) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a\n", encoding="utf-8")

    async def _approve_after_a_file_appears(_request) -> None:
        (tree / "late.txt").write_text("late\n", encoding="utf-8")

    ctx = ToolContext(
        session_id="ses_late", message_id="msg_late",
        permission_callback=_approve_after_a_file_appears,
        extra={"workspace_dir": str(tmp_path)},
    )
    if tool == "delete":
        result = await delete_tool(ctx=ctx, path="tree")
    elif tool == "copy":
        result = await copy_tool(ctx=ctx, sourcePath="tree", targetPath="copied")
    else:
        result = await move_tool(ctx=ctx, sourcePath="tree", targetPath="moved")

    assert result.success is False
    assert "changed while waiting for permission" in (result.error or "")
    assert (tree / "a.txt").exists() and (tree / "late.txt").exists()
    assert not (tmp_path / "copied").exists() and not (tmp_path / "moved").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete", "copy", "move"])
async def test_nothing_else_runs_between_the_last_check_and_the_operation(
    tmp_path: Path, monkeypatch, tool: str,
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a\n", encoding="utf-8")
    module = {"delete": delete_module, "copy": copy_module, "move": move_module}[tool]
    real_tree_entries = module.tree_entries
    loop = asyncio.get_running_loop()
    added = threading.Event()
    listings: list[str] = []

    def add_late_file() -> None:
        tree.mkdir(exist_ok=True)
        (tree / "late.txt").write_text("late\n", encoding="utf-8")
        added.set()

    def list_then_let_other_tasks_run(root, **kwargs):
        entries = real_tree_entries(root, **kwargs)
        listings.append(root)
        if len(listings) == 2:
            # Another task on the event loop adds a file right after the last
            # check took its listing; it must not get in before the operation.
            loop.call_soon_threadsafe(add_late_file)
            added.wait(0.5)
        return entries

    monkeypatch.setattr(module, "tree_entries", list_then_let_other_tasks_run)
    ctx = _rule_ctx(
        tmp_path, monkeypatch,
        ("edit", "*", "allow"),
        ("edit", "tree/late.txt", "deny"), ("edit", "copied/late.txt", "deny"), ("edit", "moved/late.txt", "deny"),
    )

    if tool == "delete":
        await delete_tool(ctx=ctx, path="tree")
    elif tool == "copy":
        await copy_tool(ctx=ctx, sourcePath="tree", targetPath="copied")
    else:
        await move_tool(ctx=ctx, sourcePath="tree", targetPath="moved")
    await asyncio.sleep(0)

    assert len(listings) == 2
    assert (tree / "late.txt").exists()
    assert not (tmp_path / "copied" / "late.txt").exists()
    assert not (tmp_path / "moved" / "late.txt").exists()


@pytest.mark.asyncio
async def test_target_becoming_a_directory_while_waiting_is_not_covered(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    async def _approve_after_target_appears(_request) -> None:
        (tmp_path / "dest").mkdir()

    ctx = ToolContext(
        session_id="ses_target", message_id="msg_target",
        permission_callback=_approve_after_target_appears,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="a.txt", targetPath="dest")

    assert result.success is False
    assert not (tmp_path / "dest" / "a.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch", "mkdir", "delete", "copy", "move"])
async def test_a_path_redirected_while_waiting_for_permission_is_refused(tmp_path: Path, tool: str) -> None:
    """A directory on the way swapped for a link during the wait (arch_v2).

    The paths were checked against the workspace and policy bindings before
    the wait; afterwards they would lead into another user's files.  The
    other user's file has the same text, so comparing contents alone passes.
    """
    work = tmp_path / "work"
    work.mkdir()
    other = tmp_path / "other_user"
    other.mkdir()
    (work / "report.txt").write_text("mine\n", encoding="utf-8")
    (other / "report.txt").write_text("mine\n", encoding="utf-8")

    async def _approve_after_the_redirect(_request) -> None:
        work.rename(tmp_path / "parked")
        work.symlink_to(other, target_is_directory=True)

    ctx = ToolContext(
        session_id="ses_redirect", message_id="msg_redirect",
        permission_callback=_approve_after_the_redirect,
        extra={"workspace_dir": str(tmp_path)},
    )
    calls = {
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

    result = await calls[tool]()

    assert result.success is False
    assert "changed while waiting for permission" in (result.error or "")
    assert sorted(entry.name for entry in other.iterdir()) == ["report.txt"]
    assert (other / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert (tmp_path / "parked" / "report.txt").read_text(encoding="utf-8") == "mine\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch", "mkdir", "delete", "copy", "move"])
async def test_a_path_redirected_before_the_tool_looks_again_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """The swap lands right after the path was checked, before the snapshot is taken.

    A snapshot of where the path leads at that point would take the link as
    the baseline; the path as it was checked is the baseline instead.
    """
    import importlib

    from flocks.tool import path_utils

    work = tmp_path / "work"
    work.mkdir()
    other = tmp_path / "other_user"
    other.mkdir()
    (work / "report.txt").write_text("mine\n", encoding="utf-8")
    (other / "report.txt").write_text("mine\n", encoding="utf-8")
    resolve = path_utils.resolve_tool_path

    async def resolve_then_redirect(*args, **kwargs):
        resolution = await resolve(*args, **kwargs)
        if not work.is_symlink():
            # Another task, scheduled right after the check.
            work.rename(tmp_path / "parked")
            work.symlink_to(other, target_is_directory=True)
        return resolution

    for name in ("write", "edit", "apply_patch", "mkdir", "delete", "copy", "move"):
        monkeypatch.setattr(importlib.import_module(f"flocks.tool.file.{name}"), "resolve_tool_path", resolve_then_redirect)

    # Their file matches the edit too, so only the check keeps it out of a diff.
    (other / "report.txt").write_text("mine\ntheir secret\n", encoding="utf-8")
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_redirect", message_id="msg_redirect",
        permission_callback=_approve, extra={"workspace_dir": str(tmp_path)},
    )
    calls = {
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

    result = await calls[tool]()

    assert result.success is False
    # Nothing read or listed through the link reaches an approval request.
    assert requests == []
    assert sorted(entry.name for entry in other.iterdir()) == ["report.txt"]
    assert (other / "report.txt").read_text(encoding="utf-8") == "mine\ntheir secret\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["copy", "delete", "move"])
async def test_names_listed_through_a_path_redirected_meanwhile_are_not_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """The swap lands while the tree is being listed for the approval request."""
    from flocks.tool import path_utils

    work = tmp_path / "work"
    (work / "tree").mkdir(parents=True)
    (work / "tree" / "mine.txt").write_text("mine\n", encoding="utf-8")
    other = tmp_path / "other_user"
    (other / "tree").mkdir(parents=True)
    (other / "tree" / "their-private-name.txt").write_text("theirs\n", encoding="utf-8")
    module = {"copy": copy_module, "delete": delete_module, "move": move_module}[tool]
    list_tree = path_utils.tree_entries

    def list_after_the_swap(*args, **kwargs):
        if not work.is_symlink():
            work.rename(tmp_path / "parked")
            work.symlink_to(other, target_is_directory=True)
        return list_tree(*args, **kwargs)

    monkeypatch.setattr(module, "tree_entries", list_after_the_swap)
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_listing", message_id="msg_listing",
        permission_callback=_approve, extra={"workspace_dir": str(tmp_path)},
    )
    calls = {
        "copy": lambda: copy_tool(ctx=ctx, sourcePath="work/tree", targetPath="copied"),
        "delete": lambda: delete_tool(ctx=ctx, path="work/tree"),
        "move": lambda: move_tool(ctx=ctx, sourcePath="work/tree", targetPath="moved"),
    }

    result = await calls[tool]()

    assert result.success is False
    assert requests == []
    assert (other / "tree" / "their-private-name.txt").read_text(encoding="utf-8") == "theirs\n"


@pytest.mark.asyncio
async def test_a_hard_link_put_in_place_while_waiting_is_refused(tmp_path: Path) -> None:
    """Same path, but now another user's file (a hard link): not the approved file."""
    (tmp_path / "report.txt").write_text("mine\n", encoding="utf-8")
    other = tmp_path / "other_user.txt"
    other.write_text("mine\n", encoding="utf-8")

    async def _approve_after_the_swap(_request) -> None:
        (tmp_path / "report.txt").unlink()
        (tmp_path / "report.txt").hardlink_to(other)

    ctx = ToolContext(
        session_id="ses_hardlink", message_id="msg_hardlink",
        permission_callback=_approve_after_the_swap,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await edit_tool(
        ctx=ctx, filePath="report.txt", edits=[{"oldString": "mine", "newString": "changed"}],
    )

    assert result.success is False
    assert other.read_text(encoding="utf-8") == "mine\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch", "mkdir", "delete", "copy", "move"])
async def test_parent_path_rules_hold_for_a_child_in_another_directory(
    tmp_path: Path, monkeypatch, tool: str,
) -> None:
    """A delegated child with its own session directory (arch_v2).

    The child names paths relative to its directory ("../parent/protected/a.txt");
    the parent's rules are relative to the parent's ("protected/*").
    """
    parent_dir = tmp_path / "parent"
    child_dir = tmp_path / "child"
    (parent_dir / "protected").mkdir(parents=True)
    child_dir.mkdir()
    protected_file = parent_dir / "protected" / "a.txt"
    protected_file.write_text("old\n", encoding="utf-8")
    (child_dir / "x.txt").write_text("x\n", encoding="utf-8")
    parent = _session("ses_parent")
    parent.directory = str(parent_dir)
    parent.permission = [
        SimpleNamespace(permission=permission, action=action, pattern=pattern)
        for permission, pattern, action in PROTECTED
    ]
    child = _session("ses_child", category="task", parent_id="ses_parent", rules=(("edit", "allow"),))
    child.directory = str(child_dir)
    runner = _runner(monkeypatch, child, {"ses_parent": parent})
    ctx = ToolContext(
        session_id="ses_child", message_id="msg_child",
        permission_callback=runner._handle_permission,
        extra={"workspace_dir": str(child_dir)},
    )
    target = str(protected_file)
    calls = {
        "write": lambda: write_tool(ctx=ctx, content="new\n", filePath=target),
        "edit": lambda: edit_tool(ctx=ctx, filePath=target, edits=[{"oldString": "old", "newString": "new"}]),
        "apply_patch": lambda: apply_patch_tool(ctx=ctx, patchText=(
            f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n*** End Patch\n"
        )),
        "mkdir": lambda: mkdir_tool(ctx=ctx, path=str(parent_dir / "protected" / "sub")),
        "delete": lambda: delete_tool(ctx=ctx, path=target),
        "copy": lambda: copy_tool(
            ctx=ctx, sourcePath="x.txt", targetPath=str(parent_dir / "protected" / "x.txt"),
        ),
        "move": lambda: move_tool(ctx=ctx, sourcePath=target, targetPath="moved.txt"),
    }

    with pytest.raises(PermissionError, match="Permission denied: edit"):
        await calls[tool]()

    assert protected_file.read_text(encoding="utf-8") == "old\n"
    assert sorted(entry.name for entry in (parent_dir / "protected").iterdir()) == ["a.txt"]
    assert not (child_dir / "moved.txt").exists()


@pytest.mark.asyncio
async def test_child_in_another_directory_still_reaches_what_the_parent_allows(
    tmp_path: Path, monkeypatch,
) -> None:
    parent_dir = tmp_path / "parent"
    child_dir = tmp_path / "child"
    (parent_dir / "protected").mkdir(parents=True)
    child_dir.mkdir()
    parent = _session("ses_parent")
    parent.directory = str(parent_dir)
    parent.permission = [
        SimpleNamespace(permission=permission, action=action, pattern=pattern)
        for permission, pattern, action in PROTECTED
    ]
    child = _session("ses_child", category="task", parent_id="ses_parent", rules=(("edit", "allow"),))
    child.directory = str(child_dir)
    runner = _runner(monkeypatch, child, {"ses_parent": parent})
    ctx = ToolContext(
        session_id="ses_child", message_id="msg_child",
        permission_callback=runner._handle_permission,
        extra={"workspace_dir": str(child_dir)},
    )

    result = await write_tool(ctx=ctx, content="ok\n", filePath=str(parent_dir / "notes.txt"))
    own = await write_tool(ctx=ctx, content="ok\n", filePath="drafts/draft.txt")

    assert result.success is True and own.success is True
    assert (parent_dir / "notes.txt").read_text(encoding="utf-8") == "ok\n"
    assert (child_dir / "drafts" / "draft.txt").read_text(encoding="utf-8") == "ok\n"


@pytest.mark.asyncio
async def test_copy_refuses_a_link_inside_the_source_retargeted_while_waiting(tmp_path: Path) -> None:
    """The link the request listed was replaced by one pointing elsewhere (arch_v2)."""
    safe = tmp_path / "safe"
    other = tmp_path / "other_user"
    for directory in (safe, other):
        directory.mkdir()
        (directory / "file.txt").write_text(f"{directory.name}\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "link").symlink_to(safe, target_is_directory=True)

    async def _approve_after_retargeting(_request) -> None:
        (src / "link").unlink()
        (src / "link").symlink_to(other, target_is_directory=True)

    ctx = ToolContext(
        session_id="ses_inner", message_id="msg_inner",
        permission_callback=_approve_after_retargeting,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="dest")

    assert result.success is False
    assert "changed while waiting for permission" in (result.error or "")
    assert not (tmp_path / "dest").exists()


@pytest.mark.asyncio
async def test_copy_refuses_a_hard_link_put_under_the_target_while_waiting(tmp_path: Path) -> None:
    """copytree overwrites existing files in the target; one became another user's file."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "report.txt").write_text("new\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "report.txt").write_text("old\n", encoding="utf-8")
    other = tmp_path / "other_user.txt"
    other.write_text("theirs\n", encoding="utf-8")

    async def _approve_after_the_swap(_request) -> None:
        (dest / "report.txt").unlink()
        (dest / "report.txt").hardlink_to(other)

    ctx = ToolContext(
        session_id="ses_dest", message_id="msg_dest",
        permission_callback=_approve_after_the_swap,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="dest")

    assert result.success is False
    assert other.read_text(encoding="utf-8") == "theirs\n"


@pytest.mark.asyncio
async def test_parent_rules_judge_an_absolute_pattern_where_it_points(tmp_path: Path, monkeypatch) -> None:
    """A tool that could not make a path relative (another drive on Windows) sends it absolute."""
    parent_dir = tmp_path / "parent"
    child_dir = tmp_path / "child"
    (parent_dir / "protected").mkdir(parents=True)
    child_dir.mkdir()
    parent = _session("ses_parent")
    parent.directory = str(parent_dir)
    parent.permission = [
        SimpleNamespace(permission=permission, action=action, pattern=pattern)
        for permission, pattern, action in PROTECTED
    ]
    child = _session("ses_child", category="task", parent_id="ses_parent", rules=(("edit", "allow"),))
    child.directory = str(child_dir)
    runner = _runner(monkeypatch, child, {"ses_parent": parent})
    request = SimpleNamespace(
        permission="edit",
        patterns=[str(parent_dir / "protected" / "a.txt")],
        always=["*"],
        metadata={"tool": "write", "filepath": str(parent_dir / "protected" / "a.txt"),
                  "pattern_base": str(child_dir)},
        message_id="msg_abs",
    )

    with pytest.raises(PermissionError, match="Permission denied: edit"):
        await runner._handle_permission(request)
