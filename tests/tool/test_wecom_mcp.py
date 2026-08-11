from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import flocks.tool.wecom.wecom_mcp  # noqa: F401
from flocks.tool.registry import ToolRegistry
from flocks.tool.wecom.wecom_mcp import _handle_list


def test_wecom_mcp_schema_uses_object_arguments() -> None:
    schema = ToolRegistry.get_schema("wecom_mcp")

    assert schema is not None
    assert schema.properties["action"]["enum"] == ["list", "call"]
    assert schema.properties["args"]["type"] == "object"
    assert schema.properties["args"]["additionalProperties"] is True


@pytest.mark.asyncio
async def test_wecom_mcp_list_preserves_method_input_schema() -> None:
    input_schema = {
        "type": "object",
        "properties": {"docid": {"type": "string"}},
        "required": ["docid"],
    }
    result = {
        "tools": [
            {
                "name": "get_doc_content",
                "description": "Read document content",
                "inputSchema": input_schema,
            }
        ]
    }

    with patch(
        "flocks.tool.wecom.wecom_mcp._send_rpc",
        AsyncMock(return_value=result),
    ):
        output = await _handle_list("doc")

    payload = json.loads(output)
    assert payload["tools"][0]["inputSchema"] == input_schema
