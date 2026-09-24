import pytest

from flocks.permission.interactive import auto_approve_enabled, legacy_tool_permission_prompt_required
from flocks.permission.helpers import from_config


def test_legacy_tool_permission_prompts_are_disabled_by_default() -> None:
    assert legacy_tool_permission_prompt_required() is False


def test_auto_approve_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOCKS_AUTO_APPROVE", raising=False)
    assert auto_approve_enabled() is False
    monkeypatch.setenv("FLOCKS_AUTO_APPROVE", "true")
    assert auto_approve_enabled() is True


@pytest.mark.asyncio
async def test_runner_handle_permission_auto_allows_without_permission_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.runner import SessionRunner

    async def _unexpected_ask(*args, **kwargs):
        raise AssertionError("PermissionNext.ask should not run for legacy tool permissions")

    monkeypatch.setattr(
        "flocks.permission.next.PermissionNext.ask",
        _unexpected_ask,
    )

    runner = SessionRunner.__new__(SessionRunner)
    runner.session = type("Session", (), {"id": "ses_test"})()
    runner._step = 1
    runner.callbacks = type(
        "Callbacks",
        (),
        {"on_permission_request": None, "event_publish_callback": None},
    )()

    request = type(
        "Request",
        (),
        {
            "permission": "write",
            "patterns": ["notes.md"],
            "metadata": {},
            "message_id": "msg_1",
            "always": ["*"],
        },
    )()

    await runner._handle_permission(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_permission", "pattern"),
    [
        ({"task": "deny"}, "explore"),
        ({"delegate_task": {"*": "allow", "explore": "deny"}}, "explore"),
    ],
)
async def test_runner_enforces_delegate_task_deny_for_default_rex(
    monkeypatch: pytest.MonkeyPatch,
    configured_permission,
    pattern: str,
) -> None:
    from flocks.session.runner import SessionRunner

    agent = type(
        "Agent",
        (),
        {"name": "rex", "permission": from_config(configured_permission)},
    )()

    async def _get_agent(name: str):
        assert name == "rex"
        return agent

    async def _allow_request(request):
        return True

    monkeypatch.setattr("flocks.agent.registry.Agent.get", _get_agent)

    runner = SessionRunner.__new__(SessionRunner)
    runner.agent_name = "rex"
    runner.session = type(
        "Session",
        (),
        {"id": "ses_test", "agent": "rex", "permission": None},
    )()
    runner._step = 1
    runner.callbacks = type(
        "Callbacks",
        (),
        {
            "on_permission_request": staticmethod(_allow_request),
            "event_publish_callback": None,
        },
    )()
    request = type(
        "Request",
        (),
        {
            "permission": "delegate_task",
            "patterns": [pattern],
            "metadata": {},
            "message_id": "msg_1",
            "always": ["*"],
        },
    )()

    with pytest.raises(PermissionError, match="delegate_task"):
        await runner._handle_permission(request)


@pytest.mark.asyncio
async def test_runner_prompts_for_explicit_delegate_task_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.runner import SessionRunner

    agent = type(
        "Agent",
        (),
        {"name": "rex", "permission": from_config({"task": {"reviewer": "ask"}})},
    )()
    asked = []

    async def _get_agent(name: str):
        return agent

    async def _ask(**kwargs):
        asked.append(kwargs)
        return "allow"

    monkeypatch.setattr("flocks.agent.registry.Agent.get", _get_agent)
    monkeypatch.setattr("flocks.permission.next.PermissionNext.ask", _ask)

    runner = SessionRunner.__new__(SessionRunner)
    runner.agent_name = "rex"
    runner.session = type(
        "Session",
        (),
        {"id": "ses_test", "agent": "rex", "permission": None},
    )()
    runner._step = 1
    runner.callbacks = type(
        "Callbacks",
        (),
        {"on_permission_request": None, "event_publish_callback": None},
    )()
    request = type(
        "Request",
        (),
        {
            "permission": "delegate_task",
            "patterns": ["reviewer"],
            "metadata": {},
            "message_id": "msg_1",
            "always": ["*"],
        },
    )()

    await runner._handle_permission(request)

    assert len(asked) == 1
    assert asked[0]["permission"] == "delegate_task"
    assert asked[0]["patterns"] == ["reviewer"]
