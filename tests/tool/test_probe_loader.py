"""Unit tests for ``flocks.tool.probe_loader``."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.probe_loader import (
    ConnectivitySpec,
    Fixture,
    TestManifest,
    clear_cache,
    get_connectivity_spec,
    get_tool_fixtures,
    get_tool_fixtures_by_tool_name,
    load_test_manifest,
)

_PROVIDER_ID = "ngtip_api_v5_1_5"
_SERVICE_ID = "ngtip_api"
_TEST_YAML = "_test.yaml"
_PROVIDER_YAML = "_provider.yaml"

_FULL_MANIFEST = """\
    schema_version: 1
    provider: ngtip_api

    connectivity:
      tool: ngtip_query
      params:
        action: query_ip
        resource: "8.8.8.8"
      success_when:
        tool_result_success: true

    fixtures:
      ngtip_query:
        - label: "IP 信誉查询"
          tags: [smoke, ip]
          params: { action: query_ip, resource: "8.8.8.8" }
          assert: { success: true }
        - label: "域名失陷检测"
          params: { action: query_dns, resource: "example.com" }
      ngtip_platform:
        - label: "情报数量统计"
          params: { action: platform_intelligence_count }
"""

_PROVIDER_YAML_CONTENT = """\
    name: ngtip
    service_id: ngtip_api
    version: "5.1.5"
    description: NGTIP test
"""


def _make_plugin_dir(tmp_path: Path, test_yaml_content: str | None = _FULL_MANIFEST) -> Path:
    """Create a fake plugin dir with _provider.yaml and optionally _test.yaml."""
    plugin_dir = tmp_path / "ngtip_v5_1_5"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / _PROVIDER_YAML).write_text(
        textwrap.dedent(_PROVIDER_YAML_CONTENT), encoding="utf-8"
    )
    if test_yaml_content is not None:
        (plugin_dir / _TEST_YAML).write_text(
            textwrap.dedent(test_yaml_content), encoding="utf-8"
        )
    return plugin_dir


def _patch_plugin_dir(plugin_dir: Path):
    """Context manager: make probe_loader._plugin_dir_for return plugin_dir."""
    return patch(
        "flocks.tool.probe_loader._plugin_dir_for",
        return_value=plugin_dir,
    )


class TestLoadTestManifest:
    def setup_method(self):
        clear_cache()

    def test_full_manifest_parses_correctly(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)

        with _patch_plugin_dir(plugin_dir):
            manifest = load_test_manifest(_PROVIDER_ID)

        assert isinstance(manifest, TestManifest)
        assert manifest.provider_id == _PROVIDER_ID
        assert manifest.connectivity is not None
        assert manifest.connectivity.tool == "ngtip_query"
        assert manifest.connectivity.params == {"action": "query_ip", "resource": "8.8.8.8"}

        assert "ngtip_query" in manifest.fixtures
        assert len(manifest.fixtures["ngtip_query"]) == 2
        assert manifest.fixtures["ngtip_query"][0].label == "IP 信誉查询"
        assert manifest.fixtures["ngtip_query"][0].tags == ("smoke", "ip")
        assert manifest.fixtures["ngtip_query"][0].assertion == {"success": True}

        assert "ngtip_platform" in manifest.fixtures
        assert len(manifest.fixtures["ngtip_platform"]) == 1

    def test_no_test_yaml_returns_none(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content=None)

        with _patch_plugin_dir(plugin_dir):
            manifest = load_test_manifest(_PROVIDER_ID)

        assert manifest is None

    def test_no_plugin_dir_returns_none(self):
        with patch("flocks.tool.probe_loader._plugin_dir_for", return_value=None):
            manifest = load_test_manifest("nonexistent_api")

        assert manifest is None

    def test_broken_yaml_returns_none(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content=": bad: yaml: [")

        with _patch_plugin_dir(plugin_dir):
            manifest = load_test_manifest(_PROVIDER_ID)

        assert manifest is None

    def test_yaml_not_dict_returns_none(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="- just a list\n")

        with _patch_plugin_dir(plugin_dir):
            manifest = load_test_manifest(_PROVIDER_ID)

        assert manifest is None

    def test_result_is_cached(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)

        with _patch_plugin_dir(plugin_dir) as mock_fn:
            load_test_manifest(_PROVIDER_ID)
            load_test_manifest(_PROVIDER_ID)

        assert mock_fn.call_count == 1, "Second call should hit cache"

    def test_clear_cache_forces_reload(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)

        with _patch_plugin_dir(plugin_dir) as mock_fn:
            load_test_manifest(_PROVIDER_ID)
            clear_cache()
            load_test_manifest(_PROVIDER_ID)

        assert mock_fn.call_count == 2


class TestConnectivityParsing:
    def setup_method(self):
        clear_cache()

    def test_missing_connectivity_section(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "test"
                  params: { action: query_ip, resource: "8.8.8.8" }
        """)

        with _patch_plugin_dir(plugin_dir):
            manifest = load_test_manifest(_PROVIDER_ID)

        assert manifest is not None
        assert manifest.connectivity is None

    def test_connectivity_missing_tool_returns_none_spec(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            connectivity:
              params: { action: query_ip }
        """)

        with _patch_plugin_dir(plugin_dir):
            spec = get_connectivity_spec(_PROVIDER_ID)

        assert spec is None

    def test_connectivity_empty_params_is_ok(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            connectivity:
              tool: ngtip_query
              params: {}
        """)

        with _patch_plugin_dir(plugin_dir):
            spec = get_connectivity_spec(_PROVIDER_ID)

        assert spec is not None
        assert spec.params == {}

    def test_connectivity_no_params_key_defaults_to_empty(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            connectivity:
              tool: ngtip_query
        """)

        with _patch_plugin_dir(plugin_dir):
            spec = get_connectivity_spec(_PROVIDER_ID)

        assert spec is not None
        assert spec.params == {}

    def test_connectivity_success_when_in_yaml_is_ignored_with_warning(self, tmp_path):
        """success_when is reserved for future use; current schema always
        asserts ToolResult.success == True. Presence in YAML must not break
        parsing (forward-compat) but should be ignored.
        """
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            connectivity:
              tool: ngtip_query
              params: { action: query_ip, resource: "1.1.1.1" }
              success_when:
                tool_result_success: true
        """)

        with _patch_plugin_dir(plugin_dir):
            spec = get_connectivity_spec(_PROVIDER_ID)

        assert spec is not None
        assert spec.tool == "ngtip_query"
        assert not hasattr(spec, "success_when")


class TestFixtureParsing:
    def setup_method(self):
        clear_cache()

    def test_fixture_label_truncated_to_80_chars(self, tmp_path):
        long_label = "A" * 100
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content=f"""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "{long_label}"
                  params: {{action: query_ip}}
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert len(fixtures) == 1
        assert len(fixtures[0].label) == 80

    def test_fixture_missing_label_is_skipped(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - params: { action: query_ip }
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert fixtures == []

    def test_fixture_label_cn_parsed_when_present(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "IP reputation lookup"
                  label_cn: "IP 信誉查询"
                  params: { action: query_ip }
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert len(fixtures) == 1
        assert fixtures[0].label == "IP reputation lookup"
        assert fixtures[0].label_cn == "IP 信誉查询"

    def test_fixture_label_cn_defaults_to_none_when_omitted(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "English only"
                  params: { action: query_ip }
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert len(fixtures) == 1
        assert fixtures[0].label_cn is None

    def test_fixture_label_cn_ignored_when_not_a_string(self, tmp_path):
        # Numbers, lists, blank strings are silently coerced to None (consistent
        # with how the parser handles other malformed optional fields).
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content="""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "Has number"
                  label_cn: 12345
                  params: { action: query_ip }
                - label: "Has blank"
                  label_cn: "   "
                  params: { action: query_ip }
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert len(fixtures) == 2
        assert fixtures[0].label_cn is None
        assert fixtures[1].label_cn is None

    def test_fixture_label_cn_truncated_to_80_chars(self, tmp_path):
        long_cn = "中" * 100
        plugin_dir = _make_plugin_dir(tmp_path, test_yaml_content=f"""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "ok"
                  label_cn: "{long_cn}"
                  params: {{action: query_ip}}
        """)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "ngtip_query")

        assert len(fixtures) == 1
        assert fixtures[0].label_cn is not None
        assert len(fixtures[0].label_cn) == 80

    def test_fixture_for_unknown_tool_returns_empty(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)

        with _patch_plugin_dir(plugin_dir):
            fixtures = get_tool_fixtures(_PROVIDER_ID, "nonexistent_tool")

        assert fixtures == []

    def test_get_tool_fixtures_by_tool_name_fallback_scan(self, tmp_path):
        """When the tool registry has no entry for ``tool_name`` (registry
        not initialised, or non-API tool), fall back to scanning all
        manifests and return the first match.
        """
        plugin_dir = _make_plugin_dir(tmp_path)
        from flocks.tool import probe_loader as probe_loader_module
        from flocks.config import api_versioning

        fake_descriptor = MagicMock(spec=api_versioning.ApiServiceDescriptor)
        fake_descriptor.storage_key = _PROVIDER_ID
        fake_descriptor.service_id = _SERVICE_ID

        with (
            patch(
                "flocks.tool.probe_loader._service_id_for_tool",
                return_value=None,
            ),
            patch(
                "flocks.config.api_versioning.discover_api_service_descriptors",
                return_value=[fake_descriptor],
            ),
            patch.object(probe_loader_module, "_plugin_dir_for", return_value=plugin_dir),
        ):
            fixtures = get_tool_fixtures_by_tool_name("ngtip_query")

        assert isinstance(fixtures, list)
        assert len(fixtures) > 0
        assert all(isinstance(f, Fixture) for f in fixtures)
        assert fixtures[0].label == "IP 信誉查询"

    def test_get_tool_fixtures_by_tool_name_reverse_lookup_isolates_versions(self, tmp_path):
        """When ``tool.info.provider`` is known, only manifests from
        descriptors with the same ``service_id`` should be considered; a
        co-existing v6 plugin must not poison v5's fixtures.
        """
        # v5 dir with the canonical fixtures
        v5_dir = _make_plugin_dir(tmp_path / "v5")
        # v6 dir with a different fixture for the same tool name
        v6_dir = tmp_path / "v6" / "ngtip_v6"
        v6_dir.mkdir(parents=True)
        (v6_dir / _PROVIDER_YAML).write_text(textwrap.dedent("""\
            name: ngtip
            service_id: ngtip_api
            version: "6.0.0"
        """), encoding="utf-8")
        (v6_dir / _TEST_YAML).write_text(textwrap.dedent("""\
            provider: ngtip_api
            fixtures:
              ngtip_query:
                - label: "v6 ONLY fixture"
                  params: { action: query_ip, resource: "1.1.1.1" }
        """), encoding="utf-8")

        from flocks.tool import probe_loader as probe_loader_module
        from flocks.config import api_versioning

        v5_desc = MagicMock(spec=api_versioning.ApiServiceDescriptor)
        v5_desc.storage_key = "ngtip_api_v5_1_5"
        v5_desc.service_id = _SERVICE_ID

        v6_desc = MagicMock(spec=api_versioning.ApiServiceDescriptor)
        v6_desc.storage_key = "ngtip_api_v6_0_0"
        v6_desc.service_id = _SERVICE_ID

        # _plugin_dir_for resolves a storage_key to the right directory.
        def _resolve(provider_id: str):
            return {
                "ngtip_api_v5_1_5": v5_dir,
                "ngtip_api_v6_0_0": v6_dir,
            }.get(provider_id)

        # Reverse lookup says: this tool is bound to a registry entry whose
        # provider field happens to be "ngtip_api". Since both descriptors
        # share that service_id, both are eligible — but the v5 descriptor
        # comes first and provides a fixture, so v5's must win.
        with (
            patch(
                "flocks.tool.probe_loader._service_id_for_tool",
                return_value=_SERVICE_ID,
            ),
            patch(
                "flocks.config.api_versioning.discover_api_service_descriptors",
                return_value=[v5_desc, v6_desc],
            ),
            patch.object(probe_loader_module, "_plugin_dir_for", side_effect=_resolve),
        ):
            fixtures = get_tool_fixtures_by_tool_name("ngtip_query")

        assert len(fixtures) > 0
        assert fixtures[0].label == "IP 信誉查询", (
            "v5 should win over v6 by descriptor order; got %r" % fixtures[0].label
        )

    def test_get_tool_fixtures_by_tool_name_reverse_lookup_skips_other_services(self, tmp_path):
        """Reverse lookup must not return fixtures from descriptors whose
        service_id differs from the tool's provider.
        """
        # NGTIP plugin (matches)
        ngtip_dir = _make_plugin_dir(tmp_path / "ngtip")

        # Some unrelated service that ALSO defines an "ngtip_query" fixture
        # (pretend collision). Reverse lookup must skip it.
        other_dir = tmp_path / "other" / "other_v1"
        other_dir.mkdir(parents=True)
        (other_dir / _PROVIDER_YAML).write_text(textwrap.dedent("""\
            name: other
            service_id: other_api
            version: "1.0.0"
        """), encoding="utf-8")
        (other_dir / _TEST_YAML).write_text(textwrap.dedent("""\
            provider: other_api
            fixtures:
              ngtip_query:
                - label: "WRONG SERVICE"
                  params: { action: query_ip }
        """), encoding="utf-8")

        from flocks.tool import probe_loader as probe_loader_module
        from flocks.config import api_versioning

        ngtip_desc = MagicMock(spec=api_versioning.ApiServiceDescriptor)
        ngtip_desc.storage_key = _PROVIDER_ID
        ngtip_desc.service_id = _SERVICE_ID

        other_desc = MagicMock(spec=api_versioning.ApiServiceDescriptor)
        other_desc.storage_key = "other_api_v1_0_0"
        other_desc.service_id = "other_api"

        def _resolve(provider_id: str):
            return {
                _PROVIDER_ID: ngtip_dir,
                "other_api_v1_0_0": other_dir,
            }.get(provider_id)

        with (
            patch(
                "flocks.tool.probe_loader._service_id_for_tool",
                return_value=_SERVICE_ID,
            ),
            patch(
                "flocks.config.api_versioning.discover_api_service_descriptors",
                # Put other first to prove ordering doesn't mask correctness.
                return_value=[other_desc, ngtip_desc],
            ),
            patch.object(probe_loader_module, "_plugin_dir_for", side_effect=_resolve),
        ):
            fixtures = get_tool_fixtures_by_tool_name("ngtip_query")

        assert len(fixtures) > 0
        assert fixtures[0].label != "WRONG SERVICE", (
            "Reverse lookup must filter by service_id; the unrelated 'other_api' "
            "fixture leaked through."
        )


_PATCH_SECRET_MGR = "flocks.security.get_secret_manager"
_PATCH_PROVIDER = "flocks.server.routes.provider.Provider"
_PATCH_TOOL_REGISTRY = "flocks.tool.registry.ToolRegistry"
_PATCH_TOOL_SOURCE = "flocks.server.routes.tool._get_tool_source"
_PATCH_PROBE_SPEC = "flocks.tool.probe_loader.get_connectivity_spec"


class TestTestCredentialsManifestBranch:
    """Verify that test_provider_credentials prefers the declared probe when
    a valid _test.yaml connectivity spec is present.
    """

    @pytest.mark.asyncio
    async def test_uses_manifest_probe_on_success(self):
        from flocks.server.routes.provider import test_provider_credentials

        spec = ConnectivitySpec(
            tool="ngtip_query",
            params={"action": "query_ip", "resource": "8.8.8.8"},
        )
        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-apikey"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_PROBE_SPEC, return_value=spec),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None
            mock_tr.init = MagicMock()
            mock_tr.execute = AsyncMock(
                return_value=MagicMock(success=True, error=None)
            )

            result = await test_provider_credentials("ngtip_api_v5_1_5")

        assert result["success"] is True
        assert result["probe_source"] == "manifest"
        assert result["tool_tested"] == "ngtip_query"
        mock_tr.execute.assert_awaited_once_with(
            tool_name="ngtip_query",
            action="query_ip",
            resource="8.8.8.8",
        )

    @pytest.mark.asyncio
    async def test_uses_manifest_probe_on_tool_failure(self):
        from flocks.server.routes.provider import test_provider_credentials

        spec = ConnectivitySpec(
            tool="ngtip_query",
            params={"action": "query_ip", "resource": "8.8.8.8"},
        )
        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "bad-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_PROBE_SPEC, return_value=spec),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None
            mock_tr.init = MagicMock()
            mock_tr.execute = AsyncMock(
                return_value=MagicMock(success=False, error="invalid apikey")
            )

            result = await test_provider_credentials("ngtip_api_v5_1_5")

        assert result["success"] is False
        assert result["probe_source"] == "manifest"
        assert "invalid apikey" in result["message"]

    @pytest.mark.asyncio
    async def test_manifest_exception_falls_back_to_heuristic(self):
        """If the declared probe raises (broken _test.yaml, missing tool, …),
        the endpoint must NOT surface a manifest failure. Instead it logs a
        warning and falls through to the existing heuristic so a malformed
        manifest cannot take down connectivity testing.
        """
        from flocks.server.routes.provider import test_provider_credentials
        from flocks.tool.registry import ToolInfo, ToolCategory, ToolParameter, ParameterType

        spec = ConnectivitySpec(
            tool="ngtip_query",
            params={"action": "query_ip", "resource": "8.8.8.8"},
        )
        # The heuristic path will look for a real tool; provide one.
        heuristic_tool = ToolInfo(
            name="some_heuristic_tool",
            description="fallback tool",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="ip", type=ParameterType.STRING,
                    description="IP", required=True,
                )
            ],
        )

        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "some-key"

        # First execute() raises (manifest probe), second returns OK (heuristic).
        execute_mock = AsyncMock(
            side_effect=[
                RuntimeError("manifest tool blew up"),
                MagicMock(success=True, error=None, metadata={}),
            ]
        )

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "ngtip_api_v5_1_5")),
            patch(_PATCH_PROBE_SPEC, return_value=spec),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None
            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [heuristic_tool]
            mock_tr._dynamic_tools_by_module = {}
            mock_tr.execute = execute_mock

            result = await test_provider_credentials("ngtip_api_v5_1_5")

        assert execute_mock.await_count == 2, (
            "Manifest probe should have raised, then heuristic should have run; "
            f"got {execute_mock.await_count} call(s)"
        )
        assert result.get("probe_source") != "manifest", (
            "After manifest failure, response must NOT claim manifest source"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_manifest_falls_back_to_heuristic(self):
        """When get_connectivity_spec returns None, the heuristic path runs."""
        from flocks.server.routes.provider import test_provider_credentials
        from flocks.tool.registry import ToolInfo, ToolCategory, ToolParameter, ParameterType

        tool_info = ToolInfo(
            name="some_ip_tool",
            description="IP query",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="ip", type=ParameterType.STRING,
                    description="IP", required=True,
                )
            ],
        )
        mock_secrets = MagicMock()
        mock_secrets.get.return_value = "valid-key"

        with (
            patch(_PATCH_SECRET_MGR, return_value=mock_secrets),
            patch(_PATCH_PROVIDER) as mock_provider_cls,
            patch(_PATCH_TOOL_REGISTRY) as mock_tr,
            patch(_PATCH_TOOL_SOURCE, return_value=("api", "some_api")),
            patch(_PATCH_PROBE_SPEC, return_value=None),
        ):
            mock_provider_cls._ensure_initialized = MagicMock()
            mock_provider_cls.apply_config = AsyncMock()
            mock_provider_cls.get.return_value = None
            mock_tr.init = MagicMock()
            mock_tr.list_tools.return_value = [tool_info]
            mock_tr._dynamic_tools_by_module = {}
            mock_tr.execute = AsyncMock(
                return_value=MagicMock(success=True, error=None, metadata={})
            )

            result = await test_provider_credentials("some_api")

        assert result["success"] is True
        assert result.get("probe_source") != "manifest"
