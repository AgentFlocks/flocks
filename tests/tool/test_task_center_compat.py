from __future__ import annotations

from datetime import datetime, timezone

import pytest

import flocks.tool.task.task_center  # noqa: F401
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.manager import TaskManager
from flocks.task.models import SchedulerMode, TaskTrigger
from flocks.task.store import TaskStore
from flocks.tool.registry import ToolContext, ToolRegistry


def _make_ctx() -> ToolContext:
    return ToolContext(session_id="test-session", message_id="test-message", agent="rex")


@pytest.fixture(autouse=True)
async def isolated_task_env(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskManager._instance = None
    TaskManager._startup_error = None
    TaskStore._initialized = False
    TaskStore._conn = None

    await Storage.init()
    await TaskStore.init()

    yield

    await TaskManager.stop()
    await TaskStore.close()
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    TaskManager._instance = None
    TaskManager._startup_error = None
    TaskStore._initialized = False
    TaskStore._conn = None


class TestTaskCenterCompatibility:
    def test_task_create_schema_allows_legacy_schedule_type(self):
        schema = ToolRegistry.get_schema("task_create")

        assert schema is not None
        assert "schedule_type" in schema.properties
        assert "schedule" in schema.properties
        assert "enabled" in schema.properties
        assert "action" in schema.properties
        assert "type" not in schema.required

    def test_task_update_schema_makes_action_optional_and_exposes_trigger_fields(self):
        schema = ToolRegistry.get_schema("task_update")

        assert schema is not None
        assert "action" not in schema.required
        assert "cron" in schema.properties
        assert "run_once" in schema.properties
        assert "run_at" in schema.properties
        assert "cron_description" in schema.properties
        assert "timezone" in schema.properties
        assert "user_prompt" in schema.properties
        assert "enabled" in schema.properties

    @pytest.mark.asyncio
    async def test_task_create_accepts_legacy_schedule_type_alias(self):
        result = await ToolRegistry.execute(
            "task_create",
            ctx=_make_ctx(),
            title="每10分钟执行一次",
            description="兼容旧 schedule_type 字段",
            schedule_type="cron",
            cron="*/10 * * * *",
            cron_description="每10分钟执行一次",
            user_prompt="执行兼容性检查",
        )

        assert result.success is True

        schedulers, total = await TaskManager.list_schedulers(limit=10)
        assert total == 1
        scheduler = schedulers[0]
        assert scheduler.mode == SchedulerMode.CRON
        assert scheduler.trigger.cron == "*/10 * * * *"
        assert scheduler.trigger.cron_description == "每10分钟执行一次"
        assert scheduler.source.user_prompt == "执行兼容性检查"

    @pytest.mark.asyncio
    async def test_task_create_infers_scheduled_type_from_cron(self):
        result = await ToolRegistry.execute(
            "task_create",
            ctx=_make_ctx(),
            title="终端输出测试",
            description='每4分钟在终端输出"我是 flocks-04"',
            cron="*/4 * * * *",
            user_prompt="在终端中输出：我是 flocks-04",
        )

        assert result.success is True

        schedulers, total = await TaskManager.list_schedulers(limit=10)
        assert total == 1
        scheduler = schedulers[0]
        assert scheduler.mode == SchedulerMode.CRON
        assert scheduler.trigger.cron == "*/4 * * * *"

    @pytest.mark.asyncio
    async def test_task_create_accepts_legacy_schedule_action_and_enabled_fields(self):
        result = await ToolRegistry.execute(
            "task_create",
            ctx=_make_ctx(),
            title="终端输出测试",
            description='每4分钟在终端输出"我是 flocks-04"',
            schedule="*/4 * * * *",
            user_prompt="在终端中输出：我是 flocks-04",
            action="exec",
            enabled="True",
        )

        assert result.success is True

        schedulers, total = await TaskManager.list_schedulers(limit=10)
        assert total == 1
        scheduler = schedulers[0]
        assert scheduler.mode == SchedulerMode.CRON
        assert scheduler.status.value == "active"
        assert scheduler.trigger.cron == "*/4 * * * *"

    @pytest.mark.asyncio
    async def test_task_update_defaults_to_update_and_accepts_schedule_fields(self):
        scheduler = await TaskManager.create_scheduler(
            title="原始任务",
            description="原始描述",
            mode=SchedulerMode.ONCE,
            trigger=TaskTrigger(
                run_immediately=False,
                run_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
            ),
        )

        result = await ToolRegistry.execute(
            "task_update",
            ctx=_make_ctx(),
            task_id=scheduler.id,
            description="更新后的描述",
            cron="*/10 * * * *",
            run_once=False,
            cron_description="每10分钟执行一次",
            timezone="UTC",
            user_prompt="更新后的执行内容",
        )

        assert result.success is True

        updated = await TaskManager.get_scheduler(scheduler.id)
        assert updated is not None
        assert updated.mode == SchedulerMode.CRON
        assert updated.description == "更新后的描述"
        assert updated.trigger.cron == "*/10 * * * *"
        assert updated.trigger.cron_description == "每10分钟执行一次"
        assert updated.trigger.timezone == "UTC"
        assert updated.source.user_prompt == "更新后的执行内容"

    @pytest.mark.asyncio
    async def test_task_update_can_disable_and_enable_scheduled_task(self):
        scheduler = await TaskManager.create_scheduler(
            title="可停止的定时任务",
            mode=SchedulerMode.CRON,
            trigger=TaskTrigger(
                cron="*/5 * * * *",
                timezone="Asia/Shanghai",
            ),
        )

        disable_result = await ToolRegistry.execute(
            "task_update",
            ctx=_make_ctx(),
            task_id=scheduler.id,
            action="stop",
        )

        assert disable_result.success is True
        disabled = await TaskManager.get_scheduler(scheduler.id)
        assert disabled is not None
        assert disabled.status.value == "disabled"

        enable_result = await ToolRegistry.execute(
            "task_update",
            ctx=_make_ctx(),
            task_id=scheduler.id,
            enabled=True,
        )

        assert enable_result.success is True
        enabled = await TaskManager.get_scheduler(scheduler.id)
        assert enabled is not None
        assert enabled.status.value == "active"
