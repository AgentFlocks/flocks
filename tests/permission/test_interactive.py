import pytest

from flocks.permission.interactive import auto_approve_enabled, legacy_tool_permission_prompt_required


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
