from __future__ import annotations

import os
import asyncio
import secrets
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


class _ExtensionChannel:
    """An update channel an installed extension would register."""

    name = "extension"

    def __init__(self):
        self.released: list[str | None] = []

    async def check(self, *, locale=None, remote=True):
        from flocks.updater.models import VersionInfo

        return VersionInfo(current_version="v1", latest_version="v2", has_update=True)

    async def release(self, *, version=None):
        from flocks.updater.channels import ChannelRelease, InstallPlan

        self.released.append(version)
        return ChannelRelease(
            version=version or "v2",
            plan=lambda root, current, work: InstallPlan(source_root=None, version=current),
            archive_url="https://releases.example.test/extension-v2.tar.gz",
            label="extension bundle",
        )


@pytest.fixture
def extension_channel():
    from flocks.updater.channels import register_update_channel, unregister_update_channel

    channel = _ExtensionChannel()
    register_update_channel(channel)
    yield channel
    unregister_update_channel(channel.name)


async def test_check_version_requires_admin_for_an_extension_channel(monkeypatch: pytest.MonkeyPatch, extension_channel):
    from flocks.server.routes import update as update_routes

    called = False

    def _deny_admin(_request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    async def _fake_check_update(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Extension channel checks must not reach updater before admin auth")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    # ``edition`` is the name older clients still send.
    for params in ({"channel": "extension"}, {"edition": "extension"}):
        with pytest.raises(HTTPException) as exc:
            await update_routes.check_version(_request(), locale=None, **params)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert called is False


async def test_an_unknown_channel_is_refused(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes

    async def _fake_check_update(**kwargs):
        raise AssertionError("an unknown channel must not reach the updater")

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: object())
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    for call in (
        update_routes.check_version(_request(), locale=None, channel="nobody"),
        update_routes.apply_update(_request(), target_version=None, locale=None, channel="nobody"),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


async def test_apply_installs_what_the_extension_channel_releases(monkeypatch: pytest.MonkeyPatch, extension_channel):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import UpdateProgress, VersionInfo

    seen: dict[str, object] = {}

    async def _fake_check_update(**kwargs):
        seen["check"] = kwargs
        return VersionInfo(current_version="v1", latest_version="v2", has_update=True)

    async def _fake_perform_update(version, **kwargs):
        seen["perform"] = (version, kwargs)
        yield UpdateProgress(stage="done", message="ok", success=True)

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: object())
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)
    monkeypatch.setattr(update_routes, "perform_update", _fake_perform_update)

    response = await update_routes.apply_update(_request(), target_version=None, locale=None, channel="extension")
    body = "".join([chunk async for chunk in response.body_iterator])

    assert '"stage":"done"' in body
    assert seen["check"] == {"locale": None, "channel": "extension"}
    version, kwargs = seen["perform"]
    assert version == "v2"
    assert extension_channel.released == ["v2"]
    assert kwargs["release"].label == "extension bundle"


async def test_apply_reports_a_channel_that_cannot_release(monkeypatch: pytest.MonkeyPatch, extension_channel):
    from flocks.server.routes import update as update_routes

    async def _refuse(*, version=None):
        raise ValueError("manifest frozen")

    async def _fake_perform_update(version, **kwargs):
        raise AssertionError("nothing to install without a release")
        yield  # pragma: no cover

    monkeypatch.setattr(extension_channel, "release", _refuse)
    monkeypatch.setattr(update_routes, "require_admin", lambda _request: object())
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_routes, "perform_update", _fake_perform_update)

    response = await update_routes.apply_update(_request(), target_version="v2", locale=None, channel="extension")
    body = "".join([chunk async for chunk in response.body_iterator])

    assert '"stage": "error"' in body and "manifest frozen" in body


async def test_check_version_keeps_flocks_channel_public(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    def _deny_admin(_request):
        raise AssertionError("Flocks channel check should not require admin at route level")

    async def _fake_check_update(**kwargs):
        assert kwargs == {"locale": "zh-CN", "channel": None}
        return VersionInfo(current_version="v2026.5.9")

    monkeypatch.setattr(update_routes, "require_admin", _deny_admin)
    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    info = await update_routes.check_version(_request(), locale="zh-CN", edition="flocks")

    assert info.current_version == "v2026.5.9"


async def test_current_release_is_available_to_members_and_cached(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0

    def _member(_request):
        return object()

    async def _release(version):
        nonlocal calls
        calls += 1
        assert version == "2026.9.23"
        return VersionInfo(
            current_version=version,
            latest_version=version,
            release_notes="Full official release notes by @author in #749",
        )

    monkeypatch.setattr(update_routes, "require_user", _member)
    monkeypatch.setattr(update_routes, "get_current_version", lambda: "2026.9.23")
    monkeypatch.setattr(update_routes, "get_installed_release", _release)

    first = await update_routes.current_release(_request())
    second = await update_routes.current_release(_request())

    assert calls == 1
    assert first.release_notes == second.release_notes
    assert first.latest_version == "2026.9.23"


async def test_current_release_deduplicates_concurrent_lookup(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0

    async def _release(version):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return VersionInfo(current_version=version, latest_version=version)

    monkeypatch.setattr(update_routes, "require_user", lambda _request: object())
    monkeypatch.setattr(update_routes, "get_current_version", lambda: "2026.9.23")
    monkeypatch.setattr(update_routes, "get_installed_release", _release)

    await asyncio.gather(*[update_routes.current_release(_request()) for _ in range(5)])

    assert calls == 1


async def test_current_release_http_allows_logged_in_member(client, monkeypatch: pytest.MonkeyPatch):
    from flocks.auth.service import AuthService
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    password = secrets.token_urlsafe(18)  # secret-guard: allow - generated test credential
    await AuthService.bootstrap_admin(username="release-admin", password=password)
    member = await AuthService._create_user_internal(username="release-member", password=password)
    browser_headers = {"sec-fetch-mode": "cors"}

    async def _release(version):
        return VersionInfo(
            current_version=version,
            latest_version=version,
            release_notes="Full official notes by @author in #749",
        )

    monkeypatch.setattr(update_routes, "get_current_version", lambda: "2026.9.23")
    monkeypatch.setattr(update_routes, "get_installed_release", _release)

    denied = await client.get("/api/update/current-release", headers=browser_headers)
    assert denied.status_code == 401

    login = await client.post(
        "/api/auth/login",
        json={"username": member.username, "password": password},
        headers=browser_headers,
    )
    assert login.status_code == 200, login.text
    response = await client.get("/api/update/current-release", headers=browser_headers)

    assert response.status_code == 200, response.text
    assert response.json()["current_version"] == "2026.9.23"
    assert response.json()["release_notes"] == "Full official notes by @author in #749"


async def test_check_version_reuses_cached_flocks_result(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    calls = 0

    async def _fake_check_update(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs == {"locale": "zh-CN", "channel": None}
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


async def test_check_version_deduplicates_concurrent_forced_requests(
    monkeypatch: pytest.MonkeyPatch,
):
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
        update_routes.check_version(
            _request(), locale="zh-CN", edition="flocks", force=True
        )
        for _ in range(10)
    ])

    assert calls == 1
    assert [item.current_version for item in results] == ["v1"] * 10


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


async def test_forced_check_reuses_older_same_key_inflight_request(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import update as update_routes
    from flocks.updater.models import VersionInfo

    older_started = asyncio.Event()
    release_older = asyncio.Event()
    calls = 0

    async def _fake_check_update(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            older_started.set()
            await release_older.wait()
            return VersionInfo(current_version="v1")
        return VersionInfo(current_version="v2")

    monkeypatch.setattr(update_routes, "check_update", _fake_check_update)

    older = asyncio.create_task(
        update_routes.check_version(_request(), locale="zh-CN", edition="flocks")
    )
    await older_started.wait()

    forced = asyncio.create_task(
        update_routes.check_version(
            _request(), locale="zh-CN", edition="flocks", force=True
        )
    )
    await asyncio.sleep(0)
    assert calls == 1

    release_older.set()
    assert (await older).current_version == "v1"
    assert (await forced).current_version == "v1"

    cached = await update_routes.check_version(
        _request(), locale="zh-CN", edition="flocks"
    )
    assert cached.current_version == "v1"
    assert calls == 1


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
        assert kwargs.get("channel") is None
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


@pytest.mark.parametrize("bad", ["../../evil", "2026.03.24/../x", "v1;rm", "..", "a b", "2026.03.24\\n"])
async def test_apply_update_rejects_malformed_target_version(monkeypatch: pytest.MonkeyPatch, bad: str):
    """``target_version`` is spliced into the download URL / archive name, so
    anything that is not a release tag is refused before perform_update."""
    from flocks.server.routes import update as update_routes

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: None)

    def _no_perform(*args, **kwargs):
        raise AssertionError("perform_update must not run for a malformed target_version")

    monkeypatch.setattr(update_routes, "perform_update", _no_perform)
    with pytest.raises(HTTPException) as exc:
        await update_routes.apply_update(_request(), target_version=bad, locale=None, edition="flocks")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid target_version"


@pytest.mark.parametrize("good", ["2026.03.24", "v2026.3.24", "1.2.3-rc1", "2026.9.9+build.7"])
async def test_apply_update_accepts_release_tags(monkeypatch: pytest.MonkeyPatch, good: str):
    from flocks.server.routes import update as update_routes

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "source")

    async def _gen(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(update_routes, "perform_update", _gen)
    response = await update_routes.apply_update(_request(), target_version=good, locale=None, edition="flocks")
    assert response.media_type == "text/event-stream"


async def test_apply_update_refuses_managed_deployments_with_409(monkeypatch: pytest.MonkeyPatch):
    """A Go-managed release package is upgraded by the core; the route answers
    409 with the canonical refusal text without touching the network."""
    from flocks.server.routes import update as update_routes
    from flocks.updater.deploy import MANAGED_UPGRADE_REFUSAL

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "managed")

    async def _no_check(**kwargs):
        raise AssertionError("check_update must not run for a managed deployment")

    monkeypatch.setattr(update_routes, "check_update", _no_check)
    with pytest.raises(HTTPException) as exc:
        await update_routes.apply_update(_request(), target_version=None, locale=None, edition="flocks")
    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.detail == MANAGED_UPGRADE_REFUSAL


async def test_apply_update_keeps_docker_refusal_as_sse_event(monkeypatch: pytest.MonkeyPatch):
    """Docker keeps the historical SSE error event (the WebUI renders it)."""
    import json

    from flocks.server.routes import update as update_routes
    from flocks.updater.deploy import DOCKER_UPGRADE_REFUSAL

    monkeypatch.setattr(update_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "docker")
    response = await update_routes.apply_update(_request(), target_version=None, locale=None, edition="flocks")
    chunks = [c async for c in response.body_iterator]
    body = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    assert json.loads(body.split("data: ", 1)[1].strip()) == {"stage": "error", "message": DOCKER_UPGRADE_REFUSAL, "success": False}


async def test_apply_update_refuses_on_offline_install(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import update as update_routes

    monkeypatch.setattr(update_routes, "require_admin", lambda _req: None)
    monkeypatch.setattr(update_routes, "detect_deploy_mode", lambda: "offline")

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("perform_update must not start on an offline install")

    monkeypatch.setattr(update_routes, "perform_update", _must_not_run)

    response = await update_routes.apply_update(_request())
    body = "".join([chunk if isinstance(chunk, str) else chunk.decode() async for chunk in response.body_iterator])

    assert response.media_type == "text/event-stream"
    assert '"stage": "error"' in body
    assert "flocks-offline.run" in body
