"""
MCP Utility Functions Unit Tests
"""

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest import mock
from flocks.mcp.utils import (
    build_mcp_headers,
    build_mcp_url,
    config_has_pending_credentials,
    extract_api_key_from_mcp_url,
    extract_auth_value_from_mcp_config,
    get_connect_block_reason,
    normalize_mcp_config,
    resolve_env_var,
    sanitize_name,
    generate_tool_name,
    calculate_schema_hash,
    should_skip_connect_on_add,
)


class TestBuildMcpUrl:
    """Test URL building"""
    
    def test_without_auth(self):
        """Test URL without authentication"""
        url = build_mcp_url("https://example.com/mcp")
        assert url == "https://example.com/mcp"
    
    def test_with_query_auth(self):
        """Test query parameter authentication"""
        auth = {
            "type": "apikey",
            "location": "query",
            "param_name": "apikey",
            "value": "test123"
        }
        url = build_mcp_url("https://example.com/mcp", auth)
        assert "apikey=test123" in url
    
    def test_with_header_auth(self):
        """Test header authentication (does not modify URL)"""
        auth = {
            "type": "apikey",
            "location": "header",
            "param_name": "Authorization",
            "value": "Bearer token123"
        }
        url = build_mcp_url("https://example.com/mcp", auth)
        assert url == "https://example.com/mcp"
    
    def test_preserves_existing_query(self):
        """Test preserving existing query parameters"""
        auth = {
            "type": "apikey",
            "location": "query",
            "param_name": "key",
            "value": "value"
        }
        url = build_mcp_url("https://example.com/mcp?foo=bar", auth)
        assert "foo=bar" in url
        assert "key=value" in url


class TestBuildMcpHeaders:
    """Test remote MCP header building"""

    def test_without_headers(self):
        """No header config should return None"""
        assert build_mcp_headers() is None

    def test_with_static_headers(self):
        """Static headers should be preserved"""
        headers = build_mcp_headers({"Api-Key": "token123"})
        assert headers == {"Api-Key": "token123"}

    def test_with_header_auth(self):
        """Header auth should be injected into headers"""
        headers = build_mcp_headers(
            {"X-Client": "flocks"},
            {
                "type": "apikey",
                "location": "header",
                "param_name": "Authorization",
                "value": "Bearer token123",
            },
        )
        assert headers == {
            "X-Client": "flocks",
            "Authorization": "Bearer token123",
        }

    def test_with_bearer_scheme_prefixes_secret_value(self):
        """Bearer auth should prepend the scheme after resolving the secret."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / ".secret.json"
            secret_file.write_text(json.dumps({"demo_mcp_key": "token123"}))
            secret_file.chmod(0o600)

            from flocks.security.secrets import SecretManager
            sm = SecretManager(secret_file=secret_file)
            with mock.patch("flocks.security.secrets.get_secret_manager", return_value=sm):
                headers = build_mcp_headers(
                    None,
                    {
                        "type": "apikey",
                        "location": "header",
                        "param_name": "Authorization",
                        "scheme": "bearer",
                        "value": "{secret:demo_mcp_key}",
                    },
                )
                assert headers == {"Authorization": "Bearer token123"}

    def test_header_secret_is_resolved(self):
        """Header values should resolve {secret:KEY} placeholders"""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / ".secret.json"
            secret_file.write_text(json.dumps({"qianxin_mcp_key": "secret_value_123"}))
            secret_file.chmod(0o600)

            from flocks.security.secrets import SecretManager
            sm = SecretManager(secret_file=secret_file)
            with mock.patch("flocks.security.secrets.get_secret_manager", return_value=sm):
                headers = build_mcp_headers({"Api-Key": "{secret:qianxin_mcp_key}"})
                assert headers == {"Api-Key": "secret_value_123"}


class TestNormalizeMcpConfig:
    """Test backend MCP config normalization"""

    def test_normalizes_stdio_alias_and_combines_args(self):
        config = normalize_mcp_config(
            {
                "type": "stdio",
                "command": "uvx",
                "args": ["mcp-server", "--port", "8080"],
            }
        )
        assert config == {
            "type": "local",
            "command": ["uvx", "mcp-server", "--port", "8080"],
        }

    def test_normalizes_sse_alias_to_remote_transport(self):
        config = normalize_mcp_config(
            {
                "type": "sse",
                "url": "https://example.com/sse",
            }
        )
        assert config == {
            "type": "remote",
            "url": "https://example.com/sse",
            "transport": "sse",
        }

    def test_normalizes_streamable_http_transport_alias(self):
        config = normalize_mcp_config(
            {
                "type": "remote",
                "url": "https://example.com/mcp",
                "transport": "streamable_http",
            }
        )
        assert config["transport"] == "http"


class TestCredentialStateHelpers:
    """Test credential gating helpers"""

    def test_secret_reference_is_not_treated_as_pending(self):
        config = {
            "type": "remote",
            "url": "https://example.com/mcp?apikey={secret:qianxin_mcp_key}",
        }
        assert config_has_pending_credentials(config) is False
        assert should_skip_connect_on_add(config) is False
        assert get_connect_block_reason(config) is None

    def test_blank_sensitive_header_is_treated_as_pending(self):
        config = {
            "type": "remote",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": ""},
        }
        assert config_has_pending_credentials(config) is True
        assert should_skip_connect_on_add(config) is True


class TestExtractApiKeyFromMcpUrl:
    """Test secret extraction from remote MCP URLs"""

    def test_extracts_sensitive_query_value_to_secret_reference(self):
        saved_secrets: dict[str, str] = {}

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        with mock.patch("flocks.security.get_secret_manager", return_value=SecretManagerStub()):
            updated = extract_api_key_from_mcp_url(
                "demo-mcp",
                {
                    "type": "remote",
                    "url": "https://example.com/mcp?apikey=token123",
                },
            )

        assert saved_secrets == {"demo-mcp_mcp_key": "token123"}
        assert updated["url"] == "https://example.com/mcp?apikey={secret:demo-mcp_mcp_key}"

    def test_keeps_existing_secret_reference_unchanged(self):
        with mock.patch("flocks.security.get_secret_manager") as get_secret_manager:
            updated = extract_api_key_from_mcp_url(
                "demo-mcp",
                {
                    "type": "remote",
                    "url": "https://example.com/mcp?apikey={secret:demo-mcp_mcp_key}",
                },
            )

        get_secret_manager.assert_not_called()
        assert updated["url"] == "https://example.com/mcp?apikey={secret:demo-mcp_mcp_key}"


class TestExtractAuthValueFromMcpConfig:
    """Test secret extraction from MCP auth config."""

    def test_extracts_plain_auth_value_to_secret_reference(self):
        saved_secrets: dict[str, str] = {}

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        with mock.patch("flocks.security.get_secret_manager", return_value=SecretManagerStub()):
            updated = extract_auth_value_from_mcp_config(
                "demo-mcp",
                {
                    "type": "remote",
                    "url": "https://example.com/sse",
                    "auth": {
                        "type": "apikey",
                        "location": "header",
                        "param_name": "Authorization",
                        "scheme": "bearer",
                        "value": "Bearer token123",
                    },
                },
            )

        assert saved_secrets == {"demo-mcp_mcp_key": "token123"}
        assert updated["auth"]["value"] == "{secret:demo-mcp_mcp_key}"
        assert updated["auth"]["scheme"] == "bearer"

    def test_infers_bearer_scheme_from_authorization_header(self):
        saved_secrets: dict[str, str] = {}

        class SecretManagerStub:
            def set(self, key: str, value: str) -> None:
                saved_secrets[key] = value

        with mock.patch("flocks.security.get_secret_manager", return_value=SecretManagerStub()):
            updated = extract_auth_value_from_mcp_config(
                "demo-mcp",
                {
                    "type": "remote",
                    "url": "https://example.com/sse",
                    "auth": {
                        "type": "apikey",
                        "location": "header",
                        "param_name": "Authorization",
                        "value": "Bearer token123",
                    },
                },
            )

        assert saved_secrets == {"demo-mcp_mcp_key": "token123"}
        assert updated["auth"]["value"] == "{secret:demo-mcp_mcp_key}"
        assert updated["auth"]["scheme"] == "bearer"

    def test_keeps_existing_secret_reference_unchanged(self):
        with mock.patch("flocks.security.get_secret_manager") as get_secret_manager:
            updated = extract_auth_value_from_mcp_config(
                "demo-mcp",
                {
                    "type": "remote",
                    "url": "https://example.com/sse",
                    "auth": {
                        "type": "apikey",
                        "location": "header",
                        "param_name": "Authorization",
                        "value": "{secret:demo-mcp_mcp_key}",
                    },
                },
            )

        get_secret_manager.assert_not_called()
        assert updated["auth"]["value"] == "{secret:demo-mcp_mcp_key}"


class TestResolveEnvVar:
    """Test environment variable resolution"""
    
    def test_plain_value(self):
        """Test plain value"""
        value = resolve_env_var("plain_value")
        assert value == "plain_value"
    
    def test_env_var(self):
        """Test environment variable"""
        os.environ["TEST_MCP_VAR"] = "test_value"
        value = resolve_env_var("${TEST_MCP_VAR}")
        assert value == "test_value"
    
    def test_missing_env_var(self):
        """Test missing environment variable"""
        with pytest.raises(ValueError):
            resolve_env_var("${NONEXISTENT_VAR}")
    
    def test_empty_value(self):
        """Test empty value"""
        value = resolve_env_var("")
        assert value == ""

    def test_secret_format(self):
        """Test {secret:KEY} reads from SecretManager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / ".secret.json"
            secret_file.write_text(json.dumps({"my_test_key": "secret_value_123"}))
            secret_file.chmod(0o600)

            from flocks.security.secrets import SecretManager
            sm = SecretManager(secret_file=secret_file)
            with mock.patch("flocks.security.secrets.get_secret_manager", return_value=sm):
                value = resolve_env_var("{secret:my_test_key}")
                assert value == "secret_value_123"

    def test_secret_format_missing_key(self):
        """Test {secret:KEY} raises ValueError when key not found"""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / ".secret.json"
            secret_file.write_text("{}")
            secret_file.chmod(0o600)

            from flocks.security.secrets import SecretManager
            sm = SecretManager(secret_file=secret_file)
            with mock.patch("flocks.security.secrets.get_secret_manager", return_value=sm):
                with pytest.raises(ValueError, match="Secret not found"):
                    resolve_env_var("{secret:nonexistent_key}")


class TestSanitizeName:
    """Test name sanitization"""
    
    def test_normal_name(self):
        """Test normal name"""
        name = sanitize_name("my_tool")
        assert name == "my_tool"
    
    def test_with_spaces(self):
        """Test with spaces"""
        name = sanitize_name("My Tool")
        assert name == "my_tool"
    
    def test_with_special_chars(self):
        """Test with special characters"""
        name = sanitize_name("tool@v1.0")
        assert name == "tool_v1_0"
    
    def test_starts_with_digit(self):
        """Test starts with digit"""
        name = sanitize_name("123tool")
        assert name == "_123tool"
    
    def test_uppercase(self):
        """Test uppercase to lowercase conversion"""
        name = sanitize_name("MyTool")
        assert name == "mytool"


class TestGenerateToolName:
    """Test tool name generation"""
    
    def test_basic(self):
        """Test basic generation"""
        name = generate_tool_name("ThreatBook", "ip_query")
        assert name == "threatbook_ip_query"
    
    def test_with_special_chars(self):
        """Test special character handling"""
        name = generate_tool_name("My Server", "my-tool")
        assert name == "my_server_my-tool"


class TestCalculateSchemaHash:
    """Test schema hash calculation"""
    
    def test_same_schema(self):
        """Test same schema produces same hash"""
        schema1 = {"type": "object", "properties": {"a": {"type": "string"}}}
        schema2 = {"type": "object", "properties": {"a": {"type": "string"}}}
        hash1 = calculate_schema_hash(schema1)
        hash2 = calculate_schema_hash(schema2)
        assert hash1 == hash2
    
    def test_different_schema(self):
        """Test different schema produces different hash"""
        schema1 = {"type": "object", "properties": {"a": {"type": "string"}}}
        schema2 = {"type": "object", "properties": {"b": {"type": "string"}}}
        hash1 = calculate_schema_hash(schema1)
        hash2 = calculate_schema_hash(schema2)
        assert hash1 != hash2
    
    def test_order_independent(self):
        """Test field order does not affect hash"""
        schema1 = {"properties": {"a": 1, "b": 2}, "type": "object"}
        schema2 = {"type": "object", "properties": {"b": 2, "a": 1}}
        hash1 = calculate_schema_hash(schema1)
        hash2 = calculate_schema_hash(schema2)
        assert hash1 == hash2
