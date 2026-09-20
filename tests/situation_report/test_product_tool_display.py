"""Presentation contract tests; all fixtures are synthetic, no model calls."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.situation_report.product import tool_display as display


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("status", ["pending", "running", "succeeded", "needs_revision", "failed", "cancelled"])
def test_display_schema(language, status):
    for step in display.TOOL_STEPS.values():
        value = display.metadata(step, status, language)["display"]
        assert set(value) == {"status", "title", "detail"}
        assert value["status"] == status
        assert value["title"]


def test_last_page_does_not_claim_all_read():
    value = display.completed_metadata(
        "materials",
        {
            "offset": 20,
            "total": 22,
            "materials": [{}, {}],
            "hasMore": False,
        },
        "zh-CN",
    )["display"]
    assert "21–22" in value["detail"]
    assert "末页" in value["detail"]
    assert "全部" not in value["detail"]


def test_fragment_and_empty_source():
    value = display.completed_metadata(
        "materials",
        {
            "offset": 0,
            "total": 1,
            "hasMore": True,
            "materialFragment": {"offset": 100, "nextOffset": 200, "totalCharacters": 300},
        },
        "en-US",
    )["display"]
    assert "101–200/300" in value["detail"]
    value = display.completed_metadata(
        "source",
        {
            "offset": 0,
            "nextOffset": 0,
            "totalCharacters": 0,
        },
        "en-US",
    )["display"]
    assert "No detail" in value["detail"]


@pytest.mark.parametrize("step", ["write", "revision", "validate"])
def test_revision_is_not_tool_failure(step):
    check = {"status": "needs_revision", "attempt": 3, "issues": [{}, {}]}
    value = display.completed_metadata(step, check if step == "validate" else {"validation": check}, "zh-CN")["display"]
    assert value["status"] == "needs_revision"
    assert "上限" in value["detail"]


@pytest.mark.asyncio
async def test_generic_tools_unchanged():
    assert await display.failure_metadata("skill_load", "synthetic", "") is None


@pytest.fixture
def plugin(monkeypatch):
    from flocks.tool.registry import ToolRegistry

    # Import the wrapper without mutating the process-wide tool registry.
    monkeypatch.setattr(ToolRegistry, "register_function", lambda **kw: lambda fn: fn)
    path = Path(__file__).resolve().parents[2] / ".flocks/plugins/tools/python/situation_report_product.py"
    spec = importlib.util.spec_from_file_location("display_test_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(display, "settings", AsyncMock(return_value={"language": "en-US", "revision": False}))
    return module


@pytest.mark.asyncio
async def test_write_progress_and_final_output(plugin):
    ctx = MagicMock(session_id="synthetic", aborted=False)
    output = {"validation": {"status": "needs_revision", "attempt": 1, "issues": [{}]}}

    async def operation(**kwargs):
        await kwargs["on_validation"]()
        return output

    result = await plugin._run(ctx, "write", operation, generation_id="synthetic")
    assert result.success and result.output == output
    assert result.metadata["display"]["status"] == "needs_revision"
    titles = [call.args[0]["metadata"]["display"]["title"] for call in ctx.metadata.call_args_list]
    assert titles == ["In progress: Write initial report draft", "In progress: Check report"]


@pytest.mark.asyncio
async def test_errors_and_abort(plugin):
    ctx = MagicMock(session_id="synthetic", aborted=False)
    result = await plugin._run(
        ctx, "context", AsyncMock(side_effect=ValueError("/private/path")), generation_id="synthetic"
    )
    assert not result.success
    assert result.metadata["display"]["status"] == "failed"
    assert "/private" not in str(result.metadata)
    ctx.aborted = True
    operation = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await plugin._run(ctx, "context", operation, generation_id="synthetic")
    operation.assert_not_called()
