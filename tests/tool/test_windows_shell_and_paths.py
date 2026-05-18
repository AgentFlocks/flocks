"""
Tests for Windows shell selection and cross-drive path fallback behavior.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool import path_utils as path_utils_module
from flocks.tool.code import bash as bash_module
from flocks.tool.code import lsp_tool as lsp_module
from flocks.tool.file import apply_patch as apply_patch_module
from flocks.tool.file import edit as edit_module
from flocks.tool.file import write as write_module
from flocks.tool.registry import ToolContext, ToolResult


def _make_ctx(requests=None) -> ToolContext:
    """Create a minimal ToolContext that records permission requests."""

    async def _auto_approve(req):
        if requests is not None:
            requests.append(req)

    return ToolContext(
        session_id="test-session",
        message_id="msg-1",
        agent="test",
        call_id="call-1",
        permission_callback=_auto_approve,
    )


class _FakeProcess:
    stdout = None
    stderr = None
    returncode = 0


def test_get_shell_windows_prefers_powershell_variants(monkeypatch):
    """Windows shell detection should only consider PowerShell variants."""
    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setattr(
        bash_module,
        "shutil_which",
        lambda shell: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        if shell == "powershell"
        else None,
    )

    assert bash_module.get_shell() == "powershell"


def test_get_shell_windows_raises_when_powershell_missing(monkeypatch):
    """Windows shell detection should fail clearly without PowerShell."""
    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setattr(bash_module, "shutil_which", lambda _shell: None)

    with pytest.raises(FileNotFoundError, match="PowerShell executable not found"):
        bash_module.get_shell()


@pytest.mark.asyncio
async def test_execute_host_windows_reports_missing_powershell(monkeypatch):
    """Windows host execution should surface a clear startup error."""
    ctx = _make_ctx()

    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setattr(bash_module.Instance, "contains_path", lambda _path: True)
    monkeypatch.setattr(
        bash_module,
        "_get_windows_shell_command",
        lambda _command: (_ for _ in ()).throw(FileNotFoundError("PowerShell executable not found")),
    )

    result = await bash_module._execute_host(
        ctx=ctx,
        command="Write-Output 'hi'",
        cwd="/tmp",
        timeout_sec=1,
        timeout_ms=1000,
        description="missing powershell",
    )

    assert result.success is False
    assert "Failed to start command" in result.error
    assert "PowerShell executable not found" in result.error


@pytest.mark.asyncio
async def test_execute_host_windows_uses_explicit_powershell(monkeypatch):
    """Windows host execution should use an explicit shell command."""
    ctx = _make_ctx()
    exec_calls = []
    shell_mock = AsyncMock()

    async def fake_exec(*args, **kwargs):
        exec_calls.append((args, kwargs))
        return _FakeProcess()

    async def fake_stream_output(**kwargs):
        return ToolResult(success=True, output="ok", metadata={})

    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setattr(bash_module.Instance, "contains_path", lambda _path: True)
    monkeypatch.setattr(bash_module, "get_shell", lambda: "pwsh")
    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", shell_mock)
    monkeypatch.setattr(bash_module, "_stream_output", fake_stream_output)

    result = await bash_module._execute_host(
        ctx=ctx,
        command="Write-Output 'hi'",
        cwd="/tmp",
        timeout_sec=1,
        timeout_ms=1000,
        description="powershell test",
    )

    assert result.success is True
    assert exec_calls == [
        (
            ("pwsh", "-NoProfile", "-NonInteractive", "-Command", "Write-Output 'hi'"),
            {
                "stdout": bash_module.asyncio.subprocess.PIPE,
                "stderr": bash_module.asyncio.subprocess.PIPE,
                "cwd": "/tmp",
                "env": {
                    **bash_module.os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                },
            },
        )
    ]
    shell_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_host_unix_still_uses_subprocess_shell(monkeypatch):
    """Unix host execution should preserve the existing shell behavior."""
    ctx = _make_ctx()
    shell_calls = []
    exec_mock = AsyncMock()

    async def fake_shell(*args, **kwargs):
        shell_calls.append((args, kwargs))
        return _FakeProcess()

    async def fake_stream_output(**kwargs):
        return ToolResult(success=True, output="ok", metadata={})

    monkeypatch.setattr(bash_module.sys, "platform", "linux")
    monkeypatch.setattr(bash_module.Instance, "contains_path", lambda _path: True)
    monkeypatch.setattr(bash_module, "get_shell", lambda: "/bin/bash")
    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(bash_module.asyncio, "create_subprocess_exec", exec_mock)
    monkeypatch.setattr(bash_module, "_stream_output", fake_stream_output)

    result = await bash_module._execute_host(
        ctx=ctx,
        command="echo hi",
        cwd="/tmp",
        timeout_sec=1,
        timeout_ms=1000,
        description="unix test",
    )

    assert result.success is True
    assert shell_calls == [
        (
            ("echo hi",),
            {
                "stdout": bash_module.asyncio.subprocess.PIPE,
                "stderr": bash_module.asyncio.subprocess.PIPE,
                "cwd": "/tmp",
                "start_new_session": True,
            },
        )
    ]
    exec_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bash_tool_windows_falls_back_from_invalid_default_cwd(tmp_path, monkeypatch):
    """Windows should recover when the inherited default cwd is invalid."""
    ctx = _make_ctx()
    captured = {}

    async def fake_execute_host(**kwargs):
        captured["cwd"] = kwargs["cwd"]
        return ToolResult(success=True, output="ok", metadata={})

    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(bash_module.Instance, "get_directory", lambda: "/invalid/session/cwd")
    monkeypatch.setattr(bash_module, "_get_sandbox_config_from_ctx", lambda _ctx: None)
    monkeypatch.setattr(bash_module, "_execute_host", fake_execute_host)

    result = await bash_module.bash_tool(
        ctx=ctx,
        command="echo hi",
    )

    assert result.success is True
    assert captured["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_bash_tool_windows_does_not_fallback_explicit_workdir(tmp_path, monkeypatch):
    """Explicit workdir should keep existing error semantics on Windows."""
    ctx = _make_ctx()
    captured = {}

    async def fake_execute_host(**kwargs):
        captured["cwd"] = kwargs["cwd"]
        return ToolResult(success=True, output="ok", metadata={})

    monkeypatch.setattr(bash_module.sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(bash_module.Instance, "get_directory", lambda: "/invalid/session/cwd")
    monkeypatch.setattr(bash_module, "_get_sandbox_config_from_ctx", lambda _ctx: None)
    monkeypatch.setattr(bash_module, "_execute_host", fake_execute_host)

    result = await bash_module.bash_tool(
        ctx=ctx,
        command="echo hi",
        workdir="/explicit/invalid/workdir",
    )

    assert result.success is True
    assert captured["cwd"] == "/explicit/invalid/workdir"


@pytest.mark.asyncio
async def test_write_tool_uses_absolute_path_when_relpath_fails(tmp_path, monkeypatch):
    """Cross-drive relpath errors should fall back to the absolute path."""
    requests = []
    target = tmp_path / "cross-drive.txt"
    ctx = _make_ctx(requests)

    def fake_relpath(_path, _start):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(write_module.os.path, "relpath", fake_relpath)

    result = await write_module.write_tool(ctx=ctx, content="hello", filePath=str(target))

    assert result.success is True
    assert result.title == str(target)
    assert requests[-1].patterns == [str(target)]


def test_write_safe_relpath_keeps_relative_paths():
    """Normal relative path behavior should remain unchanged."""
    rel_path = write_module._safe_relpath("/tmp/project/file.txt", "/tmp/project")
    assert rel_path == "file.txt"


def test_edit_safe_relpath_falls_back_to_absolute(monkeypatch):
    """Edit helper should keep absolute paths on relpath failures."""

    def fake_relpath(_path, _start):
        raise ValueError("cross-drive")

    monkeypatch.setattr(edit_module.os.path, "relpath", fake_relpath)

    path = "C:\\Users\\Example\\file.txt"
    assert edit_module._safe_relpath(path, "D:\\workspace") == path


def test_apply_patch_safe_relpath_falls_back_to_absolute(monkeypatch):
    """Apply-patch helper should keep absolute paths on relpath failures."""

    def fake_relpath(_path, _start):
        raise ValueError("cross-drive")

    monkeypatch.setattr(apply_patch_module.os.path, "relpath", fake_relpath)

    path = "C:\\Users\\Example\\file.txt"
    assert apply_patch_module._safe_relpath(path, "D:\\workspace") == path


def test_resolve_workdir_expands_tilde(tmp_path, monkeypatch):
    """bash workdir resolution should expand ~/ paths consistently."""
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = bash_module._resolve_workdir("/tmp/project", "~/shell-home")

    assert resolved == str(tmp_path / "shell-home")


@pytest.mark.asyncio
async def test_lsp_tool_resolves_relative_path_from_instance(tmp_path, monkeypatch):
    """LSP should resolve relative file paths with the shared tool path rules."""
    requests = []
    ctx = _make_ctx(requests)
    target = tmp_path / "src" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hi')\n", encoding="utf-8")

    seen = {}

    class _FakeLSP:
        @staticmethod
        async def has_clients(filepath):
            seen["filepath"] = filepath
            return False

    monkeypatch.setattr(path_utils_module.Instance, "get_directory", lambda: str(tmp_path))
    monkeypatch.setattr(path_utils_module.Instance, "get_worktree", lambda: str(tmp_path))
    monkeypatch.setitem(sys.modules, "flocks.lsp", SimpleNamespace(LSP=_FakeLSP))

    result = await lsp_module.lsp_tool(
        ctx=ctx,
        operation="hover",
        filePath="src/sample.py",
        line=1,
        character=1,
    )

    assert not result.success
    assert seen["filepath"] == str(target)
    assert requests[-1].patterns == ["src/sample.py"]
