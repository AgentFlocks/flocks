import os
import tempfile

import pytest

from flocks.tool.file.apply_patch import apply_patch_tool
from flocks.tool.registry import ToolContext


@pytest.mark.asyncio
async def test_apply_patch_rejects_multi_file_patch() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_one = os.path.join(tmp_dir, "one.txt")
        file_two = os.path.join(tmp_dir, "two.txt")
        ctx = ToolContext(session_id="s", message_id="m")
        result = await apply_patch_tool(
            ctx=ctx,
            patchText=(
                "*** Begin Patch\n"
                f"*** Add File: {file_one}\n"
                "+one\n"
                f"*** Add File: {file_two}\n"
                "+two\n"
                "*** End Patch\n"
            ),
        )
        assert not result.success
        assert "exactly one file operation" in str(result.error or "")
        assert not os.path.exists(file_one)
        assert not os.path.exists(file_two)


@pytest.mark.asyncio
async def test_apply_patch_allows_single_file_patch() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        target = os.path.join(tmp_dir, "note.txt")
        ctx = ToolContext(session_id="s", message_id="m")
        result = await apply_patch_tool(
            ctx=ctx,
            patchText=(
                "*** Begin Patch\n"
                f"*** Add File: {target}\n"
                "hello\n"
                "*** End Patch\n"
            ),
        )
        assert result.success
        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "hello\n"
