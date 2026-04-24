import json

from flocks.server import app as app_module


def test_read_cors_config_merges_runtime_and_configured_origins(monkeypatch, tmp_path) -> None:
    config_file = tmp_path / "flocks.json"
    config_file.write_text(
        json.dumps(
            {
                "server": {
                    "cors": [
                        "https://configured.example",
                        "http://10.0.0.9:5173",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module.Config, "get_config_file", lambda: config_file)
    monkeypatch.setenv("_FLOCKS_WEBUI_HOST", "10.0.0.9")
    monkeypatch.setenv("_FLOCKS_WEBUI_PORT", "5173")

    allow_origins, allow_origin_regex = app_module._read_cors_config()

    assert allow_origins == [
        "http://10.0.0.9:5173",
        "https://configured.example",
    ]
    assert allow_origin_regex == app_module._LOCALHOST_ORIGIN_RE


def test_read_cors_config_ignores_localhost_and_wildcard_runtime_hosts(monkeypatch, tmp_path) -> None:
    config_file = tmp_path / "missing.json"
    monkeypatch.setattr(app_module.Config, "get_config_file", lambda: config_file)
    monkeypatch.setenv("_FLOCKS_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("_FLOCKS_WEBUI_PORT", "5173")

    allow_origins, allow_origin_regex = app_module._read_cors_config()

    assert allow_origins == []
    assert allow_origin_regex == app_module._LOCALHOST_ORIGIN_RE


def test_read_cors_config_brackets_ipv6_webui_origin(monkeypatch, tmp_path) -> None:
    config_file = tmp_path / "missing.json"
    monkeypatch.setattr(app_module.Config, "get_config_file", lambda: config_file)
    monkeypatch.setenv("_FLOCKS_WEBUI_HOST", "2001:db8::2")
    monkeypatch.setenv("_FLOCKS_WEBUI_PORT", "5173")

    allow_origins, allow_origin_regex = app_module._read_cors_config()

    assert allow_origins == ["http://[2001:db8::2]:5173"]
    assert allow_origin_regex == app_module._LOCALHOST_ORIGIN_RE
