"""Uninstall: only REA's own resources, running related tasks stopped only after confirmation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.rea import runtime, service, uninstaller
from flocks.rea.installer import ReaInstaller
from flocks.rea.uninstaller import ReaUninstaller, TasksRunning
from tests.rea.conftest import write_skill


@pytest.fixture
def installed(fake_toolchain, fake_mcp, tmp_path):
    """REA as an install leaves it, plus the shared caches an uninstall must keep."""

    from flocks.config.config_writer import ConfigWriter
    from flocks.tool.tool_loader import save_mcp_config

    config = runtime.mcp_server_config("light")
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, config)
    save_mcp_config(runtime.MCP_SERVER_NAME, config)
    fake_mcp.set_connected()
    write_skill(runtime.installed_skill_dir())
    (runtime.legacy_install_root() / "node_modules").mkdir(parents=True)
    (runtime.validation_root() / "run" / "javascript").mkdir(parents=True)
    runtime.write_install_record({
        "version": runtime.REA_VERSION, "tier": "light", "verification": {"ok": True}, "firstPassedAt": 1,
    })
    service.record_first_install("admin-1")
    service.record_install_session("ses_install", "admin-1")
    npx_cache = Path.home() / ".npm" / "_npx" / "abc" / "node_modules" / "rea-agents"
    npx_cache.mkdir(parents=True)
    ghidra = Path(runtime.ghidra_target_dir())
    (ghidra / "support").mkdir(parents=True)
    ConfigWriter.add_mcp_server("other", {"type": "local", "command": ["other-mcp"], "enabled": True})
    assert service.install_state() == "ready"
    return {"mcp": fake_mcp, "npx_cache": npx_cache, "ghidra": ghidra}


@pytest.fixture
def no_tasks(monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(uninstaller, "related_tasks", none)


@pytest.mark.asyncio
async def test_uninstall_removes_only_rea_and_keeps_shared_resources(installed, no_tasks):
    from flocks.config.config_writer import ConfigWriter
    from flocks.tool.tool_loader import _MCP_SUBDIR

    job = await ReaUninstaller.start(stop_tasks=False, user_id="admin-1", previous_state="ready")
    job = await ReaUninstaller.wait()
    assert job.status == "succeeded", job
    assert job.residues == []
    assert job.removed == [
        "MCP 服务 rea 的配置", "技能 reverse-engineer-anything", "早期版本的 REA 安装目录", "REA 验证样例", "REA 安装记录",
    ]
    assert str(Path.home() / ".npm" / "_npx") in job.kept
    assert str(Path(runtime.ghidra_target_dir()).parent / "ghidra_*") in job.kept
    assert installed["mcp"].removed == ["rea"]
    assert ConfigWriter.get_mcp_server("rea") is None
    assert not any(_MCP_SUBDIR.glob("rea*"))
    assert ConfigWriter.get_mcp_server("other") is not None  # other services stay
    assert not runtime.installed_skill_dir().exists()
    assert not runtime.legacy_install_root().exists()
    assert not runtime.validation_root().exists()
    assert runtime.read_install_record() is None
    # Shared caches and the first-install record stay.
    assert installed["npx_cache"].is_dir() and installed["ghidra"].is_dir()
    assert service.first_install_completed("admin-1") is True
    assert service.install_state() == "not_installed"
    assert service.badge_visible("admin-1", "not_installed") is False
    # The next click starts over with the install guide instead of reopening the old conversation.
    assert service.recent_install_session("admin-1") is None


@pytest.mark.asyncio
async def test_running_related_tasks_block_the_uninstall_until_confirmed(installed, monkeypatch):
    tasks = [{"kind": "session", "id": "ses_a", "title": "分析样本"}]

    async def related():
        return list(tasks)

    stopped = []

    async def stop(found):
        stopped.extend(task["id"] for task in found)
        return []

    monkeypatch.setattr(uninstaller, "related_tasks", related)
    monkeypatch.setattr(uninstaller, "_stop", stop)

    with pytest.raises(TasksRunning) as raised:
        await ReaUninstaller.start(stop_tasks=False)
    assert raised.value.tasks == tasks
    assert ReaUninstaller.current_job() is None
    assert stopped == [] and runtime.skill_installed() and service.install_state() == "ready"

    await ReaUninstaller.start(stop_tasks=True)
    job = await ReaUninstaller.wait()
    assert stopped == ["ses_a"]
    assert job.status == "succeeded" and job.stoppedTasks == tasks


@pytest.mark.asyncio
async def test_tasks_that_do_not_stop_leave_everything_in_place(installed, monkeypatch):
    tasks = [{"kind": "session", "id": "ses_a", "title": "分析样本"}]

    async def related():
        return tasks

    async def stop(found):
        return ["ses_a"]

    monkeypatch.setattr(uninstaller, "related_tasks", related)
    monkeypatch.setattr(uninstaller, "_stop", stop)
    await ReaUninstaller.start(stop_tasks=True)
    job = await ReaUninstaller.wait()
    assert job.status == "failed" and job.removed == []
    assert runtime.skill_installed() and runtime.current_mcp_config() is not None
    assert service.install_state() == "uninstall_failed"


@pytest.mark.asyncio
async def test_a_partial_uninstall_reports_residues_and_can_be_retried(installed, no_tasks, monkeypatch):
    real_rmtree = shutil.rmtree
    skill_dir = runtime.installed_skill_dir()

    def flaky_rmtree(path, *args, **kwargs):
        if Path(path) == skill_dir:
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(uninstaller.shutil, "rmtree", flaky_rmtree)
    await ReaUninstaller.start(stop_tasks=False, previous_state="ready")
    job = await ReaUninstaller.wait()
    assert job.status == "failed"
    assert "技能 reverse-engineer-anything" in job.residues
    assert job.errorDetail == "Permission denied: ~/.flocks/plugins/skills/reverse-engineer-anything"
    assert service.install_state() == "uninstall_failed"
    status = await service.build_status(SimpleNamespace(id="admin-9", role="admin"))
    assert status["state"] == "uninstall_failed" and status["removable"] is True
    member = await service.build_status(SimpleNamespace(id="member-1", role="member"))
    assert member["uninstallJob"]["residues"] == [] and member["uninstallJob"]["errorDetail"] is None

    monkeypatch.setattr(uninstaller.shutil, "rmtree", real_rmtree)
    await ReaUninstaller.start(stop_tasks=False)
    job = await ReaUninstaller.wait()
    assert job.status == "succeeded"
    assert service.install_state() == "not_installed"


@pytest.mark.asyncio
async def test_a_newer_install_outranks_an_old_failed_uninstall(installed, no_tasks, monkeypatch):
    from flocks.rea.installer import ReaInstallJob

    monkeypatch.setattr(uninstaller, "_remove_files", lambda: (_ for _ in ()).throw(OSError("busy")))
    await ReaUninstaller.start(stop_tasks=False)
    await ReaUninstaller.wait()
    assert service.install_state() == "uninstall_failed"
    later = ReaUninstaller.current_job().finishedAt + 1
    ReaInstaller._job = ReaInstallJob(
        id="reainst_x", status="succeeded", stage="done", progress=100, startedAt=later, finishedAt=later,
    )
    assert service.install_state() != "uninstall_failed"


@pytest.mark.asyncio
async def test_concurrent_starts_share_one_job(installed, monkeypatch):
    from flocks.mcp import MCP

    looking = asyncio.Event()
    removing = asyncio.Event()

    async def slow_related():
        await looking.wait()
        return []

    async def slow_remove(name):
        await removing.wait()
        return await installed["mcp"].remove(name)

    monkeypatch.setattr(uninstaller, "related_tasks", slow_related)
    monkeypatch.setattr(MCP, "remove", slow_remove)
    first = asyncio.create_task(ReaUninstaller.start(stop_tasks=False))
    await asyncio.sleep(0.01)
    assert ReaUninstaller.is_running() is True  # no install may start meanwhile
    second = asyncio.create_task(ReaUninstaller.start(stop_tasks=False))
    await asyncio.sleep(0.01)
    looking.set()
    a, b = await asyncio.gather(first, second)
    assert a.id == b.id
    removing.set()
    assert (await ReaUninstaller.wait()).status == "succeeded"


@pytest.mark.asyncio
async def test_related_tasks_are_the_install_and_busy_conversations_using_rea(monkeypatch):
    from flocks.rea.installer import ReaInstallJob
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message
    from flocks.session.session import Session

    statuses = {
        "ses_rea": SimpleNamespace(type="busy"),
        "ses_other": SimpleNamespace(type="busy"),
        "ses_earlier": SimpleNamespace(type="busy"),
        "ses_idle": SimpleNamespace(type="idle"),
    }

    def message(role, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role), parts=list(parts))

    typed = SimpleNamespace(type="text", text="分析这个样本", synthetic=False)
    rea_call = SimpleNamespace(type="tool", tool="rea_open_binary")
    conversations = {
        # REA was called a step ago; the step now waiting for the model has no parts yet.
        "ses_rea": [
            message("user", typed),
            message("assistant", SimpleNamespace(type="tool", tool="tool_search")),
            message("assistant", rea_call),
            message("user", SimpleNamespace(type="text", text="<reminder>", synthetic=True)),
            message("assistant"),
        ],
        "ses_other": [message("user", typed), message("assistant", SimpleNamespace(type="tool", tool="bash"))],
        # REA belonged to an earlier reply; the running one does something else.
        "ses_earlier": [message("user", typed), message("assistant", rea_call), message("user", typed), message("assistant")],
        "ses_idle": [message("user", typed), message("assistant", rea_call)],
    }

    async def list_with_parts(session_id, include_archived=False):
        return conversations[session_id]

    async def get_session(session_id):
        return SimpleNamespace(title="分析样本 A")

    monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: statuses))
    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    monkeypatch.setattr(Session, "get_by_id_unfiltered", get_session)
    gate = asyncio.Event()

    async def hang():
        await gate.wait()

    ReaInstaller._job = ReaInstallJob(
        id="reainst_1", status="running", stage="components", progress=10, startedAt=1, sessionId="ses_install",
    )
    ReaInstaller._task = asyncio.create_task(hang())
    try:
        tasks = await uninstaller.related_tasks()
    finally:
        gate.set()
        await ReaInstaller._task
    assert tasks == [
        {"kind": "install", "id": "reainst_1", "title": "REA 安装与验证", "sessionId": "ses_install"},
        {"kind": "session", "id": "ses_rea", "title": "分析样本 A"},
    ]


@pytest.mark.asyncio
async def test_stop_aborts_conversations_and_cancels_the_install(monkeypatch):
    import flocks.server.routes.session as session_routes
    from flocks.session.core.status import SessionStatus

    aborted = []
    busy = {"ses_a": True}

    async def abort(session_id):
        aborted.append(session_id)
        busy[session_id] = False
        return True

    cancelled = []
    monkeypatch.setattr(session_routes, "_abort_session_processing", abort)
    monkeypatch.setattr(SessionStatus, "get", classmethod(
        lambda cls, sid: SimpleNamespace(type="busy" if busy.get(sid) else "idle"),
    ))
    monkeypatch.setattr(ReaInstaller, "cancel", classmethod(lambda cls: cancelled.append(True) or True))
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: False))
    still = await uninstaller._stop([
        {"kind": "install", "id": "reainst_1"}, {"kind": "session", "id": "ses_a"},
    ])
    assert still == [] and aborted == ["ses_a"] and cancelled == [True]


def test_removable_and_kept_resources_for_the_confirmation():
    assert any("MCP 服务 rea" in item for item in uninstaller.removable_resources())
    kept = uninstaller.kept_resources()
    assert str(Path.home() / ".npm" / "_npx") in kept
    assert str(Path(runtime.ghidra_target_dir()).parent / "ghidra_*") in kept
    assert any("历史会话" in item for item in kept)


def test_kept_caches_are_the_guides_paths_on_a_root_host(monkeypatch):
    # The confirmation names this host's caches; on the guide's root host they read as in the guide.
    monkeypatch.setenv("HOME", "/root")
    for name in ("npm_config_cache", "NPM_CONFIG_CACHE", "FLOCKS_REA_GHIDRA_DIR"):
        monkeypatch.delenv(name, raising=False)
    kept = uninstaller.kept_resources()
    assert "/opt/ghidra_*" in kept and "/root/.npm/_npx" in kept


@pytest.mark.asyncio
async def test_a_kept_dependency_inside_an_rea_folder_is_not_deleted(installed, no_tasks, monkeypatch):
    # Ghidra unpacked inside the folder of an earlier REA build: removing that folder would take it along.
    ghidra = runtime.legacy_install_root() / "ghidra_12.1.4_PUBLIC"
    (ghidra / "support").mkdir(parents=True)
    (ghidra / "support" / "analyzeHeadless").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_REA_GHIDRA_DIR", str(ghidra))
    await ReaUninstaller.start(stop_tasks=False, previous_state="ready")
    job = await ReaUninstaller.wait()
    assert (ghidra / "support" / "analyzeHeadless").is_file()
    assert job.status == "failed"
    assert "早期版本的 REA 安装目录" not in job.removed
    assert any("早期版本的 REA 安装目录" in item for item in job.residues)
    assert "要保留的" in (job.errorDetail or "") and "ghidra_12.1.4_PUBLIC" in job.errorDetail
    # The rest of REA went.
    assert not runtime.installed_skill_dir().exists() and not runtime.install_record_path().exists()


@pytest.mark.asyncio
async def test_the_service_file_alone_counts_and_a_failed_delete_is_a_residue(fake_mcp, no_tasks, monkeypatch):
    from flocks.tool import tool_loader

    tool_loader.save_mcp_config(runtime.MCP_SERVER_NAME, runtime.mcp_server_config("light"))
    assert runtime.current_mcp_config() is None and runtime.mcp_config_file() is not None
    assert uninstaller.has_resources() is True

    real_delete = tool_loader.delete_mcp_config
    monkeypatch.setattr(tool_loader, "delete_mcp_config", lambda name: False)
    await ReaUninstaller.start(stop_tasks=False)
    job = await ReaUninstaller.wait()
    assert job.status == "failed" and "插件目录里的 MCP 服务 rea 配置文件" in job.residues

    monkeypatch.setattr(tool_loader, "delete_mcp_config", real_delete)
    await ReaUninstaller.start(stop_tasks=False)
    job = await ReaUninstaller.wait()
    assert job.status == "succeeded" and runtime.mcp_config_file() is None
    assert "MCP 服务 rea 的配置" in job.removed


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["env", "environment"])
@pytest.mark.parametrize("variable", ["JAVA_HOME", "npm_config_cache", "NPM_CONFIG_CACHE"])
async def test_paths_from_the_services_own_environment_are_kept(installed, no_tasks, key, variable):
    from flocks.config.config_writer import ConfigWriter

    # The service runs with a JDK or an npm cache inside the folder of an earlier REA build.
    shared = runtime.legacy_install_root() / "shared"
    (shared / "bin").mkdir(parents=True)
    (shared / "bin" / "marker").write_text("keep", encoding="utf-8")
    config = dict(runtime.current_mcp_config())
    config.pop("environment", None)
    config.pop("env", None)
    config[key] = {variable: str(shared)}
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, config)
    await ReaUninstaller.start(stop_tasks=False, previous_state="ready")
    job = await ReaUninstaller.wait()
    assert (shared / "bin" / "marker").is_file()
    assert job.status == "failed" and any("早期版本的 REA 安装目录" in item for item in job.residues)


@pytest.mark.asyncio
async def test_sessions_using_reas_own_tools_exclude_the_caller_and_status_checks(monkeypatch):
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message
    from flocks.session.session import Session

    def message(role, message_id, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role, id=message_id), parts=list(parts))

    typed = SimpleNamespace(type="text", text="分析这个样本", synthetic=False)
    conversations = {
        "ses_analysis": [message("user", "msg_1", typed), message("assistant", "msg_2", SimpleNamespace(type="tool", tool="rea_list_strings"))],
        "ses_status": [message("user", "msg_3", typed), message("assistant", "msg_4", SimpleNamespace(type="tool", tool="rea_analysis"))],
        "ses_install": [message("user", "msg_5", typed), message("assistant", "msg_6", SimpleNamespace(type="tool", tool="rea_open_binary"))],
    }

    async def list_with_parts(session_id, include_archived=False):
        return conversations[session_id]

    monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: {key: SimpleNamespace(type="busy") for key in conversations}))
    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    tasks = await uninstaller.sessions_using_rea(exclude="ses_install")
    # Only REA's MCP tools count (a status check does not hold the service), and never the caller.
    assert tasks == [{"kind": "session", "id": "ses_analysis", "turn": "msg_1:msg_2", "unknown": False}]


@pytest.mark.asyncio
async def test_the_running_reply_is_named_by_the_message_that_started_it(monkeypatch):
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message

    def message(role, message_id, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role, id=message_id), parts=list(parts))

    typed = SimpleNamespace(type="text", text="再看看这个函数", synthetic=False)
    reminder = SimpleNamespace(type="text", text="<system-reminder>", synthetic=True)
    tool = SimpleNamespace(type="tool", tool="rea_decompile_function")
    conversation = [
        message("user", "msg_1", typed), message("assistant", "msg_2", tool),
        message("user", "msg_3", typed), message("assistant", "msg_4", tool),
        # Flocks' own synthetic message does not start a reply.
        message("user", "msg_5", reminder), message("assistant", "msg_6", tool),
    ]

    async def list_with_parts(session_id, include_archived=False):
        return conversation

    monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: {"ses_b": SimpleNamespace(type="busy")}))
    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    tasks = await uninstaller.sessions_using_rea()
    assert [task["turn"] for task in tasks] == ["msg_3:msg_4"]


@pytest.mark.asyncio
async def test_editing_or_regenerating_the_message_starts_another_reply(monkeypatch):
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message

    def message(role, message_id, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role, id=message_id), parts=list(parts))

    typed = SimpleNamespace(type="text", text="分析这个样本", synthetic=False)
    tool = SimpleNamespace(type="tool", tool="rea_open_binary")
    # Regenerating (or editing and resending) keeps msg_1 and replaces the reply's steps.
    conversations = [
        [message("user", "msg_1", typed), message("assistant", "msg_2", tool), message("assistant", "msg_3", tool)],
        [message("user", "msg_1", typed), message("assistant", "msg_8", tool)],
        # A reply whose first step has no id cannot be told from the next one.
        [message("user", "msg_1", typed), message("assistant", None, tool)],
        [message("user", None, typed), message("assistant", "msg_9", tool)],
    ]
    turns = []
    for conversation in conversations:
        async def list_with_parts(session_id, include_archived=False, conversation=conversation):
            return conversation

        monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: {"ses_b": SimpleNamespace(type="busy")}))
        monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
        turns.append([(task["turn"], task["unknown"]) for task in await uninstaller.sessions_using_rea()])
    assert turns == [[("msg_1:msg_2", False)], [("msg_1:msg_8", False)], [("", True)], [("", True)]]


@pytest.mark.asyncio
async def test_a_busy_conversation_that_cannot_be_read_counts_and_none_is_titled(monkeypatch):
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message

    def message(role, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role, id=f"{role}_msg"), parts=list(parts))

    typed = SimpleNamespace(type="text", text="分析这个样本", synthetic=False)

    async def list_with_parts(session_id, include_archived=False):
        if session_id == "ses_broken":
            raise OSError(5, "Input/output error")
        return [message("user", typed), message("assistant", SimpleNamespace(type="tool", tool="rea_list_strings"))]

    monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: {
        "ses_broken": SimpleNamespace(type="busy"), "ses_private": SimpleNamespace(type="busy"),
    }))
    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    tasks = await uninstaller.sessions_using_rea()
    assert sorted((task["id"], task["unknown"]) for task in tasks) == [("ses_broken", True), ("ses_private", False)]
    assert all("title" not in task for task in tasks)


@pytest.mark.asyncio
async def test_an_uninstall_does_not_start_while_another_process_changes_rea(installed, no_tasks):
    from flocks.rea import operation

    other = operation.acquire("reainst_other")  # another Flocks process on this host runs an REA job
    assert other is not None
    try:
        with pytest.raises(uninstaller.OperationRunning):
            await ReaUninstaller.start(stop_tasks=True)
    finally:
        other.release()
    assert runtime.installed_skill_dir().exists() and installed["mcp"].removed == []
    assert service.install_state() == "ready"


@pytest.mark.asyncio
async def test_an_install_another_process_runs_is_not_taken_for_stopped(installed, monkeypatch):
    from flocks.rea import operation
    from flocks.rea.installer import ReaInstallJob

    monkeypatch.setattr(uninstaller, "_STOP_WAIT_SECONDS", 0.3)
    other = operation.acquire("reainst_other")
    running = ReaInstallJob(id="reainst_other", status="running", stage="components", progress=10, startedAt=1)
    await operation.publish("install", running.model_dump())
    try:
        tasks = await uninstaller.related_tasks()
        assert [task["id"] for task in tasks] == ["reainst_other"]
        # This process cannot stop it, even when the user agreed: refused at once.
        with pytest.raises(uninstaller.OperationRunning):
            await ReaUninstaller.start(stop_tasks=True)
        assert (await operation.read("install"))[0]["id"] == "reainst_other"
        assert await operation.read("uninstall") is None
    finally:
        other.release()
    assert runtime.installed_skill_dir().exists() and installed["mcp"].removed == []


@pytest.mark.asyncio
async def test_the_uninstall_holds_the_hosts_rea_lock_while_it_removes(installed, no_tasks, monkeypatch):
    from flocks.mcp import MCP
    from flocks.rea import operation

    held = []
    remove = MCP.remove

    async def remove_and_check(name):
        held.append(operation.acquire("reainst_other"))  # what another process's install would try now
        return await remove(name)

    monkeypatch.setattr(MCP, "remove", remove_and_check)
    started = await ReaUninstaller.start(stop_tasks=False)
    job = await ReaUninstaller.wait()
    assert job.status == "succeeded" and held == [None]
    assert operation.holder() is None
    # Published while it held the lock: other processes read how it ended.
    assert (await operation.read("uninstall"))[0]["status"] == "succeeded"
    assert (await operation.read("uninstall"))[0]["id"] == started.id


@pytest.mark.asyncio
async def test_conversations_another_process_runs_count_and_are_not_taken_for_stopped(monkeypatch):
    import flocks.server.routes.session as session_routes
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message
    from flocks.session.session import Session

    def message(role, message_id, *parts):
        return SimpleNamespace(info=SimpleNamespace(role=role, id=message_id), parts=list(parts))

    typed = SimpleNamespace(type="text", text="分析这个样本", synthetic=False)
    remote = [message("user", "msg_1", typed), message("assistant", "msg_2", SimpleNamespace(type="tool", tool="rea_open_binary"))]

    async def list_with_parts(session_id, include_archived=False):
        return remote

    async def elsewhere():
        return ["ses_remote"]  # busy in another Flocks process, idle here

    async def get_session(session_id):
        return SimpleNamespace(title="分析样本 R")

    async def abort(session_id):
        return False  # stops it in this process only

    async def owned(session_id):
        return session_id == "ses_remote"  # its runner still holds the lease

    monkeypatch.setattr(SessionStatus, "list", classmethod(lambda cls: {}))
    monkeypatch.setattr(SessionStatus, "get", classmethod(lambda cls, sid: SimpleNamespace(type="idle")))
    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    monkeypatch.setattr(Session, "get_by_id_unfiltered", get_session)
    monkeypatch.setattr(uninstaller, "_sessions_running_elsewhere", elsewhere)
    monkeypatch.setattr(uninstaller, "is_owned", owned)
    monkeypatch.setattr(session_routes, "_abort_session_processing", abort)
    monkeypatch.setattr(uninstaller, "_STOP_WAIT_SECONDS", 0.3)

    # The install check cannot confirm or stop it from here.
    assert await uninstaller.sessions_using_rea() == [
        {"kind": "session", "id": "ses_remote", "turn": "msg_1:msg_2", "unknown": True},
    ]
    tasks = await uninstaller.related_tasks()
    assert tasks == [{"kind": "session", "id": "ses_remote", "title": "分析样本 R"}]
    assert await uninstaller._stop(tasks) == ["ses_remote"]


@pytest.mark.asyncio
async def test_a_busy_conversation_that_cannot_be_read_keeps_the_uninstall_from_starting(installed, monkeypatch):
    from flocks.session.message import Message

    async def busy():
        return {"ses_unreadable": True}

    async def unreadable(session_id, include_archived=False):
        raise RuntimeError("database went away")

    monkeypatch.setattr(uninstaller, "_busy_sessions", busy)
    monkeypatch.setattr(Message, "list_with_parts", unreadable)

    with pytest.raises(TasksRunning) as running:
        await ReaUninstaller.start(stop_tasks=False)
    assert running.value.tasks == [
        {"kind": "session", "id": "ses_unreadable", "title": "ses_unreadable", "unknown": True},
    ]
    assert runtime.installed_skill_dir().exists()

