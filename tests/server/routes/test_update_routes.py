from __future__ import annotations

import os
import asyncio
from urllib.parse import quote, unquote, urlparse

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request


pytestmark = pytest.mark.asyncio

_MANUAL_REAL_UPGRADE_ENV = "FLOCKS_RUN_REAL_WEBUI_UPGRADE_TEST"
_MANUAL_REAL_UPGRADE_BRANCH_ENV = "FLOCKS_REAL_WEBUI_UPGRADE_BRANCH"
_MANUAL_REAL_UPGRADE_TARGET_BRANCH = ""


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/update/check", "headers": []})


@pytest.fixture(autouse=True)
def _clear_update_cache():
    from flocks.server.routes import update as update_routes

    update_routes.clear_update_check_cache()
    yield
    update_routes.clear_update_check_cache()


def _manual_real_upgrade_branch() -> str:
    branch_input = (
        os.environ.get(_MANUAL_REAL_UPGRADE_BRANCH_ENV, "").strip()
        or _MANUAL_REAL_UPGRADE_TARGET_BRANCH.strip()
    )
    try:
        if not branch_input:
            branch_input = input("Target branch for the real WebUI upgrade test: ").strip()
        if not branch_input:
            pytest.skip("No target branch was provided for the real WebUI upgrade test")

        confirmation = input(
            "This will trigger a real upgrade and may replace the current install tree. "
            f"Type the branch name again to confirm ({branch_input}): "
        ).strip()
    except OSError as exc:
        pytest.skip(f"Interactive confirmation is required: {exc}")

    if confirmation != branch_input:
        pytest.skip("Real WebUI upgrade test was not confirmed")
    return _normalize_manual_branch_target(branch_input)


def _normalize_manual_branch_target(target: str) -> str:
    branch = target.strip()
    parsed = urlparse(branch)
    if parsed.scheme in {"http", "https"}:
        path = parsed.path
        github_marker = "/archive/refs/heads/"
        gitee_marker = "/repository/archive/"
        if github_marker in path:
            branch = path.split(github_marker, 1)[1]
        elif gitee_marker in path:
            branch = path.split(gitee_marker, 1)[1]
        branch = branch.removesuffix(".tar.gz").removesuffix(".zip")
        branch = unquote(branch)

    for prefix in ("refs/heads/",):
        if branch.startswith(prefix):
            branch = branch[len(prefix):]
            break

    if not branch:
        pytest.skip("No target branch was provided for the real WebUI upgrade test")
    return branch


def _manual_branch_version_label(branch: str) -> str:
    return "branch-" + branch.replace("/", "-")


def _github_branch_archive_url(branch: str, extension: str) -> str:
    encoded_branch = quote(branch, safe="/")
    return f"https://github.com/AgentFlocks/flocks/archive/refs/heads/{encoded_branch}.{extension}"


async def test_normalize_manual_branch_target_accepts_archive_urls():
    assert _normalize_manual_branch_target(
        "https://gitee.com/flocks/flocks/repository/archive/refactor/supervisor-control-adapters.zip"
    ) == "refactor/supervisor-control-adapters"
    assert _normalize_manual_branch_target(
        "https://github.com/AgentFlocks/flocks/archive/refs/heads/fix/session-mixed-parts-read-merge.zip"
    ) == "fix/session-mixed-parts-read-merge"


async def test_check_version_requires_admin_for_flockspro(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes

    called = False

    def _deny_admin(_request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    async def _fake_check_update(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Pro update checks must not reach updater before admin auth")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    with pytest.raises(HTTPException) as exc:
        await update_routes.check_version(_request(), locale=None, edition="flockspro")

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert called is False


async def test_check_version_keeps_flocks_channel_public(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    def _deny_admin(_request):
        raise AssertionError("Flocks channel check should not require admin at route level")

    async def _fake_check_update(**kwargs):
        assert kwargs == {"locale": "zh-CN", "force_console_manifest": False}
        return VersionInfo(current_version="v2026.5.9")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    info = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")

    assert info.current_version == "v2026.5.9"


async def test_check_version_reuses_cached_flocks_result(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0

    async def _fake_check_update(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs == {"locale": "zh-CN", "force_console_manifest": False}
        return VersionInfo(current_version=f"v{calls}")

    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    first = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")
    second = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")
    forced = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks", force=True)

    assert calls == 2
    assert first.current_version == "v1"
    assert second.current_version == "v1"
    assert forced.current_version == "v2"


async def test_check_version_deduplicates_inflight_requests(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0

    async def _fake_check_update(**kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return VersionInfo(current_version=f"v{calls}")

    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    results = await asyncio.gather(*[
        update_routes.check_version(_request(), locale="zh-CN", edition="flocks")
        for _ in range(5)
    ])

    assert calls == 1
    assert [item.current_version for item in results] == ["v1"] * 5


async def test_check_version_keeps_inflight_task_after_cancelled_request(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_check_update(**kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return VersionInfo(current_version=f"v{calls}")

    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    first = asyncio.create_task(update_routes.check_version(_request(), locale="zh-CN", edition="flocks"))
    second = None
    try:
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(update_routes.check_version(_request(), locale="zh-CN", edition="flocks"))
        await asyncio.sleep(0)
        assert calls == 1

        release.set()
        result = await asyncio.wait_for(second, timeout=1)
        assert result.current_version == "v1"

        cached = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")
        assert cached.current_version == "v1"
        assert calls == 1
    finally:
        release.set()
        if second is not None and not second.done():
            await asyncio.wait_for(second, timeout=1)


@pytest.mark.skipif(
    os.environ.get(_MANUAL_REAL_UPGRADE_ENV) != "1",
    reason=f"manual real upgrade test; set {_MANUAL_REAL_UPGRADE_ENV}=1 to enable",
)
async def test_manual_webui_apply_update_upgrades_to_confirmed_branch(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.config.config import UpdaterConfig
    from flocks.server.routes import update as update_routes
    from flocks.updater import updater as updater_module
    from flocks.updater.models import VersionInfo

    branch = _manual_real_upgrade_branch()
    version_label = _manual_branch_version_label(branch)

    async def _manual_github_updater_config():
        return UpdaterConfig(
            repo="AgentFlocks/flocks",
            gitee_repo=None,
            sources=["github"],
            archive_format="zip",
        )

    async def _manual_branch_update_info(**kwargs):
        assert kwargs["force_console_manifest"] is False
        return VersionInfo(
            current_version="manual-real-upgrade-test",
            latest_version=version_label,
            has_update=True,
            release_url=f"https://github.com/AgentFlocks/flocks/tree/{quote(branch, safe='/')}",
            zipball_url=_github_branch_archive_url(branch, "zip"),
            tarball_url=_github_branch_archive_url(branch, "tar.gz"),
        )

    monkeypatch.setattr(update_routes, "check_update", _manual_branch_update_info)
    monkeypatch.setattr(updater_module, "_get_updater_config", _manual_github_updater_config)

    response = await client.post(
        "/api/update/apply",
        params={"edition": "flocks"},
    )

    assert response.status_code == 200, response.text
    assert '"stage":"error"' not in response.text
