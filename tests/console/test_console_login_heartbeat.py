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
                "flockspro_component_version": "2026.7.3.1",
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


def test_heartbeat_payload_keeps_sending_with_incomplete_pro_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.5"))
    marker = tmp_path / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "bundle_version": "v2026.7.5",
                "flockspro_component_version": "v2026.7.4",
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
    )

    assert payload["edition"] == "flockspro"
    assert payload["bundle_version"] == "v2026.7.5"
    assert payload["core_version"] == ""
    assert payload["flockspro_component_version"] == "v2026.7.4"


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


def test_report_pending_pro_bundle_install_receipt_posts_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.delenv("FLOCKS_CONSOLE_BASE_URL", raising=False)

    license_path = tmp_path / "flockspro" / "license.json"
    license_path.parent.mkdir(parents=True)
    license_path.write_text(json.dumps({"license_id": "lic_pending"}), encoding="utf-8")
    pending_path = tmp_path / "run" / "pro-bundle-install-receipt-pending.json"
    pending_path.parent.mkdir(parents=True)
    pending_path.write_text(
        json.dumps(
            {
                "release_id": "rel_pending",
                "bundle_release_id": "rel_pending",
                "bundle_version": "2026.7.3.5",
                "core_version": "2026.7.3.5",
                "flockspro_component_version": "2026.7.3.3",
                "build_id": "job_pending",
                "install_result": "success",
            }
        ),
        encoding="utf-8",
    )

    async def _require_session(cls):
        return {
            "console_session_token": "cs_pending",
            "fingerprint": "fp_pending",
            "install_id": "inst_pending",
            "console_base_url": "http://127.0.0.1:18001",
        }

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

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

    reported = asyncio.run(ConsoleLoginService.report_pending_pro_bundle_install_receipt())

    assert reported is True
    assert not pending_path.exists()
    assert captured["url"] == "http://127.0.0.1:18001/v1/pro-bundles/installations"
    assert captured["headers"] == {"Authorization": "Bearer cs_pending"}
    payload = captured["json"]
    assert payload["fingerprint"] == "fp_pending"
    assert payload["install_id"] == "inst_pending"
    assert payload["license_id"] == "lic_pending"
    assert payload["bundle_version"] == "2026.7.3.5"


def test_report_pending_pro_bundle_downgrade_receipt_posts_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.delenv("FLOCKS_CONSOLE_BASE_URL", raising=False)

    pending_path = tmp_path / "run" / "pro-bundle-downgrade-receipt-pending.json"
    pending_path.parent.mkdir(parents=True)
    pending_path.write_text(
        json.dumps(
            {
                "request_id": "req_downgrade_pending",
                "release_id": "rel_downgrade_pending",
                "bundle_release_id": "rel_downgrade_pending",
                "license_id": "lic_downgrade_pending",
                "bundle_version": "2026.7.3.5",
                "core_version": "2026.7.3.5",
                "flockspro_component_version": "2026.7.3.3",
                "install_result": "downgraded",
                "runtime_edition": "oss",
            }
        ),
        encoding="utf-8",
    )

    async def _require_session(cls):
        return {
            "console_session_token": "cs_pending",
            "fingerprint": "fp_pending",
            "install_id": "inst_pending",
            "console_base_url": "http://127.0.0.1:18001",
        }

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

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

    reported = asyncio.run(ConsoleLoginService.report_pending_pro_bundle_install_receipt())

    assert reported is True
    assert not pending_path.exists()
    assert captured["url"] == "http://127.0.0.1:18001/v1/pro-bundles/installations"
    assert captured["headers"] == {"Authorization": "Bearer cs_pending"}
    payload = captured["json"]
    assert payload["fingerprint"] == "fp_pending"
    assert payload["install_id"] == "inst_pending"
    assert payload["license_id"] == "lic_downgrade_pending"
    assert payload["install_result"] == "downgraded"
    assert payload["runtime_edition"] == "oss"
