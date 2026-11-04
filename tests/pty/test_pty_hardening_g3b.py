"""G3b PTY 加固回归测试（TOL-0963/0964/0999 与 Pro 门控兼容）。

  - 危险环境变量(LD_PRELOAD/PATH)被拒
  - 静默终端的阻塞 read 不冻结事件循环
  - 生命周期负载带 tool_call_id 与执行主体(subject)，供 Pro 审计/策略门控使用
"""
from __future__ import annotations

import asyncio
import time

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.pty.pty import CreateInput, Pty


@pytest.fixture(autouse=True)
def _reset_pipeline():
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.parametrize("env", [{"LD_PRELOAD": "/tmp/evil.so"}, {"PATH": "/tmp"}, {"DYLD_INSERT_LIBRARIES": "/x"}])
def test_dangerous_env_rejected(env):
    with pytest.raises(ValueError, match="Security Violation"):
        Pty._prepare_environment(env)


def test_safe_env_merged():
    env = Pty._prepare_environment({"G3B_OK": "1"})
    assert env["G3B_OK"] == "1" and env["TERM"] == "xterm-256color"


@pytest.mark.asyncio
async def test_silent_pty_does_not_block_event_loop():
    info = await Pty.create(CreateInput(command="/bin/sh", args=["-c", "sleep 5"]), use_action_lifecycle=False)
    try:
        # 若 read 阻塞在事件循环内，这个 sleep 会被拖到子进程退出(5s)之后
        t0 = time.perf_counter()
        for _ in range(5):
            await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"event loop blocked for {elapsed:.2f}s by a silent PTY"
        assert Pty.get(info.id).status.value == "running"
    finally:
        await Pty.remove(info.id)


@pytest.mark.asyncio
async def test_lifecycle_payload_carries_call_id_and_subject(monkeypatch):
    from flocks.auth.context import AuthUser, set_current_auth_user, reset_current_auth_user

    seen: list[dict] = []

    class _Recorder(HookBase):
        async def tool_before(self, ctx):
            seen.append(dict(ctx.input))
            ctx.output["decision"] = {"action": "deny", "reason": "stop-here"}

    HookPipeline.register("g3b-recorder", _Recorder())
    token = set_current_auth_user(AuthUser(id="usr_g3b", username="g3b", role="admin", status="active", must_reset_password=False))
    try:
        with pytest.raises(PermissionError):
            await Pty.create(CreateInput(command="/bin/sh"))
    finally:
        reset_current_auth_user(token)
    assert seen, "tool_before not invoked"
    te = seen[0]["tool_execution"]
    assert te["execution"]["tool_call_id"], "tool_call_id missing from PTY payload"
    assert te["actor"]["subject"]["id"] == "usr_g3b"
    assert te["actor"]["subject"]["type"] in ("human", "service")


@pytest.mark.asyncio
async def test_ptyprocess_bytes_roundtrip_and_buffering():
    """PtyProcess.read 返回 bytes / write 需要 bytes：无订阅者时输出进入 buffer(str)，输入可写入。"""
    info = await Pty.create(CreateInput(command="/bin/sh", args=["-c", "cat"]), use_action_lifecycle=False)
    try:
        await asyncio.sleep(0.3)
        await Pty.write(info.id, "roundtrip_ok\n", use_action_lifecycle=False)
        for _ in range(20):
            await asyncio.sleep(0.1)
            if "roundtrip_ok" in Pty._sessions[info.id].buffer:
                break
        session = Pty._sessions[info.id]
        assert session.info.status.value == "running", "read loop died (bytes/str mismatch)"
        assert "roundtrip_ok" in session.buffer and isinstance(session.buffer, str)
    finally:
        await Pty.remove(info.id)


@pytest.mark.asyncio
async def test_missing_cwd_rejected_before_spawn():
    """TOL-0965: cwd 不存在 → ValueError（路由转 400），不产生已退出的僵尸会话。"""
    before = set(Pty._sessions)
    with pytest.raises(ValueError, match="Working directory does not exist"):
        await Pty.create(CreateInput(command="/bin/sh", cwd="/no/such/dir_g3b"), use_action_lifecycle=False)
    assert set(Pty._sessions) == before


@pytest.mark.asyncio
async def test_running_session_cap_refuses_then_allows_after_close(monkeypatch):
    """E2E-0064: 第 N+1 个明确报错(不留进程)，关闭一个后可再建；退出的会话不占名额。"""
    from flocks.pty.pty import PtyLimitExceeded

    monkeypatch.setenv("FLOCKS_PTY_MAX_SESSIONS", "2")
    baseline = Pty.running_count()
    created = []
    try:
        for _ in range(2 - baseline):
            created.append(await Pty.create(CreateInput(command="/bin/sh", args=["-c", "sleep 30"]), use_action_lifecycle=False))
        before = set(Pty._sessions)
        with pytest.raises(PtyLimitExceeded, match=r"limit reached \(2/2 running\)"):
            await Pty.create(CreateInput(command="/bin/sh", args=["-c", "sleep 30"]), use_action_lifecycle=False)
        assert set(Pty._sessions) == before, "refused create must not register a session"

        await Pty.remove(created.pop().id)
        created.append(await Pty.create(CreateInput(command="/bin/sh", args=["-c", "sleep 30"]), use_action_lifecycle=False))
        assert Pty.running_count() == 2
    finally:
        for info in created:
            await Pty.remove(info.id)


@pytest.mark.asyncio
async def test_exited_sessions_do_not_count_toward_cap(monkeypatch):
    monkeypatch.setenv("FLOCKS_PTY_MAX_SESSIONS", "1")
    assert Pty.running_count() == 0
    done = await Pty.create(CreateInput(command="/bin/sh", args=["-c", "exit 0"]), use_action_lifecycle=False)
    try:
        for _ in range(50):
            await asyncio.sleep(0.05)
            if Pty.get(done.id).status.value == "exited":
                break
        assert Pty.get(done.id).status.value == "exited"
        live = await Pty.create(CreateInput(command="/bin/sh", args=["-c", "sleep 30"]), use_action_lifecycle=False)
        await Pty.remove(live.id)
    finally:
        await Pty.remove(done.id)


def test_max_sessions_env_parsing(monkeypatch):
    from flocks.pty import pty as mod

    monkeypatch.delenv("FLOCKS_PTY_MAX_SESSIONS", raising=False)
    assert mod.max_sessions() == mod.DEFAULT_MAX_SESSIONS == 32
    monkeypatch.setenv("FLOCKS_PTY_MAX_SESSIONS", "5")
    assert mod.max_sessions() == 5
    monkeypatch.setenv("FLOCKS_PTY_MAX_SESSIONS", "0")
    assert mod.max_sessions() is None
    monkeypatch.setenv("FLOCKS_PTY_MAX_SESSIONS", "abc")
    assert mod.max_sessions() == 32
