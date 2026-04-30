"""
Tests for the YAML tool plugin system (flocks.tool.tool_loader).
"""

import textwrap
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from flocks.tool.tool_loader import (
    _build_http_handler,
    _extract_response,
    _json_schema_to_params,
    _merge_provider_defaults,
    _normalize_input_schema,
    _params_list_to_params,
    _substitute_params,
    create_yaml_tool,
    delete_python_tool,
    delete_yaml_tool,
    find_yaml_tool,
    list_yaml_tools,
    read_yaml_tool,
    update_yaml_tool,
    yaml_to_tool,
)
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolResult,
    _coerce_params,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
    return path


def _make_tool_yaml(
    name: str = "test_tool",
    handler_type: str = "http",
    url: str = "https://api.example.com/query",
    **overrides: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "name": name,
        "description": "A test tool",
        "category": "custom",
        "handler": {
            "type": handler_type,
            "method": "GET",
            "url": url,
            "query_params": {"q": "{query}"},
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# inputSchema normalization
# ---------------------------------------------------------------------------

class TestNormalizeInputSchema:
    def test_json_schema_format(self):
        raw = {
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "IP address"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["ip"],
            }
        }
        params = _normalize_input_schema(raw)
        assert len(params) == 2
        assert params[0].name == "ip"
        assert params[0].type == ParameterType.STRING
        assert params[0].required is True
        assert params[1].name == "limit"
        assert params[1].type == ParameterType.INTEGER
        assert params[1].required is False
        assert params[1].default == 10

    def test_simplified_parameters_format(self):
        raw = {
            "parameters": [
                {"name": "q", "type": "string", "description": "query", "required": True},
                {"name": "n", "type": "integer", "required": False, "default": 5},
            ]
        }
        params = _normalize_input_schema(raw)
        assert len(params) == 2
        assert params[0].name == "q"
        assert params[0].required is True
        assert params[1].name == "n"
        assert params[1].default == 5

    def test_empty_returns_empty(self):
        assert _normalize_input_schema({}) == []

    def test_inputSchema_takes_priority(self):
        raw = {
            "inputSchema": {
                "type": "object",
                "properties": {"a": {"type": "string"}},
            },
            "parameters": [{"name": "b", "type": "string"}],
        }
        params = _normalize_input_schema(raw)
        assert len(params) == 1
        assert params[0].name == "a"


class TestJsonSchemaToParams:
    def test_enum_support(self):
        schema = {
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
        }
        params = _json_schema_to_params(schema)
        assert params[0].enum == ["fast", "slow"]

    def test_all_types(self):
        schema = {
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "a": {"type": "array"},
                "o": {"type": "object"},
            },
        }
        params = _json_schema_to_params(schema)
        types = {p.name: p.type for p in params}
        assert types["s"] == ParameterType.STRING
        assert types["i"] == ParameterType.INTEGER
        assert types["n"] == ParameterType.NUMBER
        assert types["b"] == ParameterType.BOOLEAN
        assert types["a"] == ParameterType.ARRAY
        assert types["o"] == ParameterType.OBJECT

    def test_preserves_object_and_array_subschemas(self):
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "description": "List of item ids",
                    "items": {"type": "integer"},
                    "minItems": 1,
                },
                "config": {
                    "type": "object",
                    "description": "Configuration map",
                    "additionalProperties": True,
                    "properties": {
                        "mode": {"type": "string"},
                    },
                },
            },
            "required": ["config"],
        }

        params = _json_schema_to_params(schema)
        params_by_name = {param.name: param for param in params}

        assert params_by_name["items"].json_schema == schema["properties"]["items"]
        assert params_by_name["config"].json_schema == schema["properties"]["config"]

        tool_info = ToolInfo(
            name="test_complex_schema",
            description="Test tool",
            category=ToolCategory.CUSTOM,
            parameters=params,
        )
        json_schema = tool_info.get_schema().to_json_schema()

        assert json_schema["properties"]["items"]["items"]["type"] == "integer"
        assert json_schema["properties"]["items"]["minItems"] == 1
        assert json_schema["properties"]["config"]["additionalProperties"] is True
        assert json_schema["properties"]["config"]["properties"]["mode"]["type"] == "string"


class TestCoerceParams:
    def test_coerces_object_and_array_json_strings(self):
        parameters = [
            ToolParameter(
                name="config",
                type=ParameterType.OBJECT,
            ),
            ToolParameter(
                name="items",
                type=ParameterType.ARRAY,
            ),
        ]

        result = _coerce_params(
            {
                "config": '{"enabled": true, "retries": 3}',
                "items": '["a", "b"]',
            },
            parameters,
            tool_name="test_tool",
        )

        assert result["config"] == {"enabled": True, "retries": 3}
        assert result["items"] == ["a", "b"]

    def test_keeps_non_json_or_type_mismatch_strings(self):
        parameters = [
            ToolParameter(
                name="workflow",
                type=ParameterType.OBJECT,
            ),
            ToolParameter(
                name="items",
                type=ParameterType.ARRAY,
            ),
        ]

        result = _coerce_params(
            {
                "workflow": "/tmp/workflow.json",
                "items": '{"not": "a list"}',
            },
            parameters,
            tool_name="test_tool",
        )

        assert result["workflow"] == "/tmp/workflow.json"
        assert result["items"] == '{"not": "a list"}'


# ---------------------------------------------------------------------------
# Provider config merge
# ---------------------------------------------------------------------------

class TestMergeProviderDefaults:
    def test_injects_base_url(self):
        raw = {
            "handler": {
                "type": "http",
                "url": "{base_url}/query",
            }
        }
        provider = {
            "defaults": {"base_url": "https://api.example.com"},
        }
        result = _merge_provider_defaults(raw, provider)
        assert result["handler"]["url"] == "https://api.example.com/query"

    def test_injects_timeout(self):
        raw = {"handler": {"type": "http", "url": "https://example.com"}}
        provider = {"defaults": {"timeout": 60}}
        result = _merge_provider_defaults(raw, provider)
        assert result["handler"]["timeout"] == 60

    def test_does_not_overwrite_existing_timeout(self):
        raw = {"handler": {"type": "http", "url": "https://example.com", "timeout": 10}}
        provider = {"defaults": {"timeout": 60}}
        result = _merge_provider_defaults(raw, provider)
        assert result["handler"]["timeout"] == 10

    def test_injects_auth_header(self):
        raw = {"handler": {"type": "http", "url": "https://example.com"}}
        provider = {
            "defaults": {},
            "auth": {
                "secret": "my_api_key",
                "inject_as": "header",
                "header_name": "X-API-Key",
                "header_prefix": "",
            },
        }
        result = _merge_provider_defaults(raw, provider)
        assert result["handler"]["headers"]["X-API-Key"] == "{secret:my_api_key}"

    def test_injects_auth_query_param(self):
        raw = {"handler": {"type": "http", "url": "https://example.com"}}
        provider = {
            "defaults": {},
            "auth": {
                "secret": "key123",
                "inject_as": "query_param",
                "param_name": "apikey",
            },
        }
        result = _merge_provider_defaults(raw, provider)
        assert result["handler"]["query_params"]["apikey"] == "{secret:key123}"

    def test_no_provider_passthrough(self):
        raw = {"handler": {"type": "http", "url": "https://example.com"}}
        assert _merge_provider_defaults(raw, None) is raw

    def test_injects_category(self):
        raw = {"handler": {"type": "http", "url": "https://example.com"}}
        provider = {"defaults": {"category": "search"}}
        result = _merge_provider_defaults(raw, provider)
        assert result["category"] == "search"


# ---------------------------------------------------------------------------
# Parameter substitution
# ---------------------------------------------------------------------------

class TestSubstituteParams:
    def test_basic_substitution(self):
        result = _substitute_params("Hello {name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_missing_param_becomes_empty(self):
        result = _substitute_params("{missing}", {})
        assert result == ""

    def test_preserves_secret_placeholders(self):
        with patch("flocks.tool.tool_loader._resolve_secrets", side_effect=lambda s: s):
            result = _substitute_params("{secret:key}", {})
            assert "{secret:key}" in result

    def test_multiple_params(self):
        result = _substitute_params(
            "{a}/{b}?c={c}",
            {"a": "x", "b": "y", "c": "z"},
        )
        assert result == "x/y?c=z"


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------

class TestExtractResponse:
    def test_no_path(self):
        assert _extract_response({"a": 1}, None) == {"a": 1}

    def test_single_key(self):
        assert _extract_response({"data": [1, 2]}, "data") == [1, 2]

    def test_nested_path(self):
        data = {"response": {"data": {"items": [1]}}}
        assert _extract_response(data, "response.data.items") == [1]

    def test_missing_key(self):
        assert _extract_response({"a": 1}, "b") is None


# ---------------------------------------------------------------------------
# yaml_to_tool
# ---------------------------------------------------------------------------

class TestYamlToTool:
    def test_basic_tool(self, tmp_path: Path):
        data = _make_tool_yaml()
        yaml_path = _write_yaml(tmp_path / "test_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert isinstance(tool, Tool)
        assert tool.info.name == "test_tool"
        assert tool.info.description == "A test tool"
        assert tool.info.category == ToolCategory.CUSTOM
        assert len(tool.info.parameters) == 1
        assert tool.info.parameters[0].name == "query"

    def test_missing_name_raises(self, tmp_path: Path):
        data = _make_tool_yaml()
        del data["name"]
        yaml_path = _write_yaml(tmp_path / "bad.yaml", data)
        with pytest.raises(ValueError, match="name"):
            yaml_to_tool(data, yaml_path)

    def test_missing_handler_raises(self, tmp_path: Path):
        data = _make_tool_yaml()
        del data["handler"]
        yaml_path = _write_yaml(tmp_path / "bad.yaml", data)
        with pytest.raises(ValueError, match="handler"):
            yaml_to_tool(data, yaml_path)

    def test_with_provider_directory(self, tmp_path: Path):
        provider_dir = tmp_path / "my_provider"
        provider_dir.mkdir()
        _write_yaml(provider_dir / "_provider.yaml", {
            "name": "my_provider",
            "defaults": {"base_url": "https://api.test.com"},
        })
        data = _make_tool_yaml(url="{base_url}/search")
        yaml_path = _write_yaml(provider_dir / "test_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)
        assert tool._provider == "my_provider"  # type: ignore
        assert tool.info.provider == "my_provider"

    def test_disabled_tool(self, tmp_path: Path):
        data = _make_tool_yaml(enabled=False)
        yaml_path = _write_yaml(tmp_path / "test_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)
        assert tool.info.enabled is False

    def test_api_tool_type_sets_source(self, tmp_path: Path, monkeypatch):
        """Tools under the 'api/' subdirectory should get source='api'."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_provider_dir = tmp_path / "api" / "fofa"
        api_provider_dir.mkdir(parents=True)
        _write_yaml(api_provider_dir / "_provider.yaml", {
            "name": "fofa",
            "description": "FOFA search engine",
            "auth": {"secret": "fofa_api_key", "inject_as": "query_param", "param_name": "key"},
            "defaults": {"base_url": "https://fofa.info/api/v1"},
        })
        data = _make_tool_yaml(name="fofa_search", url="{base_url}/search")
        data["provider"] = "fofa"
        yaml_path = _write_yaml(api_provider_dir / "fofa_search.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert tool.info.source == "api"
        assert tool.info.provider == "fofa"
        assert tool._source == "api"  # type: ignore

    def test_api_tool_provider_from_provider_yaml(self, tmp_path: Path, monkeypatch):
        """Provider should be inferred from _provider.yaml when not set in the tool YAML."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_dir = tmp_path / "api" / "fofa"
        api_dir.mkdir(parents=True)
        _write_yaml(api_dir / "_provider.yaml", {
            "name": "fofa",
            "defaults": {"base_url": "https://fofa.info/api/v1"},
        })
        data = _make_tool_yaml(name="fofa_search", url="{base_url}/search")
        # Intentionally NOT setting data["provider"] — must come from _provider.yaml
        yaml_path = _write_yaml(api_dir / "fofa_search.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert tool.info.source == "api"
        assert tool.info.provider == "fofa"

    def test_non_api_tool_type_no_source(self, tmp_path: Path, monkeypatch):
        """Tools NOT under 'api/' should not get source='api'."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        python_dir = tmp_path / "python"
        python_dir.mkdir(parents=True)
        data = _make_tool_yaml(name="py_tool")
        yaml_path = _write_yaml(python_dir / "py_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert tool.info.source is None

    def test_provider_version_from_provider_yaml(self, tmp_path: Path, monkeypatch):
        """`version` in _provider.yaml should be propagated to ToolInfo.provider_version."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_dir = tmp_path / "api" / "sangfor_sip_v92"
        api_dir.mkdir(parents=True)
        _write_yaml(api_dir / "_provider.yaml", {
            "name": "sangfor_sip",
            "version": "9.2",
            "defaults": {"base_url": "https://sip.test/api"},
        })
        data = _make_tool_yaml(name="sangfor_sip_assets", url="{base_url}/assets")
        yaml_path = _write_yaml(api_dir / "sangfor_sip_assets.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        # ``info.provider`` is the storage key (service_id + version) so that
        # ``api_services`` lookups can keep multiple versions side-by-side.
        # The unversioned ``service_id`` is preserved on the Tool instance.
        assert tool.info.provider == "sangfor_sip_v9_2"
        assert tool.info.provider_version == "9.2"
        assert getattr(tool, "_service_id", None) == "sangfor_sip"
        assert getattr(tool, "_provider_version", None) == "9.2"

    def test_provider_version_falls_back_to_defaults_product_version(
        self, tmp_path: Path, monkeypatch,
    ):
        """When top-level `version` is missing, fall back to defaults.product_version."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_dir = tmp_path / "api" / "legacy_provider"
        api_dir.mkdir(parents=True)
        _write_yaml(api_dir / "_provider.yaml", {
            "name": "legacy_provider",
            "defaults": {"base_url": "https://x.test", "product_version": "8.1"},
        })
        data = _make_tool_yaml(name="legacy_tool", url="{base_url}/x")
        yaml_path = _write_yaml(api_dir / "legacy_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert tool.info.provider_version == "8.1"

    def test_provider_version_absent_when_not_declared(
        self, tmp_path: Path, monkeypatch,
    ):
        """No `version` anywhere → provider_version stays None."""
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_dir = tmp_path / "api" / "no_version"
        api_dir.mkdir(parents=True)
        _write_yaml(api_dir / "_provider.yaml", {"name": "no_version"})
        data = _make_tool_yaml(name="no_version_tool")
        yaml_path = _write_yaml(api_dir / "no_version_tool.yaml", data)
        tool = yaml_to_tool(data, yaml_path)

        assert tool.info.provider_version is None


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

class TestCrudHelpers:
    def test_create_and_find(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="my_tool")
        path = create_yaml_tool(data)
        assert path.exists()
        assert path.name == "my_tool.yaml"

        found = find_yaml_tool("my_tool")
        assert found == path

    def test_create_with_provider(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="provider_tool")
        path = create_yaml_tool(data, provider="acme")
        assert "acme" in str(path.parent)

    def test_create_duplicate_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="dup_tool")
        create_yaml_tool(data)
        with pytest.raises(ValueError, match="already exists"):
            create_yaml_tool(data)

    def test_read_yaml_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="read_me")
        create_yaml_tool(data)
        result = read_yaml_tool("read_me")
        assert result is not None
        assert result["name"] == "read_me"

    def test_read_nonexistent(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        assert read_yaml_tool("nope") is None

    def test_update_yaml_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="upd_tool")
        create_yaml_tool(data)
        assert update_yaml_tool("upd_tool", {"description": "Updated"})
        result = read_yaml_tool("upd_tool")
        assert result["description"] == "Updated"

    def test_update_nonexistent(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        assert update_yaml_tool("nope", {"description": "x"}) is False

    def test_delete_yaml_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        data = _make_tool_yaml(name="del_tool")
        create_yaml_tool(data)
        assert delete_yaml_tool("del_tool")
        assert find_yaml_tool("del_tool") is None

    def test_delete_nonexistent(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        assert delete_yaml_tool("nope") is False

    def test_delete_python_tool_removes_only_target_definition(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        py_dir = tmp_path / "python"
        py_dir.mkdir(parents=True, exist_ok=True)
        py_file = py_dir / "helpers.py"
        py_file.write_text(textwrap.dedent("""\
            from flocks.tool.registry import ToolRegistry

            @ToolRegistry.register_function(
                name="tool_one",
                description="one",
            )
            async def tool_one(ctx):
                return None

            @ToolRegistry.register_function(
                name="tool_two",
                description="two",
            )
            async def tool_two(ctx):
                return None
        """))

        assert delete_python_tool("tool_one") is True
        content = py_file.read_text()
        assert 'name="tool_one"' not in content
        assert "async def tool_one" not in content
        assert 'name="tool_two"' in content
        assert "async def tool_two" in content

    def test_list_yaml_tools(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        create_yaml_tool(_make_tool_yaml(name="tool_a"))
        create_yaml_tool(_make_tool_yaml(name="tool_b"))
        results = list_yaml_tools()
        names = {t["name"] for t in results}
        assert "tool_a" in names
        assert "tool_b" in names

    def test_list_with_provider_subdir(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        create_yaml_tool(_make_tool_yaml(name="flat_tool"))
        create_yaml_tool(_make_tool_yaml(name="grouped_tool"), provider="acme")
        results = list_yaml_tools()
        names = {t["name"] for t in results}
        assert "flat_tool" in names
        assert "grouped_tool" in names

    def test_find_in_provider_subdir(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)
        create_yaml_tool(_make_tool_yaml(name="sub_tool"), provider="acme")
        found = find_yaml_tool("sub_tool")
        assert found is not None
        assert "acme" in str(found)

    def test_find_project_level_yaml_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path / "user_tools")
        monkeypatch.chdir(tmp_path)

        project_yaml = _write_yaml(
            tmp_path / ".flocks" / "plugins" / "tools" / "api" / "acme" / "project_tool.yaml",
            _make_tool_yaml(name="project_tool"),
        )

        found = find_yaml_tool("project_tool")

        assert found == project_yaml

    def test_delete_project_level_yaml_tool(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path / "user_tools")
        monkeypatch.chdir(tmp_path)

        project_yaml = _write_yaml(
            tmp_path / ".flocks" / "plugins" / "tools" / "api" / "acme" / "project_del_tool.yaml",
            _make_tool_yaml(name="project_del_tool"),
        )

        assert delete_yaml_tool("project_del_tool") is True
        assert not project_yaml.exists()
        assert find_yaml_tool("project_del_tool") is None


# ---------------------------------------------------------------------------
# scan_directory recursive
# ---------------------------------------------------------------------------

class TestScanDirectoryRecursive:
    def test_recursive_finds_subdirs(self, tmp_path: Path):
        from flocks.plugin.loader import scan_directory

        (tmp_path / "flat.yaml").write_text("name: flat")
        sub = tmp_path / "provider"
        sub.mkdir()
        (sub / "tool.yaml").write_text("name: tool")
        (sub / "_provider.yaml").write_text("name: provider")

        flat_result = scan_directory(tmp_path, recursive=False)
        assert len(flat_result) == 1

        recursive_result = scan_directory(tmp_path, recursive=True)
        assert len(recursive_result) == 2
        names = [Path(p).name for p in recursive_result]
        assert "flat.yaml" in names
        assert "tool.yaml" in names
        assert "_provider.yaml" not in names


# ---------------------------------------------------------------------------
# HTTP handler (integration-style with mocked aiohttp)
# ---------------------------------------------------------------------------

class TestGetApiServiceIds:
    """Test ToolRegistry.get_api_service_ids()."""

    def test_returns_yaml_api_providers(self, tmp_path: Path, monkeypatch):
        from flocks.tool.registry import ToolRegistry
        monkeypatch.setattr("flocks.tool.tool_loader._TOOLS_SUBDIR", tmp_path)

        api_dir = tmp_path / "api" / "fofa"
        api_dir.mkdir(parents=True)
        _write_yaml(api_dir / "_provider.yaml", {
            "name": "fofa", "defaults": {"base_url": "https://fofa.info/api/v1"},
        })
        data = _make_tool_yaml(name="fofa_search", url="{base_url}/search")
        yaml_path = _write_yaml(api_dir / "fofa_search.yaml", data)

        from flocks.tool.tool_loader import yaml_to_tool, _read_yaml_raw
        raw = _read_yaml_raw(yaml_path)
        tool = yaml_to_tool(raw, yaml_path)
        ToolRegistry.register(tool)

        try:
            ids = ToolRegistry.get_api_service_ids()
            assert "fofa" in ids
        finally:
            ToolRegistry._tools.pop("fofa_search", None)

    def test_excludes_non_api_tools(self, tmp_path: Path, monkeypatch):
        from flocks.tool.registry import ToolRegistry, ToolInfo, ToolCategory, Tool

        class _DummyHandler:
            pass

        info = ToolInfo(
            name="_test_custom_xyz",
            description="test",
            category=ToolCategory.CUSTOM,
            source="plugin_yaml",
            provider="some_provider",
        )
        tool = Tool(info=info, handler=lambda ctx, **kw: None)
        ToolRegistry.register(tool)
        try:
            ids = ToolRegistry.get_api_service_ids()
            assert "some_provider" not in ids
        finally:
            ToolRegistry._tools.pop("_test_custom_xyz", None)

    def test_ignores_dynamic_modules_for_api_service_discovery(self):
        from flocks.tool.registry import ToolRegistry

        old_dynamic = ToolRegistry._dynamic_tools_by_module.copy()
        try:
            ToolRegistry._dynamic_tools_by_module = {
                "flocks.tool.security.made_up_service": ["made_up_lookup"],
                "flocks.tool.security.ssh_host_cmd": [],
                "flocks.tool.security.ssh_utils": [],
                "flocks.tool.security.ssh_run_script": [],
            }
            ids = ToolRegistry.get_api_service_ids()
            assert "made_up_service" not in ids
            assert "ssh_host_cmd" not in ids
            assert "ssh_utils" not in ids
            assert "ssh_run_script" not in ids
        finally:
            ToolRegistry._dynamic_tools_by_module = old_dynamic


class TestGetToolSource:
    """Test that _get_tool_source correctly classifies tools."""

    def test_api_source(self):
        from flocks.server.routes.tool import _get_tool_source
        from flocks.tool.registry import ToolInfo, ToolCategory

        info = ToolInfo(
            name="fofa_search",
            description="FOFA search",
            category=ToolCategory.CUSTOM,
            source="api",
            provider="fofa",
        )
        source, source_name = _get_tool_source(info)
        assert source == "api"
        assert source_name == "fofa"

    def test_plugin_yaml_source(self):
        from flocks.server.routes.tool import _get_tool_source
        from flocks.tool.registry import ToolInfo, ToolCategory

        info = ToolInfo(
            name="some_tool",
            description="Some tool",
            category=ToolCategory.CUSTOM,
            source="plugin_yaml",
            provider="acme",
        )
        source, source_name = _get_tool_source(info)
        assert source == "plugin_yaml"
        assert source_name == "acme"

    def test_plugin_py_source(self):
        from flocks.server.routes.tool import _get_tool_source
        from flocks.tool.registry import ToolInfo, ToolCategory

        info = ToolInfo(
            name="py_tool",
            description="Py tool",
            category=ToolCategory.CUSTOM,
            source="plugin_py",
        )
        source, source_name = _get_tool_source(info)
        assert source == "plugin_py"
        assert source_name is None


class TestPluginPyRegistration:
    def test_load_plugin_tools_marks_decorator_registered_tools_as_plugin_py(self):
        from flocks.tool.registry import ToolRegistry, ToolInfo, ToolCategory, Tool

        old_tools = ToolRegistry._tools.copy()
        old_plugin_names = ToolRegistry._plugin_tool_names.copy()
        try:
            ToolRegistry._tools = {}
            ToolRegistry._plugin_tool_names = []

            def _fake_plugin_load() -> None:
                ToolRegistry.register(Tool(
                    info=ToolInfo(
                        name="base64_encode",
                        description="Base64 encode helper",
                        category=ToolCategory.CUSTOM,
                    ),
                    handler=lambda ctx, **kwargs: None,
                ))

            with patch("flocks.plugin.PluginLoader.load_all", side_effect=_fake_plugin_load):
                ToolRegistry._load_plugin_tools()

            assert ToolRegistry._plugin_tool_names == ["base64_encode"]
            assert ToolRegistry._tools["base64_encode"].info.source == "plugin_py"
        finally:
            ToolRegistry._tools = old_tools
            ToolRegistry._plugin_tool_names = old_plugin_names


# ---------------------------------------------------------------------------
# HTTP handler (integration-style with mocked aiohttp)
# ---------------------------------------------------------------------------

class TestHttpHandler:
    @pytest.mark.asyncio
    async def test_get_request(self):
        cfg = {
            "type": "http",
            "method": "GET",
            "url": "https://api.example.com/search",
            "query_params": {"q": "{query}"},
            "timeout": 10,
        }
        handler = _build_http_handler(cfg)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"data": [1, 2, 3]})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        ctx = ToolContext(session_id="test", message_id="test")

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await handler(ctx, query="hello")

        assert result.success is True
        assert result.output == {"data": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_error_mapping(self):
        cfg = {
            "type": "http",
            "method": "GET",
            "url": "https://api.example.com/search",
            "timeout": 10,
            "response": {
                "error_mapping": {401: "Bad API key"},
            },
        }
        handler = _build_http_handler(cfg)

        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        ctx = ToolContext(session_id="test", message_id="test")

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await handler(ctx)

        assert result.success is False
        assert result.error == "Bad API key"

    @pytest.mark.asyncio
    async def test_response_extract(self):
        cfg = {
            "type": "http",
            "method": "GET",
            "url": "https://api.example.com/data",
            "timeout": 10,
            "response_path": "result.items",
        }
        handler = _build_http_handler(cfg)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"result": {"items": [1, 2]}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        ctx = ToolContext(session_id="test", message_id="test")

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await handler(ctx)

        assert result.success is True
        assert result.output == [1, 2]
