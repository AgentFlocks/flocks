"""Targeted tests for the Sangfor SIP plugin handler."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "tools"
    / "device"
    / "sangfor_sip_v92"
    / "sangfor_sip.handler.py"
)


def _load_handler_module():
    if not _HANDLER_PATH.exists():
        pytest.skip(f"Sangfor SIP handler not present at {_HANDLER_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_sangfor_sip_handler_under_test",
        str(_HANDLER_PATH),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def handler():
    return _load_handler_module()


def test_run_returns_payload_via_output_field(handler):
    payload = {"code": 0, "data": [{"id": "asset-1"}]}

    async def _fake_handler(cfg, session, params):
        assert params == {"max_count": 10}
        return payload

    cfg = handler.RuntimeConfig(
        base_url="https://sip.test:7443",
        timeout=5,
        platform_name="platform",
        username="user",
        password="password",
        verify_ssl=False,
    )

    with (
        patch.object(handler, "_resolve_runtime_config", return_value=cfg),
        patch.dict(handler._ACTION_MAP, {"compat_check": _fake_handler}),
    ):
        result = asyncio.run(handler._run("compat_check", {"max_count": 10}))

    assert result.success is True
    assert result.error is None
    assert result.output == payload


def test_success_result_falls_back_to_data_for_legacy_constructor(handler):
    class LegacyToolResult:
        def __init__(self, *, success, data=None, error=None):
            self.success = success
            self.data = data
            self.error = error

    payload = {"legacy": True}

    with patch.object(handler, "ToolResult", LegacyToolResult):
        result = handler._success_result(payload)

    assert result.success is True
    assert result.error is None
    assert result.data == payload


def test_success_result_uses_declared_data_field_for_legacy_model(handler):
    class LegacyToolResult:
        __fields__ = {"success": object(), "data": object(), "error": object()}

        def __init__(self, **kwargs):
            assert "output" not in kwargs
            self.success = kwargs["success"]
            self.data = kwargs.get("data")
            self.error = kwargs.get("error")

    payload = {"legacy_field": True}

    with patch.object(handler, "ToolResult", LegacyToolResult):
        result = handler._success_result(payload)

    assert result.success is True
    assert result.error is None
    assert result.data == payload
