import json
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.mcp import MCP
from flocks.mcp.types import McpStatus, McpStatusInfo
from flocks.skill.onboarding_status import build_onboarding_preflight_status


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("FLOCKS_ROOT", raising=False)
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    Config._global_config = None
    Config._cached_config = None
    yield Config.get_config_path()
    Config._global_config = None
    Config._cached_config = None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_onboarding_status_uses_default_model_services_and_channels(isolated_user_config, monkeypatch) -> None:
    config_dir = isolated_user_config
    _write_json(
        config_dir / "flocks.json",
        {
            "default_models": {
                "llm": {
                    "provider_id": "threatbook-cn-llm",
                    "model_id": "minimax-m2.7",
                }
            },
            "api_services": {
                "threatbook-cn": {"enabled": True, "apiKey": "{secret:threatbook_cn_api_key}"},
                "virustotal": {"enabled": True, "apiKey": "{secret:virustotal_api_key}"},
                "fofa": {"enabled": True, "apiKey": "{secret:fofa_key}"},
                "urlscan": {"enabled": True, "apiKey": "{secret:urlscan_api_key}"},
                "shodan": {"enabled": True, "apiKey": "{secret:shodan_api_key}"},
            },
            "mcp": {
                "threatbook_mcp": {
                    "type": "remote",
                    "url": "https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}",
                    "enabled": True,
                }
            },
            "channels": {
                "feishu": {"enabled": True},
                "wecom": {"enabled": False},
                "dingtalk": {"enabled": True},
                "telegram": {"enabled": False},
            },
        },
    )
    _write_json(
        config_dir / ".secret.json",
        {
            "threatbook_cn_api_key": "tb-api-key",
            "threatbook_mcp_key": "tb-mcp-key",
            "virustotal_api_key": "vt-key",
            "fofa_key": "foo@example.com:fofa-key",
            "urlscan_api_key": "urlscan-key",
            "shodan_api_key": "shodan-key",
        },
    )

    async def fake_status(cls):
        return {
            "threatbook_mcp": McpStatusInfo(status=McpStatus.CONNECTED, tools_count=3),
        }

    monkeypatch.setattr(MCP, "status", classmethod(fake_status))

    status = await build_onboarding_preflight_status()

    assert status["llm_status"] == {
        "openai": False,
        "anthropic": False,
        "threatbook": True,
    }
    assert status["tb_api_configured"] is True
    assert status["tb_mcp_configured"] is True
    assert status["tb_mcp_connected"] is True
    assert status["tb_mcp_status"] == "connected"
    assert status["security_tool_status"] == {
        "virustotal": True,
        "fofa": True,
        "urlscan": True,
        "shodan": True,
    }
    assert status["channel_status"] == {
        "feishu": True,
        "wecom": False,
        "dingtalk": True,
        "telegram": False,
    }


@pytest.mark.asyncio
async def test_onboarding_status_distinguishes_configured_but_disconnected_mcp(isolated_user_config, monkeypatch) -> None:
    config_dir = isolated_user_config
    _write_json(
        config_dir / "flocks.json",
        {
            "mcp": {
                "threatbook_mcp": {
                    "type": "remote",
                    "url": "https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}",
                    "enabled": True,
                }
            }
        },
    )
    _write_json(config_dir / ".secret.json", {"threatbook_mcp_key": "tb-mcp-key"})

    async def fake_status(cls):
        return {
            "threatbook_mcp": McpStatusInfo(status=McpStatus.DISCONNECTED),
        }

    monkeypatch.setattr(MCP, "status", classmethod(fake_status))

    status = await build_onboarding_preflight_status()

    assert status["tb_mcp_configured"] is True
    assert status["tb_mcp_connected"] is False
    assert status["tb_mcp_status"] == "configured"


@pytest.mark.asyncio
async def test_onboarding_status_supports_legacy_threatbook_api_fallback(isolated_user_config, monkeypatch) -> None:
    config_dir = isolated_user_config
    _write_json(config_dir / "flocks.json", {"api_services": {}})
    _write_json(config_dir / ".secret.json", {"threatbook_api_key": "legacy-tb-key"})

    async def fake_status(cls):
        return {}

    monkeypatch.setattr(MCP, "status", classmethod(fake_status))

    status = await build_onboarding_preflight_status()

    assert status["tb_api_configured"] is True
