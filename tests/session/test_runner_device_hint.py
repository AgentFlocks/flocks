import pytest

from flocks.session.runner import SessionRunner


@pytest.mark.asyncio
async def test_device_asset_hint_stays_short_and_strategy_only() -> None:
    runner = SessionRunner.__new__(SessionRunner)
    hint = await SessionRunner._build_device_asset_hint(runner)

    assert hint is not None
    assert "当前共接入" not in hint
    assert "启用" not in hint
    assert "`device_context`" in hint
    assert "`tool_search`" in hint
    assert "`device_id`" in hint
    assert "机房:" not in hint
    assert "可用工具:" not in hint
