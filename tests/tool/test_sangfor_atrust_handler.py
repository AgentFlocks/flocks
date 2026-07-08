"""Targeted tests for the Sangfor aTrust OpenAPI V3 handler."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "flockshub"
    / "plugins"
    / "tools"
    / "device"
    / "sangfor_atrust_v3"
)
_HANDLER_PATH = _PLUGIN_DIR / "sangfor_atrust.handler.py"
_PROVIDER_PATH = _PLUGIN_DIR / "_provider.yaml"


def _load_handler_module():
    if not _HANDLER_PATH.exists():
        pytest.skip(f"Sangfor aTrust handler not present at {_HANDLER_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_sangfor_atrust_handler_under_test",
        str(_HANDLER_PATH),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def handler():
    return _load_handler_module()


def test_provider_version_derives_expected_storage_key():
    from flocks.config.api_versioning import derive_storage_key

    provider = yaml.safe_load(_PROVIDER_PATH.read_text(encoding="utf-8"))

    assert provider["version"] == "3"
    assert provider["defaults"]["product_version"] == "3"
    assert derive_storage_key(provider["service_id"], provider["version"]) == "sangfor_atrust_v3"


def test_signature_matches_documented_example(handler, monkeypatch):
    monkeypatch.setattr(handler.time, "time", lambda: 1629527100)
    monkeypatch.setattr(handler.uuid, "uuid4", lambda: "f5f0fe63-5b3e-4e44-908c-b95758b6d7e4")

    config = handler.RuntimeConfig(
        base_url="https://1.1.1.1:4433",
        app_id="8165305",
        app_secret="aebd2e3c5ea2449aa2928c102f9db276",
        verify_ssl=False,
        timeout=30,
        locale="zh-cn",
        default_lang="zh-CN",
    )
    query_string = handler._query_string({"username": "sf", "password": "123"})
    body_text = handler._body_text({"status": 1, "type": "test"})

    headers = handler._signature_headers(config, "/api/v1/admin/login", query_string, body_text)

    assert query_string == "password=123&username=sf"
    assert body_text == '{"status":1,"type":"test"}'
    assert headers["X-Ca-Sign"] == "5eec2b22d4ad87daac420d9ef1476346da46ecabbfb2ed18a744d571cdde7756"


def test_query_signing_uses_raw_values_but_url_query_is_encoded(handler):
    query = {"pageSize": 20, "pageIndex": 1, "groupName": "集团内部应用"}

    assert handler._query_string(query) == "groupName=集团内部应用&pageIndex=1&pageSize=20"
    assert handler._url_query_string(query) == (
        "groupName=%E9%9B%86%E5%9B%A2%E5%86%85%E9%83%A8%E5%BA%94%E7%94%A8"
        "&pageIndex=1&pageSize=20"
    )


def test_identity_v3_paths_default_lang_query(handler):
    assert handler._with_default_query_params("/api/v3/user/queryAll", {}, "zh-CN") == {
        "lang": "zh-CN"
    }
    assert handler._with_default_query_params("/api/v3/group/queryAll", {}, "zh-CN") == {
        "lang": "zh-CN"
    }
    assert handler._with_default_query_params(
        "/api/v3/user/queryById",
        {"directoryDomain": "custom", "lang": "en-US"},
        "zh-CN",
    ) == {"directoryDomain": "custom", "lang": "en-US"}
    assert handler._with_default_query_params("/api/v3/group/queryByFullPath", {}, "zh-CN") == {}
    assert handler._with_default_query_params("/api/v3/resource/queryAll", {}, "zh-CN") == {}
