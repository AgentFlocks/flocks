"""Session-level edit/write limits reach file tools and delegated subagents."""

from __future__ import annotations

import asyncio
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
async def test_copy_sees_the_files_a_directory_link_in_the_source_brings_along(
    tmp_path: Path, monkeypatch,
) -> None:
    # copytree follows directory links, so the linked files land in the copy.
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    ctx = _rule_ctx(tmp_path, monkeypatch, ("edit", "*", "allow"), ("edit", "copied/link/secret.txt", "deny"))

    with pytest.raises(PermissionError):
        await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")

    assert not (tmp_path / "copied").exists()


TWO_LINKS = {"shared": None, "src/l1": "shared", "src/l2": "shared"}
LINK_TO_SIBLING = {"src/data": None, "src/link": "src/data"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("layout", "denied"),
    [
        (TWO_LINKS, "copied/l1/secret.txt"),
        (TWO_LINKS, "copied/l2/secret.txt"),
        (LINK_TO_SIBLING, "copied/data/secret.txt"),
        (LINK_TO_SIBLING, "copied/link/secret.txt"),
    ],
    ids=["two-links-first", "two-links-second", "sibling-directory", "sibling-link"],
)
async def test_copy_sees_every_path_that_reaches_the_same_directory(
    tmp_path: Path, monkeypatch, layout: dict, denied: str,
) -> None:
    # copytree copies a directory again for every link that reaches it.
    for name, link_target in layout.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if link_target is None:
            path.mkdir()
            (path / "secret.txt").write_text("secret\n", encoding="utf-8")
        else:
            path.symlink_to(tmp_path / link_target, target_is_directory=True)
    ctx = _rule_ctx(tmp_path, monkeypatch, ("edit", "*", "allow"), ("edit", denied, "deny"))

    with pytest.raises(PermissionError):
        await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")

    assert not (tmp_path / "copied").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("link_target", ["src", "."], ids=["to-the-source", "above-the-source"])
async def test_copy_refuses_a_source_with_a_link_back_into_itself(tmp_path: Path, link_target: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "src" / "loop").symlink_to((tmp_path / link_target).resolve(), target_is_directory=True)
    requests: list = []

    async def _approve(request) -> None:
        requests.append(request)

    ctx = ToolContext(
        session_id="ses_loop", message_id="msg_loop",
        permission_callback=_approve,
        extra={"workspace_dir": str(tmp_path)},
    )

    result = await copy_tool(ctx=ctx, sourcePath="src", targetPath="copied")

    assert result.success is False
    assert "links back" in (result.error or "")
    assert requests == []
    assert not (tmp_path / "copied").exists()


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
