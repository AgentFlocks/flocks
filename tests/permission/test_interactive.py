import pytest

from flocks.permission.interactive import auto_approve_enabled, legacy_tool_permission_prompt_required


def test_legacy_tool_permission_prompts_are_disabled_by_default() -> None:
    assert legacy_tool_permission_prompt_required() is False


def test_auto_approve_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOCKS_AUTO_APPROVE", raising=False)
    assert auto_approve_enabled() is False
    monkeypatch.setenv("FLOCKS_AUTO_APPROVE", "true")
    assert auto_approve_enabled() is True
