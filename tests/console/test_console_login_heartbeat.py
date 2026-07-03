import asyncio
import json

from flocks.console import login as login_mod
from flocks.console.login import ConsoleLoginService


def test_heartbeat_payload_reports_oss_for_core_only_install(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_EDITION", "flockspro")
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3.3"))

    payload = ConsoleLoginService.heartbeat_payload(
        {
            "console_session_token": "cs_heartbeat",
            "fingerprint": "fp_heartbeat",
            "install_id": "inst_heartbeat",
        },
    )

    assert payload["edition"] == "oss"
    assert payload["core_version"] == "2026.7.3.3"
    assert "version" not in payload
    assert "bundle_version" not in payload
    assert "flockspro_component_version" not in payload
    assert "version_info" not in payload


def test_heartbeat_payload_includes_pro_runtime_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3"))

    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "bundle_version": "v2026.7.3",
                "core_version": "v2026.7.3",
            }
        ),
        encoding="utf-8",
    )

    payload = ConsoleLoginService.heartbeat_payload(
        {
            "console_session_token": "cs_heartbeat",
            "fingerprint": "fp_heartbeat",
            "install_id": "inst_heartbeat",
        },
        status="poc",
        license_id="lic_heartbeat",
        pro_component_version="2026.7.3.1",
    )

    assert payload["fingerprint"] == "fp_heartbeat"
    assert payload["install_id"] == "inst_heartbeat"
    assert payload["status"] == "poc"
    assert payload["license_id"] == "lic_heartbeat"
    assert payload["edition"] == "flockspro"
    assert payload["bundle_version"] == "v2026.7.3"
    assert payload["core_version"] == "v2026.7.3"
    assert payload["flockspro_component_version"] == "2026.7.3.1"
    assert "version" not in payload
    assert "version_info" not in payload


def test_send_heartbeat_uses_local_pro_license_and_applies_response(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3"))

    license_path = tmp_path / "flockspro" / "license.json"
    license_path.parent.mkdir(parents=True)
    license_path.write_text(
        json.dumps(
            {
                "license_id": "lic_core",
                "payload": {"license_id": "lic_core", "status": "poc"},
                "patches": [],
            }
        ),
        encoding="utf-8",
    )
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "bundle_version": "v2026.7.3",
                "core_version": "v2026.7.3",
                "flockspro_component_version": "2026.7.3.1",
            }
        ),
        encoding="utf-8",
    )

    async def _require_session(cls):
        return {
            "console_session_token": "cs_core",
            "fingerprint": "fp_core",
            "install_id": "inst_core",
        }

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "license_patch": "patch_token_1",
                "revoked_license_ids": ["lic_revoked"],
            }

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert captured["url"] == "https://console.example.com/v1/heartbeats"
    assert captured["headers"] == {"Authorization": "Bearer cs_core"}
    payload = captured["json"]
    assert payload["status"] == "poc"
    assert payload["license_id"] == "lic_core"
    assert payload["bundle_version"] == "v2026.7.3"
    assert payload["core_version"] == "v2026.7.3"
    assert payload["flockspro_component_version"] == "2026.7.3.1"
    assert "version" not in payload
    assert "version_info" not in payload

    updated = json.loads(license_path.read_text(encoding="utf-8"))
    assert updated["patches"] == ["patch_token_1"]
    assert updated["last_sync_at"]
    revocation = json.loads((tmp_path / "flockspro" / "revocation.json").read_text(encoding="utf-8"))
    assert revocation == {"revoked_license_ids": ["lic_revoked"]}
