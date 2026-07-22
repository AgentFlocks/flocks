from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import flocks.session.features.memory as memory_module


@pytest.mark.asyncio
async def test_missing_memory_config_logs_info(monkeypatch, tmp_path):
    manager = SimpleNamespace(initialize=AsyncMock(return_value=None))
    info_log = MagicMock()
    warn_log = MagicMock()

    monkeypatch.setattr(
        memory_module.Config,
        "get",
        AsyncMock(return_value=SimpleNamespace(memory=None)),
    )
    monkeypatch.setattr(
        memory_module.MemoryManager,
        "get_instance",
        lambda **_kwargs: manager,
    )
    monkeypatch.setattr(memory_module.log, "info", info_log)
    monkeypatch.setattr(memory_module.log, "warn", warn_log)

    memory = memory_module.SessionMemory(
        session_id="session-no-memory-config",
        project_id="project",
        workspace_dir=str(tmp_path),
        enabled=True,
    )

    assert await memory.initialize() is True
    info_log.assert_any_call(
        "session.memory.no_config",
        {"session_id": "session-no-memory-config"},
    )
    assert not any(call.args and call.args[0] == "session.memory.no_config" for call in warn_log.call_args_list)
