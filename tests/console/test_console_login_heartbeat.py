import asyncio
import base64
import json
import os
import time

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
            captured.setdefault("urls", []).append(url)
            captured["json"] = json
            return _Response()

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert captured["json"]["status"] == "ok"
    assert captured["json"]["license_id"] is None
    assert captured["json"]["edition"] == "oss"
    # The heartbeat and nothing else: reports are an extension's to send.
    assert captured["urls"] == ["https://console.example.com/v1/heartbeats"]


def _receipt_console(monkeypatch, tmp_path, *, receipt_status=200, receipt_error=None):
    """A Console for the heartbeat and the receipt, and the receipt the old updater left. The requests it saw."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(ConsoleLoginService, "_runtime_version", staticmethod(lambda: "2026.11.4.3"))

    async def _require_session(cls):
        return {"console_session_token": "cs_core", "fingerprint": "fp_core", "install_id": "inst_core"}

    seen: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code

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
            seen.append((url, json))
            if url.endswith("/v1/heartbeats"):
                return _Response(200)
            assert headers == {"Authorization": "Bearer cs_core"}
            if receipt_error:
                raise receipt_error
            return _Response(receipt_status)

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)
    return seen


def _write_receipt(tmp_path, content='{"release_id": "rel_1", "bundle_version": "2026.11.01.2", "license_id": "ubuntu-nn", "install_result": "success"}'):
    path = tmp_path / "run" / "pro-bundle-install-receipt-pending.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_the_old_updaters_receipt_goes_with_the_next_heartbeat_once(tmp_path, monkeypatch):
    seen = _receipt_console(monkeypatch, tmp_path)
    path = _write_receipt(tmp_path)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert [url.rsplit("/", 1)[-1] for url, _ in seen] == ["heartbeats", "installations"]
    assert seen[1][1] == {"release_id": "rel_1", "bundle_version": "2026.11.01.2", "license_id": "ubuntu-nn", "install_result": "success",
                          "fingerprint": "fp_core", "install_id": "inst_core"}
    assert not path.exists()

    asyncio.run(ConsoleLoginService.send_heartbeat())
    assert [url.rsplit("/", 1)[-1] for url, _ in seen] == ["heartbeats", "installations", "heartbeats"]


@pytest.mark.parametrize("kwargs", [{"receipt_status": 500}, {"receipt_status": 401}, {"receipt_error": login_mod.httpx.ConnectError("down")}])
def test_a_receipt_console_did_not_take_stays_for_the_next_heartbeat(tmp_path, monkeypatch, kwargs):
    seen = _receipt_console(monkeypatch, tmp_path, **kwargs)
    path = _write_receipt(tmp_path)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert [url.rsplit("/", 1)[-1] for url, _ in seen] == ["heartbeats", "installations"]
    assert path.exists()


def test_a_receipt_that_does_not_read_does_not_stop_the_heartbeat(tmp_path, monkeypatch):
    seen = _receipt_console(monkeypatch, tmp_path)
    path = _write_receipt(tmp_path, "not json")

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert [url.rsplit("/", 1)[-1] for url, _ in seen] == ["heartbeats"]
    assert path.exists()


def test_an_extension_reports_what_it_owes_itself(tmp_path, monkeypatch, node_extension):
    node_extension(identity={"status": "commercial", "license_id": "ubuntu-nn"})
    seen = _receipt_console(monkeypatch, tmp_path)
    path = _write_receipt(tmp_path)

    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert [url.rsplit("/", 1)[-1] for url, _ in seen] == ["heartbeats"]
    assert path.exists()


# --- the Python enterprise package still on this release (the transition package) ---


class _Answered:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _console_answers(monkeypatch, body, sent):
    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return _Answered(body)

    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)


_SESSION = {"console_session_token": "cs", "fingerprint": "fp", "install_id": "inst"}


def test_a_heartbeat_keeps_the_previous_editions_licence_alive(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    licence = tmp_path / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-old", "status": "commercial", "last_sync_at": 1, "last_heartbeat_ok_at": 1, "patches": ["p1"]}))
    os.chmod(licence, 0o600)
    _console_answers(monkeypatch, {"license_patch": "p2", "revoked_license_ids": ["b", "a"]}, [])

    asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=_SESSION))

    state = json.loads(licence.read_text())
    now = time.time()
    assert abs(state["last_sync_at"] - now) < 60 and abs(state["last_heartbeat_ok_at"] - now) < 60
    assert state["patches"] == ["p1", "p2"] and state["license_id"] == "lic-old"
    assert licence.stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path / "flockspro" / "revocation.json").read_text()) == {"revoked_license_ids": ["a", "b"]}
    assert [p.name for p in licence.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_machine_with_no_such_licence_gets_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    _console_answers(monkeypatch, {"revoked_license_ids": ["a"]}, [])

    asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=_SESSION))

    assert not (tmp_path / "flockspro").exists()


def test_an_extension_keeps_its_own_licence_and_this_file_stays(tmp_path, monkeypatch, node_extension):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    node_extension(identity={"status": "poc", "license_id": "lic_core"})
    licence = tmp_path / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-old", "last_sync_at": 1}))
    _console_answers(monkeypatch, {}, [])

    asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=_SESSION))

    assert json.loads(licence.read_text())["last_sync_at"] == 1


def test_a_file_that_cannot_be_written_does_not_fail_the_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    licence = tmp_path / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-old"}))
    _console_answers(monkeypatch, {"ok": True}, [])

    def no_room(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(login_mod, "_write_private_json", no_room)
    assert asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=_SESSION)) == {"ok": True}


def test_the_heartbeat_names_the_previous_editions_licence(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    licence = tmp_path / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-old", "status": "commercial"}))
    sent: list[dict] = []
    _console_answers(monkeypatch, {}, sent)

    async def _require_session(cls):
        return _SESSION

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    asyncio.run(ConsoleLoginService.send_heartbeat())
    assert sent[0]["license_id"] == "lic-old" and sent[0]["status"] == "commercial"

    # The way a licence signed by the older Console keeps these.
    licence.write_text(json.dumps({"payload": {"license_id": "ubuntu-nn-root", "license_status": "poc"}}))
    asyncio.run(ConsoleLoginService.send_heartbeat())
    assert sent[1]["license_id"] == "ubuntu-nn-root" and sent[1]["status"] == "poc"

    # A file that keeps them only inside the token: Console could not answer for a licence not named.
    claims = base64.urlsafe_b64encode(json.dumps({"license_id": "token-only", "status": "commercial"}).encode()).rstrip(b"=").decode()
    licence.write_text(json.dumps({"license_id": None, "key": f"h.{claims}.s"}))
    asyncio.run(ConsoleLoginService.send_heartbeat())
    assert sent[2]["license_id"] == "token-only" and sent[2]["status"] == "commercial"


def test_a_heartbeat_keeps_the_licence_where_the_page_found_it(tmp_path, monkeypatch):
    """The old offline checker kept it under the home's .flocks, whatever FLOCKS_ROOT says."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])
    licence = tmp_path / "home" / ".flocks" / "flockspro" / "license.json"
    licence.parent.mkdir(parents=True)
    licence.write_text(json.dumps({"license_id": "lic-home"}))
    sent: list[dict] = []
    _console_answers(monkeypatch, {"license_patch": "p1"}, sent)

    async def _require_session(cls):
        return _SESSION

    monkeypatch.setattr(ConsoleLoginService, "_require_session", classmethod(_require_session))
    asyncio.run(ConsoleLoginService.send_heartbeat())

    assert sent[0]["license_id"] == "lic-home"
    assert json.loads(licence.read_text())["patches"] == ["p1"]
    assert not (tmp_path / "root").exists()


@pytest.mark.parametrize("code", [401, 403])
def test_a_refused_sign_in_says_which_refusal_and_is_still_a_value_error(tmp_path, monkeypatch, code):
    """The scheduler passes over a ValueError; the page says what happened."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.example.com")
    monkeypatch.setattr(login_mod, "_node_extensions", [])

    class _Refused(_Answered):
        status_code = code

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _Refused({})

    monkeypatch.setattr(login_mod.httpx, "AsyncClient", _Client)
    with pytest.raises(login_mod.ConsoleSessionRefused) as refused:
        asyncio.run(ConsoleLoginService.send_heartbeat_for_session(session=_SESSION))
    assert refused.value.status_code == code and isinstance(refused.value, ValueError)


def test_a_write_that_fails_leaves_the_licence_and_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "license.json"
    path.write_text('{"a": 1}')
    os.chmod(path, 0o600)
    real = os.replace

    def no_room(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(login_mod.os, "replace", no_room)
    with pytest.raises(OSError):
        login_mod._write_private_json(path, {"a": 2})
    monkeypatch.setattr(login_mod.os, "replace", real)
    assert json.loads(path.read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["license.json"]
