"""Synthetic large-input tests; no model calls or business-data replacement."""

import json

import pytest

from flocks.situation_report.product.material_paging import bounded_material_page
from flocks.situation_report.product.workspace import ProductWorkspaceError, read_material_page
from flocks.tool import truncation
from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolResult


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['中文\\"\n' * 12_000, list(range(12_000))])
async def test_every_material_is_recoverable_through_registry_without_truncation(content):
    rows = [{"material_id": f"REPORT:test-{index}", "summary": content if index == 1 else "正文"} for index in range(4)]
    recovered, pieces = [], []
    offset = content_offset = 0
    while True:

        async def handler(ctx):
            return ToolResult(
                success=True,
                output=bounded_material_page(
                    rows,
                    offset=offset,
                    content_offset=content_offset,
                    limit=20,
                    metadata={},
                ),
            )

        result = await Tool(ToolInfo(name="material-test", description="unit test"), handler).execute(
            ToolContext("unit-session", "unit-message", agent="situation-report-product"),
        )
        assert not result.truncated
        assert not truncation.truncate_output(result.output).truncated
        page = json.loads(result.output)
        recovered.extend(page["materials"])
        if fragment := page.get("materialFragment"):
            pieces.append(fragment["text"])
            if not fragment["hasMore"]:
                recovered.append(json.loads("".join(pieces)))
                pieces = []
        if not page["hasMore"]:
            break
        cursor = page["nextOffset"], page["nextContentOffset"]
        assert cursor > (offset, content_offset)
        offset, content_offset = cursor
    assert recovered == rows
    assert not pieces


def test_many_small_records_stop_at_size_budget_not_record_limit():
    rows = [{"material_id": f"REPORT:{i}", "summary": "甲" * 1_200} for i in range(50)]
    page = bounded_material_page(rows, offset=0, content_offset=0, limit=50, metadata={})
    assert 1 < len(page["materials"]) < 50
    assert page["nextOffset"] == len(page["materials"])
    assert page["hasMore"] and page["nextContentOffset"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"offset": True},
        {"offset": -1},
        {"content_offset": -1},
        {"content_offset": "1"},
        {"limit": 0},
        {"limit": 51},
        {"limit": False},
    ],
)
async def test_invalid_cursors_fail_before_reading_snapshot(params):
    with pytest.raises(ProductWorkspaceError):
        await read_material_page(session_id="missing", generation_id="missing", **params)


def test_empty_and_invalid_continuation():
    page = bounded_material_page([], offset=0, content_offset=0, limit=20, metadata={})
    assert not page["hasMore"] and not page["materials"]
    with pytest.raises(ValueError):
        bounded_material_page([], offset=0, content_offset=1, limit=20, metadata={})
