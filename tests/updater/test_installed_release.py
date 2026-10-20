"""The notice must use the notes published for the installed Core tag or Pro bundle."""

import json

import httpx
import pytest

from flocks.config.config import UpdaterConfig
from flocks.updater import updater


@pytest.mark.asyncio
async def test_installed_release_uses_exact_tag_and_full_body(monkeypatch):
    body = "### 工作台与场景\n\n* feat(webui): 保留状态 by @jamon-bodhi in #749"
    seen_urls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            seen_urls.append((url, kwargs))
            return httpx.Response(200, json={
                "tag_name": "v2026.9.23",
                "body": body,
                "html_url": "https://github.com/AgentFlocks/flocks/releases/tag/v2026.9.23",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.current_version == result.latest_version == "2026.9.23"
    assert result.release_notes == body
    assert result.release_url == "https://github.com/AgentFlocks/flocks/releases/tag/v2026.9.23"
    assert len(seen_urls) == 2
    assert seen_urls[0][0].endswith("/releases/tags/v2026.9.23")


@pytest.mark.asyncio
async def test_installed_release_ignores_mismatched_mirror_and_uses_matching_fallback(monkeypatch):
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            requested.append(url)
            tag = "v2026.9.14" if "api.github.com" in url else "v2026.9.23"
            return httpx.Response(200, json={
                "tag_name": tag,
                "body": f"notes for {tag}",
                "html_url": f"https://example.com/{tag}",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("v2026.9.23")

    assert result.release_notes == "notes for v2026.9.23"
    assert len(requested) == 2
    assert all(url.endswith("/releases/tags/v2026.9.23") for url in requested)


@pytest.mark.asyncio
async def test_installed_release_fails_closed_when_sources_have_no_matching_notes(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            return httpx.Response(200, json={
                "tag_name": "v2026.9.14",
                "body": "outdated notes",
            }, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.release_notes is None
    assert result.latest_version == "2026.9.23"


@pytest.mark.asyncio
async def test_installed_release_returns_no_notes_when_all_sources_fail(monkeypatch):
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(503, request=httpx.Request("GET", url))

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr("flocks.updater.deploy.detect_deploy_mode", lambda: "source")

    result = await updater.get_installed_release("2026.9.23")

    assert result.release_notes is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_installed_release_skips_unknown_version_without_network(monkeypatch):
    def should_not_call(*_args, **_kwargs):
        raise AssertionError("unknown versions must not reach release APIs")

    monkeypatch.setattr(updater, "_get_updater_config", should_not_call)

    result = await updater.get_installed_release("")

    assert result.release_notes is None
    assert result.latest_version is None


def _write_pro_marker(root, **fields):
    marker = root / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bundle_version": "v2026.10.16",
        "core_version": "v2026.10.15",
        "flockspro_component_version": "2026.10.16",
        **fields,
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")


def _console_release(version, notes, **fields):
    manifest = {"bundle_version": version, "core_version": "v2026.10.15", **fields}
    if notes is not None:
        manifest["release_notes"] = notes
    return updater.ConsoleManifestRelease(
        version=version,
        release_notes=notes,
        release_url=None,
        bundle_url="https://console.example/bundle.zip",
        bundle_sha256=None,
        bundle_format="zip",
        manifest=manifest,
    )


def test_installed_pro_marker_requires_bundle_version(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    assert updater.read_installed_pro_bundle_marker() is None

    _write_pro_marker(tmp_path, bundle_version="")
    assert updater.read_installed_pro_bundle_marker() is None

    _write_pro_marker(tmp_path)
    assert updater.read_installed_pro_bundle_marker()["bundle_version"] == "v2026.10.16"


@pytest.mark.asyncio
async def test_installed_pro_release_uses_marker_notes_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path, release_notes="### Pro\n\n- 代码审计报告中心")

    async def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("marker notes must not reach Console")

    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", should_not_fetch)
    monkeypatch.setattr(updater, "_get_updater_config", should_not_fetch)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.edition == "flockspro"
    assert result.current_version == result.latest_version == "v2026.10.16"
    assert result.current_bundle_version == "v2026.10.16"
    assert result.current_core_version == "v2026.10.15"
    assert result.current_pro_component_version == "2026.10.16"
    assert result.release_notes == "### Pro\n\n- 代码审计报告中心"


@pytest.mark.asyncio
async def test_installed_pro_release_falls_back_to_console_notes_of_the_same_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path)

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    async def console_release():
        return _console_release("v2026.10.16", "Pro notes from Console")

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", console_release)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.edition == "flockspro"
    assert result.release_notes == "Pro notes from Console"


@pytest.mark.asyncio
@pytest.mark.parametrize("console_failure", ["newer_bundle", "fetch_error"])
async def test_installed_pro_release_never_borrows_other_notes(monkeypatch, tmp_path, console_failure):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path)

    async def config():
        return UpdaterConfig(sources=["github", "gitee"])

    async def console_release():
        if console_failure == "fetch_error":
            raise ValueError("FLOCKS_CONSOLE_BASE_URL 未配置，无法使用 console-manifest 源")
        return _console_release("v2026.10.20", "notes of a release that is not installed")

    async def oss_release(*_args, **_kwargs):
        raise AssertionError("a Pro install must not fall back to the OSS Core notes")

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", console_release)
    monkeypatch.setattr(updater, "get_installed_release", oss_release)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.edition == "flockspro"
    assert result.current_bundle_version == "v2026.10.16"
    assert result.release_notes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "published"),
    [
        ({"flockspro_component_version": "v2026.6.1"}, {"flockspro_component_version": "v2026.6.2"}),
        ({"build_id": "job_1"}, {"build_id": "job_2"}),
        ({"release_id": "rel_1", "bundle_release_id": "rel_1"}, {"release_id": "rel_2"}),
        ({"core_version": "v2026.6.17"}, {"core_version": "v2026.6.18"}),
    ],
    ids=["component_only_update", "repacked_build", "republished_release", "core_update"],
)
async def test_installed_pro_release_rejects_console_notes_of_another_release_with_the_same_number(
    monkeypatch, tmp_path, installed, published
):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path, **{"bundle_version": "v2026.6.18", "core_version": "v2026.6.18", **installed})

    async def config():
        return UpdaterConfig(sources=["github"])

    async def console_release():
        fields = {"flockspro_component_version": "2026.10.16", "core_version": "v2026.6.18", **published}
        return _console_release("v2026.6.18", "notes of the other v2026.6.18 release", **fields)

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", console_release)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.current_bundle_version == "v2026.6.18"
    assert result.release_notes is None


@pytest.mark.asyncio
async def test_installed_pro_release_borrows_console_notes_when_only_the_kept_local_core_differs(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    # A bundle with an older core is recorded with the newer local core it kept.
    _write_pro_marker(
        tmp_path,
        bundle_version="v2026.6.13",
        core_version="v2026.6.18",
        flockspro_component_version="v2026.6.2",
        release_id="rel_new_pro_old_core",
        build_id="job_new_pro_old_core",
    )

    async def config():
        return UpdaterConfig(sources=["github"])

    async def console_release():
        return _console_release(
            "v2026.6.13",
            "new Pro on older core",
            core_version="v2026.6.13",
            flockspro_component_version="v2026.6.2",
            release_id="rel_new_pro_old_core",
            build_id="job_new_pro_old_core",
        )

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", console_release)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.current_core_version == "v2026.6.18"
    assert result.release_notes == "new Pro on older core"


@pytest.mark.asyncio
async def test_installed_pro_release_keeps_the_recorded_bundle_label(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path, bundle_version="pro-v2026.6.23", release_notes="legacy label notes")

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.current_bundle_version == result.latest_version == "pro-v2026.6.23"
    assert result.release_notes == "legacy label notes"


@pytest.mark.asyncio
async def test_installed_pro_release_respects_disabled_updater(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    _write_pro_marker(tmp_path)

    async def config():
        return UpdaterConfig(enabled=False)

    async def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("a disabled updater must not reach Console")

    monkeypatch.setattr(updater, "_get_updater_config", config)
    monkeypatch.setattr(updater, "_fetch_console_manifest_release_info", should_not_fetch)

    result = await updater.get_installed_pro_release(updater.read_installed_pro_bundle_marker())

    assert result.release_notes is None
