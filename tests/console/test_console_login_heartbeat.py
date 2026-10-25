import asyncio

import pytest

from flocks.console import login as login_mod
from flocks.console.login import ConsoleLoginService


def test_heartbeat_payload_reports_oss_for_core_only_install(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_EDITION", "sample-extension")
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
    assert "extension_component_version" not in payload
    assert "version_info" not in payload


class _NodeExtension:
    """What an installed extension would add to the node's reports."""

    def __init__(self, versions=None, identity=None):
        self.versions = versions
        self.identity = identity or {}
        self.responses: list[dict] = []

    def node_versions(self):
        return self.versions

    def heartbeat_identity(self):
        return self.identity

    async def on_heartbeat_response(self, data, *, client, session, console_base, report_pending):
        self.responses.append({"data": data, "console_base": console_base, "report_pending": report_pending})


@pytest.fixture
def node_extension():
    created: list[_NodeExtension] = []

    def _register(**kwargs):
        extension = _NodeExtension(**kwargs)
        login_mod.register_console_node_extension(extension)
        created.append(extension)
        return extension

    yield _register
    for extension in created:
        login_mod.unregister_console_node_extension(extension)


def test_heartbeat_payload_uses_the_versions_an_extension_reports(tmp_path, monkeypatch, node_extension):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3"))
    node_extension(versions={"edition": "extension-edition", "core_version": "v2026.7.3", "component_version": "1.2"})

    payload = ConsoleLoginService.heartbeat_payload(
        {"console_session_token": "cs_heartbeat", "fingerprint": "fp_heartbeat", "install_id": "inst_heartbeat"},
        status="poc",
        license_id="lic_heartbeat",
    )

    assert payload["status"] == "poc" and payload["license_id"] == "lic_heartbeat"
    assert payload["edition"] == "extension-edition"
    assert payload["core_version"] == "v2026.7.3"
    assert payload["component_version"] == "1.2"


def test_an_extension_with_nothing_to_report_leaves_the_default(tmp_path, monkeypatch, node_extension):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3"))
    node_extension(versions=None)

    payload = ConsoleLoginService.heartbeat_payload(
        {"console_session_token": "cs", "fingerprint": "fp", "install_id": "inst"},
    )

    assert payload["edition"] == "oss" and payload["core_version"] == "2026.7.3"


def test_send_heartbeat_reports_the_extension_identity_and_hands_it_the_answer(tmp_path, monkeypatch, node_extension):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.7.3"))
    extension = node_extension(identity={"status": "poc", "license_id": "lic_core"})

    async def _require_session(cls):
        return {"console_session_token": "cs_core", "fingerprint": "fp_core", "install_id": "inst_core"}

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"license_patch": "patch_token_1"}

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
    assert captured["json"]["status"] == "poc" and captured["json"]["license_id"] == "lic_core"
    assert extension.responses == [
        {"data": {"license_patch": "patch_token_1"}, "console_base": "https://console.example.com", "report_pending": True}
    ]


def test_heartbeat_without_extensions_sends_the_plain_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")

    async def _require_session(cls):
        return {"console_session_token": "cs_core", "fingerprint": "fp_core", "install_id": "inst_core"}

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            return _Response()

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert captured["json"]["status"] == "ok"
    assert captured["json"]["license_id"] is None
    assert captured["json"]["edition"] == "oss"
