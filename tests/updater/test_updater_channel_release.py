"""What the updater does with a release an extension's update channel offers.

The channel says where the release is and what of it to install; the updater
downloads (sending the channel's credentials to the release's own origin
only), verifies, and installs -- or takes a component out again.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from flocks.updater import updater
from flocks.updater.channels import ChannelRelease, InstallPlan


class _Resp:
    reason_phrase = "OK"

    def __init__(self, status_code=200, headers=None, chunks=(b"bundle",), body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_bytes(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk


class _Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _client_answering(responses: dict[str, _Resp], seen: list[tuple[str, dict]]):
    class _Client:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            seen.append((url, dict(headers or {})))
            return _Stream(responses[url])

    return _Client


@pytest.mark.asyncio
async def test_channel_credentials_go_to_the_release_origin_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[tuple[str, dict]] = []
    responses = {
        "https://console.example.com/v1/bundles/rel_1/download": _Resp(302, headers={"location": "https://cdn.example.com/b.zip"}),
        "https://cdn.example.com/b.zip": _Resp(200),
    }
    monkeypatch.setattr(updater.httpx, "AsyncClient", _client_answering(responses, seen))

    path = await updater._download_channel_archive(
        "https://console.example.com/v1/bundles/rel_1/download",
        tmp_path,
        "bundle.zip",
        headers={"Authorization": "Bearer cs_token"},
    )

    assert path.read_bytes() == b"bundle"
    assert seen == [
        ("https://console.example.com/v1/bundles/rel_1/download", {"Authorization": "Bearer cs_token"}),
        # the redirect leaves the console: the token stays behind
        ("https://cdn.example.com/b.zip", {}),
    ]


@pytest.mark.asyncio
async def test_channel_download_follows_a_relative_redirect_with_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[tuple[str, dict]] = []
    responses = {
        "https://console.example.com/v1/bundles/rel_1/download": _Resp(307, headers={"location": "/files/b.zip"}),
        "https://console.example.com/files/b.zip": _Resp(200),
    }
    monkeypatch.setattr(updater.httpx, "AsyncClient", _client_answering(responses, seen))

    await updater._download_channel_archive(
        "https://console.example.com/v1/bundles/rel_1/download", tmp_path, "bundle.zip", headers={"Authorization": "Bearer t"}
    )

    assert [headers for _url, headers in seen] == [{"Authorization": "Bearer t"}, {"Authorization": "Bearer t"}]


@pytest.mark.asyncio
async def test_channel_download_emits_byte_progress(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[tuple[str, dict]] = []
    url = "https://cdn.example.com/extension/bundle.zip"
    responses = {url: _Resp(200, headers={"content-length": "11"}, chunks=(b"hello", b" ", b"world"))}
    monkeypatch.setattr(updater.httpx, "AsyncClient", _client_answering(responses, seen))
    progresses: list[updater.UpdateProgress] = []

    async def _record(progress):
        progresses.append(progress)

    await updater._download_channel_archive(url, tmp_path, "bundle.zip", label="extension bundle", progress_callback=_record)

    assert progresses[-1].stage == "fetching"
    assert progresses[-1].message == "Downloading extension bundle..."
    assert (progresses[-1].downloaded_bytes, progresses[-1].total_bytes, progresses[-1].percent) == (11, 11, 100)


@pytest.mark.asyncio
async def test_channel_download_reports_http_status_and_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: list[tuple[str, dict]] = []
    url = "https://console.example.com/v1/bundles/rel_1/download?license_id=lic_1"
    responses = {url: _Resp(401, body=b"missing console_session_token")}
    responses[url].reason_phrase = "Unauthorized"
    monkeypatch.setattr(updater.httpx, "AsyncClient", _client_answering(responses, seen))

    with pytest.raises(RuntimeError, match="HTTP 401 Unauthorized: missing console_session_token"):
        await updater._download_channel_archive(url, tmp_path, "bundle.zip", headers={"Authorization": "Bearer t"})

    assert [u for u, _h in seen] == [url]


@pytest.mark.asyncio
async def test_removing_components_uses_uv_uninstall_without_yes_flag(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    install_root = tmp_path / "install"
    python_path = updater._venv_python_path(install_root)
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def _fake_run_async(cmd, cwd=None, timeout=None, env=None):
        captured.update(cmd=cmd, cwd=cwd, timeout=timeout, env=env)
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    error = await updater._uninstall_packages(
        ["extension-component"], uv_path="/usr/bin/uv", install_root=install_root, env={"UV_NO_PROGRESS": "1"}
    )

    assert error is None
    assert captured["cmd"] == ["/usr/bin/uv", "pip", "uninstall", "--python", str(python_path), "extension-component"]
    assert "-y" not in captured["cmd"]
    assert (captured["cwd"], captured["timeout"], captured["env"]) == (install_root, 180, {"UV_NO_PROGRESS": "1"})


@pytest.mark.asyncio
async def test_a_release_that_replaces_the_source_needs_the_handoff(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    release_root = tmp_path / "release-root"
    (release_root / "flocks").mkdir(parents=True)
    (release_root / "flocks" / "pyproject.toml").write_text('[project]\nname = "flocks"\n', encoding="utf-8")
    (release_root / "flocks" / "new_core.py").write_text("UPDATED = True\n", encoding="utf-8")
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in release_root.rglob("*"):
            if path.is_file():
                handle.write(path, path.relative_to(release_root).as_posix())

    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "old_core.py").write_text("OLD = True\n", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: install_root)
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.10")
    monkeypatch.setattr(updater, "_find_executable", lambda name: "/usr/bin/uv" if name == "uv" else None)
    version_writes: list[str] = []
    monkeypatch.setattr(updater, "_write_version_marker", lambda version: version_writes.append(version))
    captured: list[list[str]] = []

    async def _fake_run_async(cmd, **_kwargs):
        captured.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(updater, "_run_async", _fake_run_async)

    def _plan(content_root: Path, current_version: str, work_dir: Path) -> InstallPlan:
        return InstallPlan(source_root=content_root / "flocks", version="2026.5.11")

    release = ChannelRelease(version="v2026.5.11", plan=_plan, archive_path=archive, label="extension bundle", own_version_rules=True)
    progresses = [step async for step in updater.perform_update("v2026.5.11", release=release, restart=False)]

    assert progresses[-1].stage == "error"
    assert "detached handoff" in progresses[-1].message
    assert not (install_root / "new_core.py").exists() and (install_root / "old_core.py").exists()
    assert captured == [] and version_writes == []


@pytest.mark.asyncio
async def test_a_plan_that_refuses_the_release_stops_before_anything_changes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setattr(updater, "_get_repo_root", lambda: tmp_path / "install")
    monkeypatch.setattr(updater, "get_current_version", lambda: "2026.5.10")

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a refused release must not touch the installation")

    monkeypatch.setattr(updater, "_run_async", _must_not_run)

    def _refuse(content_root, current_version, work_dir):
        raise ValueError("the bundle ships no component wheel")

    release = ChannelRelease(version="v2", plan=_refuse, archive_path=release_dir, label="extension bundle", own_version_rules=True)
    progresses = [step async for step in updater.perform_update("v2", release=release, restart=False)]

    assert [step.stage for step in progresses] == ["fetching", "error"]
    assert progresses[-1].message == "Cannot install this extension bundle: the bundle ships no component wheel"
