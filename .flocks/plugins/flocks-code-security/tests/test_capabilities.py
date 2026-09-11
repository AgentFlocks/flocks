from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.agent.registry import Agent
from flocks.permission.rule import PermissionLevel, PermissionRule
from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.session.callable_state import set_session_callable_tools
from flocks.tool.registry import Tool, ToolContext, ToolRegistry, ToolResult

from flocks_code_security import cli, runtime as runtime_module
from flocks_code_security.capabilities import (
    OPTIONAL_TOOLS, capability_permissions, capability_prompt, optional_tool_names,
)
from flocks_code_security.projection import AGENT_TOOLS, code_security_tool_projection, register_projection
from flocks_code_security.runtime import build_runtime
from flocks_code_security.service import AuditCaller, AuditService, AuditServiceError, StartScanRequest
from flocks_code_security.tools import audit_prepare, register_tools


@pytest.fixture(autouse=True)
async def isolated_session_storage(tmp_path, monkeypatch):
    from flocks.storage.storage import Storage
    from flocks.session import callable_state

    await Storage.shutdown()
    monkeypatch.setattr(callable_state, "_cache", {})
    await Storage.init(tmp_path / "sessions.db")
    yield
    await Storage.shutdown()


async def prepare(runtime, target, session, **flags):
    result = await audit_prepare(
        ToolContext(session, "message", agent="code-security", extra={"agent_execution_session": True}),
        str(target),
    )
    assert result.success, result.error
    scan_id = result.output["scan_id"]
    runtime.store.set_scan_request_metadata(
        scan_id, owner_subject="cli:test", request_source="cli", workspace_ref=None,
        idempotency_key=None, request_digest="test", **flags,
    )
    return scan_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bash,web", [(False, False), (True, False), (False, True), (True, True)])
async def test_persisted_flags_control_strict_schema_and_execution(tmp_path, monkeypatch, bash, web):
    target = tmp_path / "source"
    target.mkdir()
    (target / "app.py").write_text("value = 1\n")
    runtime = build_runtime(tmp_path / "plugin")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    register_tools()
    register_projection()
    await prepare(runtime, target, "enabled", bash_enabled=bash, web_search_enabled=web)
    await prepare(runtime, target, "disabled")
    # A restart must resolve flags from durable scan state, not process-local flags.
    monkeypatch.setattr(runtime_module, "_runtime", build_runtime(tmp_path / "plugin"))
    all_names = AGENT_TOOLS["code-security"]
    expected = ({"bash"} if bash else set()) | ({"websearch", "webfetch"} if web else set())
    for session, optional in [("enabled", expected), ("disabled", set()), ("unbound", set())]:
        await set_session_callable_tools(session, all_names)
        schema = await list_session_callable_tool_infos(
            session, all_names, strict_declared_tools=True, agent="code-security",
        )
        actual = {tool.name for tool in schema.tool_infos}
        assert actual & OPTIONAL_TOOLS == optional
        ctx = ToolContext(session, "message", agent="code-security", extra={
            "enforce_callable_tools": True, "turn_callable_tool_names": sorted(actual),
        })
        for name in OPTIONAL_TOOLS - optional:
            result = await ToolRegistry.execute(name, ctx)
            assert not result.success and "callable" in result.error.lower()
    wrong_role = code_security_tool_projection(
        [ToolRegistry.get(name).info for name in OPTIONAL_TOOLS],
        {"session_id": "enabled", "agent": "code-security-verifier"},
    )
    assert wrong_role == []


@pytest.mark.asyncio
async def test_projection_rejects_replaced_native_tool(tmp_path, monkeypatch):
    target = tmp_path / "source"
    target.mkdir()
    runtime = build_runtime(tmp_path / "plugin")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    register_tools()
    await prepare(runtime, target, "native", bash_enabled=True, web_search_enabled=True)
    for name in OPTIONAL_TOOLS:
        original = ToolRegistry.get(name)
        replacement = Tool(info=original.info, handler=AsyncMock(return_value=ToolResult(success=True)))
        ToolRegistry.register(replacement)
        try:
            assert code_security_tool_projection(
                [replacement.info], {"session_id": "native", "agent": "code-security"},
            ) == []
        finally:
            ToolRegistry.register(original)


@pytest.mark.parametrize("name", sorted(OPTIONAL_TOOLS))
def test_preflight_requires_enabled_optional_tools(name):
    register_tools()
    tool = ToolRegistry.get(name)
    enabled = tool.info.enabled
    tool.info.enabled = False
    try:
        cli._require_enabled_audit_tools()
        with pytest.raises(RuntimeError, match=name):
            cli._require_enabled_audit_tools(bash_enabled=True, web_search_enabled=True)
    finally:
        tool.info.enabled = enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", sorted(AGENT_TOOLS))
@pytest.mark.parametrize("mode", ["standard", "cybergym_level1"])
@pytest.mark.parametrize("bash,web", [(False, False), (True, False), (False, True), (True, True)])
async def test_all_roles_can_select_enabled_tools_in_both_modes(monkeypatch, agent, mode, bash, web):
    from flocks_code_security.tools import ROLE_AGENTS

    flags = {"mode": mode, "status": "running", "bash_enabled": bash, "web_search_enabled": web}
    role = next(role for role, name in ROLE_AGENTS.items() if name == agent)
    store = SimpleNamespace(
        resolve_binding=lambda session: SimpleNamespace(role=role, scan_id="scan"),
        get_scan=lambda scan: flags,
    )
    register_tools()
    register_projection()
    monkeypatch.setattr(runtime_module, "_runtime", SimpleNamespace(store=store))
    await set_session_callable_tools("all-roles", AGENT_TOOLS[agent])
    schema = await list_session_callable_tool_infos(
        "all-roles", AGENT_TOOLS[agent], strict_declared_tools=True, agent=agent,
    )
    expected = ({"bash"} if bash else set()) | ({"websearch", "webfetch"} if web else set())
    assert {tool.name for tool in schema.tool_infos} & OPTIONAL_TOOLS == expected
    prompt = capability_prompt(flags, agent)
    assert ("Bash is enabled" if bash else "Bash is disabled") in prompt
    assert ("reading are enabled" if web else "reading are disabled") in prompt
    assert "Choose autonomously" in prompt


@pytest.mark.asyncio
async def test_permissions_allow_opt_in_but_preserve_explicit_denial(monkeypatch):
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=SimpleNamespace(permission=[
        PermissionRule(permission="webfetch", pattern="https://blocked.example/*", level=PermissionLevel.DENY),
    ])))
    from flocks.permission.next import PermissionNext
    from flocks.permission.rule import PermissionScope

    permissions = await capability_permissions({"web_search_enabled": True}, "code-security")
    rules = [PermissionRule(
        permission=p.permission, pattern=p.pattern, level=PermissionLevel(p.action), scope=PermissionScope.PATTERN,
    ) for p in permissions]
    assert PermissionNext.evaluate_request("websearch", ["query"], rules) == "allow"
    assert PermissionNext.evaluate_request("webfetch", ["https://example.com"], rules) == "allow"
    assert PermissionNext.evaluate_request("webfetch", ["https://blocked.example/page"], rules) == "deny"
    Agent.get.return_value.permission.append(PermissionRule(
        permission="webfetch", pattern="https://blocked.example/public/*", level=PermissionLevel.ALLOW,
    ))
    permissions = await capability_permissions({"web_search_enabled": True}, "code-security")
    rules = [PermissionRule(permission=p.permission, pattern=p.pattern,
                            level=PermissionLevel(p.action), scope=PermissionScope.PATTERN) for p in permissions]
    assert PermissionNext.evaluate_request("webfetch", ["https://blocked.example/public/page"], rules) == "allow"
    assert PermissionNext.evaluate_request("webfetch", ["https://blocked.example/private"], rules) == "deny"


@pytest.mark.asyncio
async def test_service_rejects_invalid_capabilities_before_start(tmp_path):
    service = AuditService(build_runtime(tmp_path / "plugin"))
    target = tmp_path / "source"
    target.mkdir()
    caller = AuditCaller(subject="cli:test", source="cli", is_admin=True, authorized_root=target)
    for request in (
        StartScanRequest(target, bash_enabled="false"),
        StartScanRequest(target, web_search_enabled=1),
    ):
        with pytest.raises(AuditServiceError):
            await service.start_scan(request, caller)


@pytest.mark.asyncio
async def test_cli_passes_capabilities_to_service(tmp_path, monkeypatch):
    from flocks_code_security import service

    run = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(service, "get_audit_service", lambda: SimpleNamespace(run_scan=run))
    await cli.run_standard_audit(tmp_path, bash_enabled=True, web_search_enabled=True)
    request = run.call_args.args[0]
    assert request.bash_enabled is True and request.web_search_enabled is True
    base = StartScanRequest(tmp_path)
    digests = {AuditService._request_digest(replace(base, bash_enabled=b, web_search_enabled=w))
               for b, w in [(False, False), (True, False), (False, True), (True, True)]}
    assert len(digests) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["standard", "cybergym_level1"])
async def test_service_normalizes_persists_and_reports_flags(tmp_path, monkeypatch, mode):
    target = tmp_path / "source"
    target.mkdir()
    (target / "app.py").write_text("value = 1\n")
    runtime = build_runtime(tmp_path / "plugin")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    service = AuditService(runtime)
    ctx = ToolContext("service-parent", "message", agent="code-security", extra={"agent_execution_session": True})
    async def create_context(request, caller):
        ctx.extra["trusted_cybergym_manifest"] = request.cybergym_manifest
        return ctx

    monkeypatch.setattr(service, "_create_execution_context", create_context)
    run = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(service, "_run_background", run)
    events = []
    result = await service.start_scan(
        StartScanRequest(
            target, bash_enabled=True, web_search_enabled=True, scan_mode=mode,
            cybergym_manifest={
                "task_id": "arvo:0", "task_kind": "arvo", "vulnerable_runner": "fixture:latest",
                "target_binary": "/out/target", "argv_template": ["{input}"], "input_path": "/scratch/input",
                "fuzzer_supported": True, "fuzzer_target": "/out/target", "input_contract": {},
                "gdb_supported": True, "limits": {"fuzz_seconds": 10, "gdb_seconds": 5},
            } if mode == "cybergym_level1" else None,
        ),
        AuditCaller(subject="cli:test", source="cli", is_admin=True, authorized_root=target),
        progress=lambda event, payload: events.append((event, payload)),
    )
    scan_id = result["scan"]["scan_id"]
    await service._active[scan_id].task
    assert result["scan"]["bash_enabled"] is True
    assert result["scan"]["web_search_enabled"] is True
    assert runtime.store.scan_status(scan_id)["bash_enabled"] is True
    assert runtime.store.scan_status(scan_id)["web_search_enabled"] is True
    assert run.call_args.args[0].bash_enabled is True
    prepared = next(payload for event, payload in events if event == "scan.prepared")
    assert prepared["bash_enabled"] is True and prepared["web_search_enabled"] is True


@pytest.mark.asyncio
async def test_native_handlers_execute_with_enabled_schema(tmp_path, monkeypatch):
    import aiohttp
    import json

    register_tools()
    ctx = ToolContext("native-smoke", "message", agent="code-security", extra={
        "enforce_callable_tools": True, "turn_callable_tool_names": sorted(OPTIONAL_TOOLS),
        "workspace_dir": str(tmp_path),
    })
    result = await ToolRegistry.execute("bash", ctx, command="echo flocks-capability-smoke", timeout=5000)
    assert result.success, result.error
    assert "flocks-capability-smoke" in result.output
    # Check the existing sandbox host-override boundary without starting Docker.
    ctx.extra["sandbox"] = {"container_name": "unused", "workspace_dir": str(tmp_path), "container_workdir": "/work"}
    result = await ToolRegistry.execute("bash", ctx, command="echo blocked", host="host")
    assert not result.success and "Elevated host execution is not allowed" in result.error
    ctx.extra.pop("sandbox")

    requests = []

    class Response:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def __init__(self, body):
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return self.body

    class Client(Response):
        def __init__(self):
            pass

        def post(self, url, **kwargs):
            requests.append((url, kwargs["json"]["params"]["arguments"]["query"]))
            return Response("data: " + json.dumps({"result": {"content": [{"text": "https://example.com/advisory"}]}}))

        def get(self, url, **kwargs):
            requests.append((url, None))
            return Response("Affected versions: before 2.0")

    monkeypatch.setattr(aiohttp, "ClientSession", Client)
    search = await ToolRegistry.execute("websearch", ctx, query="official advisory")
    assert search.success, search.error
    page = await ToolRegistry.execute("webfetch", ctx, url=search.output)
    assert page.success and "before 2.0" in page.output
    assert requests == [("https://mcp.exa.ai/mcp", "official advisory"), ("https://example.com/advisory", None)]
    monkeypatch.setattr(Response, "status", 503)
    unavailable = await ToolRegistry.execute("webfetch", ctx, url="https://example.com/unavailable")
    assert not unavailable.success and "503" in unavailable.error


@pytest.mark.asyncio
async def test_bash_enforces_command_policy_before_execution(tmp_path, monkeypatch):
    from flocks.session.runner import SessionRunner, RunnerCallbacks
    from flocks.permission.rule import PermissionScope

    policy = [
        PermissionRule(permission="bash", pattern="echo *", level=PermissionLevel.DENY),
        PermissionRule(permission="bash", pattern="echo permitted", level=PermissionLevel.ALLOW),
    ]
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=SimpleNamespace(permission=policy)))
    permissions = await capability_permissions({"bash_enabled": True}, "code-security")
    runner = object.__new__(SessionRunner)
    runner._turn_permission_ruleset = [PermissionRule(
        permission=p.permission, pattern=p.pattern, level=PermissionLevel(p.action), scope=PermissionScope.PATTERN,
    ) for p in permissions]
    runner.callbacks = RunnerCallbacks()
    runner.session = SimpleNamespace(id="permissions")
    ctx = ToolContext("permissions", "message", permission_callback=runner._handle_permission,
                      extra={"workspace_dir": str(tmp_path)})
    register_tools()
    denied = await ToolRegistry.execute("bash", ctx, command="echo blocked > marker")
    assert not denied.success and "Permission denied: bash" in denied.error
    assert not (tmp_path / "marker").exists()
    allowed = await ToolRegistry.execute("bash", ctx, command="echo permitted")
    assert allowed.success and "permitted" in allowed.output
    ctx.extra["sandbox"] = {"container_name": "unused", "workspace_dir": str(tmp_path), "container_workdir": "/work"}
    denied = await ToolRegistry.execute("bash", ctx, command="echo blocked")
    assert not denied.success and "Permission denied: bash" in denied.error


@pytest.mark.asyncio
async def test_real_sessions_use_independent_persisted_source_workspaces(tmp_path, monkeypatch):
    from pathlib import Path
    from flocks.session.session import Session
    from flocks_code_security import workspaces

    target = tmp_path / "source"
    target.mkdir()
    (target / "app.py").write_text("value = 1\n")
    runtime = build_runtime(tmp_path / "plugin")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(workspaces, "runtime_dir", lambda: tmp_path / "runtime")
    from flocks_code_security.agents import register_agents
    from flocks.session.agent_policy import prepare_session_for_agent

    register_agents()
    agent = await Agent.get("code-security")
    register_tools()
    sessions = [await Session.create(project_id="test", directory=str(tmp_path / "shared"), agent="code-security")
                for _ in range(2)]
    scan_id = await prepare(runtime, target, sessions[0].id, bash_enabled=True)
    scan = runtime.store.get_scan(scan_id)
    directories = []
    for session in sessions:
        assert session.directory == str(tmp_path / "shared")
        await workspaces.prepare_bash_workspace(runtime, scan, session.id)
        restored = await Session.get_by_id(session.id)
        restored = await prepare_session_for_agent(restored, agent)
        assert (await Session.get_by_id(session.id)).directory == restored.directory
        directory = Path(restored.directory)
        directories.append(directory)
        assert (directory / "app.py").read_text() == "value = 1\n"
        result = await ToolRegistry.execute("bash", ToolContext(session.id, "message", extra={
            "workspace_dir": restored.directory,
        }), command="echo changed > app.py")
        assert result.success, result.error
        await workspaces.prepare_bash_workspace(runtime, scan, session.id)
        assert (directory / "app.py").read_text().strip() == "changed"
    assert directories[0] != directories[1]
    assert (target / "app.py").read_text() == "value = 1\n"
    snapshot = runtime.store.get_snapshot(scan["snapshot_id"])
    assert (Path(snapshot.root_path) / "app.py").read_text() == "value = 1\n"


@pytest.mark.asyncio
async def test_static_service_checks_only_parent_policy(tmp_path, monkeypatch):
    from flocks_code_security import service as service_module

    policy = AsyncMock(return_value=[])
    monkeypatch.setattr("flocks_code_security.capabilities.capability_permissions", policy)
    monkeypatch.setattr(service_module, "_require_enabled_audit_tools", lambda **kwargs: None)
    monkeypatch.setattr(service_module, "_resolve_model", AsyncMock(return_value=("provider", "model")))
    monkeypatch.setattr(service_module.Project, "from_directory", AsyncMock(return_value={"project": SimpleNamespace(id="p")}))
    monkeypatch.setattr(service_module.Session, "create", AsyncMock(return_value=SimpleNamespace(id="parent")))
    service = AuditService(build_runtime(tmp_path / "plugin"))
    await service._create_execution_context(
        StartScanRequest(tmp_path, bash_enabled=True),
        AuditCaller(subject="cli:test", source="cli", is_admin=True, authorized_root=tmp_path),
    )
    assert policy.await_count == 1
    assert policy.call_args.args[1] == "code-security"


@pytest.mark.asyncio
async def test_real_registration_preserves_configured_policy(tmp_path, monkeypatch):
    from flocks.config.config import Config, ConfigInfo
    from flocks_code_security.agents import register_agents
    from flocks.permission.next import PermissionNext
    from flocks.permission.rule import PermissionScope

    register_tools()
    register_agents()
    allowed_directory = tmp_path / "allowed"
    allowed_directory.mkdir()
    config = ConfigInfo(
        permission={"bash": "deny", "webfetch": "deny", "external_directory": {"*": "deny"}},
        agent={"code-security": {"permission": {
            "bash": {"echo permitted": "allow"},
            "external_directory": {str(allowed_directory): "allow"},
        }}},
    )
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=config))
    monkeypatch.setattr(Config, "resolve_default_llm", AsyncMock(return_value=None))
    monkeypatch.setattr(ToolRegistry, "init_async", AsyncMock())
    monkeypatch.setattr("flocks.agent.registry.scan_and_load", lambda: {
        "code-security": Agent._custom_agents["code-security"].model_copy(deep=True),
    })
    monkeypatch.setattr("flocks.agent.registry.PluginLoader.load_extension", lambda *a, **kw: None)
    monkeypatch.setattr("flocks.agent.registry._load_storage_custom_agents", AsyncMock(return_value={}))
    monkeypatch.setattr("flocks.agent.registry.Skill.list_enabled", AsyncMock(return_value=[]))
    monkeypatch.setattr("flocks.workflow.center.scan_skill_workflows", AsyncMock(return_value=[]))
    agents = await Agent._load_agents()
    monkeypatch.setattr(Agent, "state", AsyncMock(return_value=agents))
    permissions = await capability_permissions({"bash_enabled": True, "web_search_enabled": True}, "code-security")
    rules = [PermissionRule(permission=p.permission, pattern=p.pattern,
                            level=PermissionLevel(p.action), scope=PermissionScope.PATTERN) for p in permissions]
    assert PermissionNext.evaluate_request("bash", ["echo blocked"], rules) == "deny"
    assert PermissionNext.evaluate_request("bash", ["echo permitted"], rules) == "allow"
    assert PermissionNext.evaluate_request("webfetch", ["https://example.com"], rules) == "deny"
    assert PermissionNext.evaluate_request("websearch", ["query"], rules) == "allow"

    from flocks.project.instance import Instance
    from flocks.session.runner import SessionRunner, RunnerCallbacks

    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="directory-policy", permission=permissions)
    runner.callbacks = RunnerCallbacks()
    runner._turn_permission_ruleset = runner._permission_ruleset_for_agent(agents["code-security"])
    monkeypatch.setattr(Instance, "contains_path", lambda path: False)
    for directory, expected in [(tmp_path, False), (allowed_directory, True)]:
        ctx = ToolContext("directory-policy", "message", permission_callback=runner._handle_permission,
                          extra={"workspace_dir": str(directory)})
        result = await ToolRegistry.execute("bash", ctx, command="echo permitted")
        assert result.success is expected, result.error
        if not expected:
            assert "Permission denied: external_directory" in result.error


@pytest.mark.asyncio
async def test_cancelled_workspace_copy_exits_before_cleanup(tmp_path, monkeypatch):
    import asyncio
    import threading
    from flocks.session.session import Session
    from flocks_code_security import workspaces
    from flocks_code_security.cleanup import _remove_tree

    target = tmp_path / "source"
    target.mkdir()
    (target / "app.py").write_text("original")
    runtime = build_runtime(tmp_path / "plugin")
    snapshot = runtime.snapshots.create(target)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = runtime.store.list_snapshot_files

    def delayed_list(snapshot_id):
        entered.set()
        assert release.wait(5)
        return original(snapshot_id)

    original_copy = runtime.source.copy_to

    def copying(*args):
        try:
            original_copy(*args)
        finally:
            finished.set()

    monkeypatch.setattr(runtime.store, "list_snapshot_files", delayed_list)
    monkeypatch.setattr(runtime.source, "copy_to", copying)
    monkeypatch.setattr(workspaces, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=SimpleNamespace(directory=str(target))))
    update = AsyncMock()
    monkeypatch.setattr(Session, "update", update)
    scan = {"bash_enabled": True, "scan_id": "scan_test", "snapshot_id": snapshot.snapshot_id}
    task = asyncio.create_task(workspaces.prepare_bash_workspace(runtime, scan, "session"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        # A second cancellation must not abandon the still-running copy either.
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        directory = tmp_path / "runtime" / "shell" / "scan_test"
        _remove_tree(directory, expected=directory)
        assert not directory.exists()
        update.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [
    {"external_directory": "deny"},
    {"*": "deny", "bash": "allow"},
    {"*": "ask"},
])
async def test_bash_opt_in_preserves_secondary_permission_gates(tmp_path, monkeypatch, configured):
    from flocks.config.config import Config, ConfigInfo
    from flocks.project.instance import Instance
    from flocks.session.runner import SessionRunner, RunnerCallbacks

    agent = SimpleNamespace(permission=[])
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=ConfigInfo(permission=configured)))
    permissions = await capability_permissions({"bash_enabled": True}, "code-security")
    runner = object.__new__(SessionRunner)
    runner.session = SimpleNamespace(id="secondary-permission", permission=permissions)
    permission_request = AsyncMock(return_value=False)
    runner.callbacks = RunnerCallbacks(on_permission_request=permission_request)
    runner._turn_permission_ruleset = runner._permission_ruleset_for_agent(agent)
    monkeypatch.setattr(Instance, "contains_path", lambda path: False)
    register_tools()
    ctx = ToolContext("secondary-permission", "message", permission_callback=runner._handle_permission,
                      extra={"workspace_dir": str(tmp_path)})
    result = await ToolRegistry.execute("bash", ctx, command="echo blocked > marker")
    assert not result.success and "Permission denied: external_directory" in result.error
    assert not (tmp_path / "marker").exists()
    if configured.get("*") == "ask":
        from flocks.permission.next import PermissionNext

        assert PermissionNext.evaluate_request(
            "external_directory", [str(tmp_path)], runner._turn_permission_ruleset,
        ) == "ask"
        permission_request.assert_awaited_once()
        assert permission_request.call_args.args[0].permission == "external_directory"
    else:
        permission_request.assert_not_awaited()
