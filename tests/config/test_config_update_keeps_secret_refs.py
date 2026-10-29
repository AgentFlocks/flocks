"""Config.update / Config.get_unresolved must never turn {secret:...} references into plaintext."""
from __future__ import annotations

import json

import pytest

from flocks.config.config import Config, ConfigInfo

PLAIN = "sk-plain-secret-value-0123456789"


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    (config_dir / "flocks.json").write_text(json.dumps({
        "theme": "light",
        "provider": {"openai": {"npm": "@ai-sdk/openai", "options": {"apiKey": "{secret:openai_llm_key}"}, "models": {"gpt-4o": {"name": "gpt-4o"}}}},
    }))
    (config_dir / ".secret.json").write_text(json.dumps({"openai_llm_key": PLAIN}))
    from flocks.security import secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "_secret_manager", None)
    Config._global_config = None
    Config.clear_cache()
    yield config_dir
    Config.clear_cache()
    Config._global_config = None
    monkeypatch.setattr(secrets_mod, "_secret_manager", None)


async def test_get_unresolved_keeps_references_while_get_resolves(config_env):
    resolved = await Config.get()
    resolved_dump = resolved.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert resolved_dump["provider"]["openai"]["options"]["apiKey"] == PLAIN  # runtime view is resolved
    unresolved = await Config.get_unresolved()
    unresolved_dump = unresolved.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert unresolved_dump["provider"]["openai"]["options"]["apiKey"] == "{secret:openai_llm_key}"
    assert PLAIN not in json.dumps(unresolved.model_dump(by_alias=True, exclude_none=True, mode="json"))


async def test_update_writes_references_not_plaintext(config_env):
    await Config.get()  # warm the resolved cache like a running server
    await Config.update(ConfigInfo.model_validate({"theme": "dark"}))
    on_disk = (config_env / "flocks.json").read_text()
    assert PLAIN not in on_disk
    data = json.loads(on_disk)
    assert data["theme"] == "dark"
    assert data["provider"]["openai"]["options"]["apiKey"] == "{secret:openai_llm_key}"
