import sys
import types
from types import SimpleNamespace

import pytest

from flocks.server import app as app_module


class _DummyLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def warn(self, *_args, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("managed_service", "expected_events"),
    [
        (False, ["cleanup_replaced_files", "recover_upgrade_state"]),
        (True, ["cleanup_replaced_files"]),
    ],
)
async def test_lifespan_recovers_upgrade_state_only_outside_managed_service(
    monkeypatch: pytest.MonkeyPatch,
    managed_service: bool,
    expected_events: list[str],
) -> None:
    events: list[str] = []

    def cleanup_replaced_files() -> None:
        events.append("cleanup_replaced_files")

    def recover_upgrade_state() -> None:
        events.append("recover_upgrade_state")

    async def fake_storage_init() -> None:
        return None

    async def fake_config_get():
        return SimpleNamespace(memory=SimpleNamespace(enabled=False))

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_async_noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(app_module.Log, "_writer", object())
    monkeypatch.setattr(app_module.Log, "create", lambda service: _DummyLogger())
    monkeypatch.setattr(app_module, "init_observability", lambda: None)
    monkeypatch.setattr(app_module, "shutdown_observability", lambda: None)
    monkeypatch.setattr(app_module.Storage, "init", fake_storage_init)
    monkeypatch.setattr(app_module.Config, "get", fake_config_get)
    monkeypatch.setattr(app_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(app_module.asyncio, "sleep", fake_async_noop)
    if managed_service:
        monkeypatch.setenv("_FLOCKS_SERVICE_CONFIG", "{}")
    else:
        monkeypatch.delenv("_FLOCKS_SERVICE_CONFIG", raising=False)

    monkeypatch.setitem(
        sys.modules,
        "flocks.updater.updater",
        types.SimpleNamespace(
            cleanup_replaced_files=cleanup_replaced_files,
            recover_upgrade_state=recover_upgrade_state,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.config.config_writer",
        types.SimpleNamespace(ensure_config_files=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.tool.question_handler",
        types.SimpleNamespace(setup_api_question_handler=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.provider.credential",
        types.SimpleNamespace(migrate_env_credentials=lambda: 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.server.routes.custom_provider",
        types.SimpleNamespace(load_custom_providers_on_startup=fake_async_noop),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.mcp",
        types.SimpleNamespace(MCP=types.SimpleNamespace(init=fake_async_noop, shutdown=fake_async_noop)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.server.routes.workflow",
        types.SimpleNamespace(sync_workflows_from_filesystem=lambda: fake_async_noop() or 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.task.manager",
        types.SimpleNamespace(TaskManager=types.SimpleNamespace(start=fake_async_noop, stop=fake_async_noop)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.task.plugin",
        types.SimpleNamespace(seed_tasks_from_plugin=lambda: fake_async_noop() or 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.skill.skill",
        types.SimpleNamespace(Skill=types.SimpleNamespace(start_watcher=lambda: None, stop_watcher=lambda: None)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.agent.registry",
        types.SimpleNamespace(Agent=types.SimpleNamespace(start_watcher=lambda: None)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.tool.registry",
        types.SimpleNamespace(ToolRegistry=types.SimpleNamespace(init=lambda: None, start_watcher=lambda: None)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.channel.gateway.manager",
        types.SimpleNamespace(
            default_manager=types.SimpleNamespace(start_all=fake_async_noop, stop_all=fake_async_noop),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.server.routes.event",
        types.SimpleNamespace(
            EventBroadcaster=types.SimpleNamespace(
                get=lambda: types.SimpleNamespace(client_count=0, shutdown=fake_async_noop),
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.session.core.status",
        types.SimpleNamespace(SessionStatus=types.SimpleNamespace(get_busy_session_ids=lambda: [])),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.task.store",
        types.SimpleNamespace(TaskStore=types.SimpleNamespace(close=fake_async_noop)),
    )
    monkeypatch.setitem(
        sys.modules,
        "flocks.project.instance",
        types.SimpleNamespace(Instance=types.SimpleNamespace(dispose_all=fake_async_noop)),
    )

    async with app_module.lifespan(SimpleNamespace()):
        pass

    assert events == expected_events
