"""Regressions for the file/code tools found in the 2026-09-14 full-suite run (G3a)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult


@pytest.fixture(autouse=True)
def _registry():
    ToolRegistry.init()


def _ctx(workspace: Path, **extra) -> ToolContext:
    return ToolContext(session_id="s", message_id="m", extra={"workspace_dir": str(workspace), **extra})


async def test_read_rejects_negative_offset(tmp_path: Path):
    (tmp_path / "t.txt").write_text("\n".join(f"t{i}" for i in range(10)))
    result = await ToolRegistry.execute("read", _ctx(tmp_path), filePath="t.txt", offset=-3)

    assert result.success is False
    assert "offset" in result.error
    assert "-0002|" not in (result.output or "")


async def test_read_counts_lines_without_trailing_newline_artifact(tmp_path: Path):
    (tmp_path / "three.txt").write_text("a\nb\nc\n")
    (tmp_path / "empty.txt").write_text("")
    three = await ToolRegistry.execute("read", _ctx(tmp_path), filePath="three.txt")
    empty = await ToolRegistry.execute("read", _ctx(tmp_path), filePath="empty.txt")

    assert "(End of file — total 3 lines)" in three.output
    assert "00004|" not in three.output
    assert "(End of file — total 0 lines)" in empty.output


def test_truncate_output_ignores_trailing_newline():
    from flocks.tool.truncation import truncate_output

    exact = "\n".join(str(i) for i in range(1000)) + "\n"
    assert truncate_output(exact).truncated is False
    over = "\n".join(str(i) for i in range(5000)) + "\n"
    assert "...4000 lines truncated..." in truncate_output(over).content


async def test_apply_patch_rejects_context_mismatch(tmp_path: Path):
    (tmp_path / "u.txt").write_text("a\nb\nc\n")
    result = await ToolRegistry.execute(
        "apply_patch", _ctx(tmp_path),
        patchText="*** Begin Patch\n*** Update File: u.txt\n@@ -2,1 +2,1 @@\n-ZZZ\n+B\n*** End Patch",
    )

    assert result.success is False and "mismatch" in result.error
    assert (tmp_path / "u.txt").read_text() == "a\nb\nc\n"


async def test_apply_patch_rejects_hunk_beyond_end(tmp_path: Path):
    (tmp_path / "u.txt").write_text("a\nb\nc\n")
    result = await ToolRegistry.execute(
        "apply_patch", _ctx(tmp_path),
        patchText="*** Begin Patch\n*** Update File: u.txt\n@@ -100,1 +100,1 @@\n-x\n+y\n*** End Patch",
    )

    assert result.success is False and "line 100" in result.error


async def test_apply_patch_add_refuses_existing_file(tmp_path: Path):
    (tmp_path / "p.py").write_text("print(1)\n")
    result = await ToolRegistry.execute(
        "apply_patch", _ctx(tmp_path),
        patchText="*** Begin Patch\n*** Add File: p.py\nprint(2)\n*** End Patch",
    )

    assert result.success is False and "already exists" in result.error
    assert (tmp_path / "p.py").read_text() == "print(1)\n"


async def test_delete_blocked_in_read_only_sandbox(tmp_path: Path):
    (tmp_path / "x.txt").write_text("x")
    ctx = ToolContext(session_id="s", message_id="m", extra={"sandbox": {"workspace_dir": str(tmp_path), "workspace_access": "ro"}})
    result = await ToolRegistry.execute("delete", ctx, path="x.txt")

    assert result.success is False and "read-only" in result.error
    assert (tmp_path / "x.txt").exists()


async def test_grep_reports_invalid_regex_from_ripgrep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fake ``rg`` reproduces ripgrep's exit-2 + stderr contract for a bad regex."""
    import flocks.tool.code.grep as grep_module

    fake = tmp_path / "rg"
    fake.write_text("#!/bin/sh\necho 'rg: regex parse error:' 1>&2\necho 'error: unclosed group' 1>&2\nexit 2\n")
    fake.chmod(0o755)
    monkeypatch.setattr(grep_module, "find_ripgrep", lambda: str(fake))
    (tmp_path / "f.txt").write_text("x\n")
    result = await ToolRegistry.execute("grep", _ctx(tmp_path), pattern="(")

    assert result.success is False
    assert result.error.startswith("Search failed: Invalid regex pattern:")


def test_glob_fallback_includes_hidden_files(tmp_path: Path):
    from flocks.tool.file.glob import fallback_glob

    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.py").write_text("x")
    (tmp_path / "y.py").write_text("y")

    assert sorted(fallback_glob(str(tmp_path), "**/*.py")) == [".hidden/x.py", "y.py"]


async def test_string_param_receives_pretty_json_for_dict(tmp_path: Path):
    result = await ToolRegistry.execute("write", _ctx(tmp_path), filePath="d/c.json", content={"k": 1})

    assert result.success, result.error
    assert (tmp_path / "d" / "c.json").read_text() == '{\n  "k": 1\n}'


async def test_bash_bounds_output_memory_and_spills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flocks.tool import truncation
    from flocks.tool.code import bash as bash_module

    monkeypatch.setattr(truncation, "_OUTPUT_DIR", tmp_path / "spill")
    (tmp_path / "spill").mkdir()
    publishes: list[dict] = []
    ctx = ToolContext(session_id="s", message_id="m", extra={"workspace_dir": str(tmp_path)}, metadata_callback=lambda m: publishes.append(dict(m)))

    started = time.perf_counter()
    result: ToolResult = await ToolRegistry.execute("bash", ctx, command="yes | head -c 5000000")
    elapsed = time.perf_counter() - started

    assert result.success, result.error
    assert elapsed < 5
    assert result.truncated is True and result.metadata["truncated"] is True
    assert result.metadata["total_bytes"] == 5_000_000
    body = result.output.split("\n\n<bash_metadata>")[0]
    assert len(body.encode("utf-8")) == bash_module.MAX_OUTPUT_BYTES
    assert "output truncated at" in result.output and "full output saved to:" in result.output
    assert Path(result.metadata["output_path"]).stat().st_size == 5_000_000
    assert len(publishes) < 100, "metadata publishing must be throttled"


async def test_lsp_tool_rejects_zero_based_positions(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n")
    result = await ToolRegistry.execute("lsp", _ctx(tmp_path), operation="hover", filePath="a.py", line=0, character=0)

    assert result.success is False and "1-based" in result.error
