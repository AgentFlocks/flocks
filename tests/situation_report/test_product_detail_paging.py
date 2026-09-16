"""Synthetic boundary tests; not report-quality or live backend test data."""

import json

import pytest

from flocks.situation_report.product.workspace import (
    ProductWorkspaceError,
    _material_detail_view,
    read_material_detail,
)
from flocks.tool import truncation
from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolResult


@pytest.mark.parametrize("detail", [
    {"body": "中文\\\"\n" * 100_000},
    {"rows": list(range(20_000))},
    {"body": "start" + "x" * 500_000 + "明确冲突事实" + "y" * 100_000},
])
def test_large_details_can_be_reassembled_without_loss_or_generic_truncation(detail):
    expected = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    offset, pieces = 0, []
    while True:
        page = _material_detail_view(detail, offset=offset, limit=8_000, query="")
        assert page["offset"] == offset
        assert len(page["detailText"]) <= 8_000
        assert page["nextOffset"] > offset
        assert not truncation.truncate_output(json.dumps(page, ensure_ascii=False, indent=2)).truncated
        pieces.append(page["detailText"])
        offset = page["nextOffset"]
        if not page["hasMore"]:
            break
    assert "".join(pieces) == expected
    assert offset == len(expected)


def test_literal_search_locates_tail_fact_without_full_scan():
    detail = {"body": "x" * 600_000 + "sample[1].exe" + "y" * 100_000}
    page = _material_detail_view(detail, offset=0, limit=100, query="sample[1].exe")
    assert page["matchFound"] is True
    assert page["offset"] > 599_000
    assert "sample[1].exe" in page["detailText"]
    assert len(page["detailText"]) <= 100
    missing = _material_detail_view(detail, offset=0, limit=100, query="sample.*exe")
    assert missing["matchFound"] is False  # Literal, not regex.
    assert missing["detailText"] == ""
    assert missing["hasMore"] is False


def test_small_detail_remains_structured_and_out_of_range_is_explicit():
    detail = {"title": "正文"}
    page = _material_detail_view(detail, offset=0, limit=6_000, query="")
    assert page["detail"] == detail
    assert page["hasMore"] is False
    end = _material_detail_view(detail, offset=100_000, limit=6_000, query="")
    assert end["detailText"] == ""
    assert end["offset"] == end["nextOffset"] == end["totalCharacters"]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"offset": -1}, {"offset": True}, {"offset": "1"},
    {"limit": 0}, {"limit": 8_001}, {"limit": True},
    {"query": "x" * 201}, {"query": None},
])
async def test_invalid_paging_is_rejected_before_backend_access(params):
    with pytest.raises(ProductWorkspaceError):
        await read_material_detail(
            session_id="no-session", generation_id="no-generation",
            material_id="REPORT:unit-test", reason="boundary check", **params,
        )


@pytest.mark.asyncio
async def test_tool_registry_keeps_large_detail_page_intact():
    async def handler(ctx):
        return ToolResult(success=True, output={
            "reason": "歧义" * 1_000,
            **_material_detail_view(
                {"body": "中文\\\"\n" * 100_000}, offset=0, limit=8_000, query="",
            ),
        })

    result = await Tool(ToolInfo(name="detail-test", description="unit test"), handler).execute(
        ToolContext("unit-session", "unit-message", agent="situation-report-product")
    )
    assert result.success and not result.truncated
    data = json.loads(result.output)
    assert data["hasMore"]
    assert data["nextOffset"] == 8_000
    assert "Use Grep" not in result.output
