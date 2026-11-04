"""会话循环的文件快照接线：轮次起点 snapshot part → 每步 patch part → 轮次结束 diff → revert 能回退文件。

之前 Snapshot 模块只被 revert/summary 引用，循环里从不 track，所以 GET /diff 永远是 []、
revert 找不到 patch part 也就回不了文件（SES-0168 / SES-0182）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from flocks.session.lifecycle.revert import RevertInput, SessionRevertManager
from flocks.session.lifecycle.summary import SessionSummary
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.session.session_loop import LoopCallbacks, LoopContext, SessionLoop


def _part_type(part) -> str:
    return part.get("type") if isinstance(part, dict) else getattr(part, "type", "")


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    tree = tmp_path / "proj"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "note.txt").write_text("v1\n")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    return tree


async def _make_turn(worktree: Path):
    session = await Session.create("snap_project", str(worktree), title="snapshot wiring")
    user = await Message.create(session.id, MessageRole.USER, "改一下 note.txt")
    ctx = LoopContext(session=session, provider_id="p", model_id="m", agent_name="rex")
    return session, user, ctx


@pytest.mark.asyncio
async def test_turn_snapshot_patch_and_diff_are_recorded(worktree: Path):
    session, user, ctx = await _make_turn(worktree)
    published: list = []

    async def publish(event, payload):
        published.append((event, payload))

    callbacks = LoopCallbacks(event_publish_callback=publish)

    snapshot = await SessionLoop._track_turn_snapshot(ctx, user)
    assert snapshot and len(snapshot) == 40
    user_parts = await Message.parts(user.id, session.id)
    assert [_part_type(p) for p in user_parts].count("snapshot") == 1
    # 再跑一步不会重复挂 snapshot part
    await SessionLoop._track_turn_snapshot(ctx, user)
    assert [_part_type(p) for p in await Message.parts(user.id, session.id)].count("snapshot") == 1

    # 这一步里工具改了文件
    (worktree / "docs" / "note.txt").write_text("v2\n")
    assistant = await Message.create(session.id, MessageRole.ASSISTANT, "done", parentID=user.id)
    await SessionLoop._record_step_patch(ctx, callbacks, user, snapshot)

    patches = [p for p in await Message.parts(assistant.id, session.id) if _part_type(p) == "patch"]
    assert len(patches) == 1
    assert getattr(patches[0], "hash", None) == snapshot
    assert [os.path.relpath(f, worktree) for f in patches[0].files] == ["docs/note.txt"]
    assert [e for e, payload in published if e == "message.part.updated"]
    assert published[0][1]["part"]["type"] == "patch"

    # 没改文件的步不记 patch part
    await SessionLoop._record_step_patch(ctx, callbacks, user, await SessionLoop._track_turn_snapshot(ctx, user))
    assert len([p for p in await Message.parts(assistant.id, session.id) if _part_type(p) == "patch"]) == 1

    await SessionLoop._record_turn_diffs(ctx, user)
    diffs = await SessionSummary.diff(session.id)
    assert diffs == [{"file": "docs/note.txt", "before": "v1\n", "after": "v2\n", "additions": 1, "deletions": 1}]
    assert await SessionSummary.diff(session.id, message_id=user.id) == diffs
    refreshed = await Session.get_by_id(session.id)
    assert refreshed.summary.additions == 1 and refreshed.summary.deletions == 1 and refreshed.summary.files == 1


@pytest.mark.asyncio
async def test_revert_restores_files_from_recorded_patch_parts(worktree: Path):
    session, user1, ctx = await _make_turn(worktree)
    callbacks = LoopCallbacks()
    snap1 = await SessionLoop._track_turn_snapshot(ctx, user1)
    (worktree / "docs" / "note.txt").write_text("v2\n")
    assistant1 = await Message.create(session.id, MessageRole.ASSISTANT, "done", parentID=user1.id)
    await SessionLoop._record_step_patch(ctx, callbacks, user1, snap1)

    user2 = await Message.create(session.id, MessageRole.USER, "再改成 v3")
    snap2 = await SessionLoop._track_turn_snapshot(ctx, user2)
    (worktree / "docs" / "note.txt").write_text("v3\n")
    await Message.create(session.id, MessageRole.ASSISTANT, "done", parentID=user2.id)
    await SessionLoop._record_step_patch(ctx, callbacks, user2, snap2)

    updated = await SessionRevertManager.revert(
        project_id=session.project_id,
        input=RevertInput(session_id=session.id, message_id=user2.id),
        worktree=str(worktree),
    )
    assert (worktree / "docs" / "note.txt").read_text() == "v2\n"
    assert updated.revert.message_id == user2.id
    assert updated.revert.snapshot and len(updated.revert.snapshot) == 40
    assert "-v3" in updated.revert.diff and "+v2" in updated.revert.diff
    assert assistant1.id  # 第一轮的改动保留
