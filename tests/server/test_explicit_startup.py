"""The server starts its extensions explicitly, not on import (V1.2 3.2).

Importing ``flocks.server.app`` must not run any installed plugin; the lifespan
prepares the extension layer first -- plugins, then the deployment's declared
requirements, then their routes -- and only then starts business services.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import flocks.server.app as server_app


def _fake_plugin(tmp_path: Path, marker: Path) -> Path:
    """An installed package whose ``flocks.plugins`` entry point leaves a marker."""
    site = tmp_path / "site"
    package = site / "fakeplug_g04"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path


            def register():
                Path({str(marker)!r}).write_text("loaded", encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    dist = site / "fakeplug_g04-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: fakeplug-g04\nVersion: 0.1\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[flocks.plugins]\nfakeplug_g04 = fakeplug_g04:register\n", encoding="utf-8")
    return site


def test_importing_the_app_runs_no_plugin_until_it_is_prepared(tmp_path: Path) -> None:
    marker = tmp_path / "plugin-ran"
    site = _fake_plugin(tmp_path, marker)
    script = textwrap.dedent(
        f"""
        import json, os
        import flocks.server.app as app_module

        before = {{"prepared": app_module.app_prepared(), "plugin_ran": os.path.exists({str(marker)!r})}}
        app_module.prepare_app()
        after = {{"prepared": app_module.app_prepared(), "plugin_ran": os.path.exists({str(marker)!r})}}
        print(json.dumps({{"before": before, "after": after}}))
        """
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(site), str(Path(server_app.__file__).resolve().parents[2]), env.get("PYTHONPATH", "")]
    )
    env["HOME"] = str(tmp_path / "home")
    env["USERPROFILE"] = env["HOME"]
    env.pop("FLOCKS_ROOT", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["before"] == {"prepared": False, "plugin_ran": False}
    assert result["after"] == {"prepared": True, "plugin_ran": True}


class _StopAfterStorage(Exception):
    """Ends the lifespan right after storage started: nothing later matters here."""


@pytest.fixture(autouse=True)
def _no_install_tree_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lifespan tidies leftovers of an earlier upgrade in the install tree; not here.
    from flocks.updater import updater

    monkeypatch.setattr(updater, "cleanup_replaced_files", lambda *_args, **_kwargs: None)


@pytest.mark.asyncio
async def test_lifespan_prepares_extensions_before_any_business_service(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(server_app, "_prepared", False)
    monkeypatch.setattr(server_app, "_load_installed_package_plugins", lambda: order.append("plugins"))
    monkeypatch.setattr(server_app, "_install_extension_routes", lambda: order.append("routes"))

    async def storage_init(*_args, **_kwargs):
        order.append("storage")
        raise _StopAfterStorage()

    monkeypatch.setattr(server_app.Storage, "init", storage_init)

    with pytest.raises(_StopAfterStorage):
        async with server_app.lifespan(server_app.app):
            pass

    assert order == ["plugins", "routes", "storage"]
    assert server_app.app_prepared() is True


@pytest.mark.asyncio
async def test_a_failed_extension_start_stops_the_server_before_business_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.plugin.loader import CriticalPluginEntrypointFailure

    started: list[str] = []
    monkeypatch.setattr(server_app, "_prepared", False)

    def refuse():
        raise CriticalPluginEntrypointFailure("required capability missing")

    async def storage_init(*_args, **_kwargs):
        started.append("storage")

    monkeypatch.setattr(server_app, "_load_installed_package_plugins", refuse)
    monkeypatch.setattr(server_app, "_install_extension_routes", lambda: started.append("routes"))
    monkeypatch.setattr(server_app.Storage, "init", storage_init)

    with pytest.raises(CriticalPluginEntrypointFailure):
        async with server_app.lifespan(server_app.app):
            pass

    assert started == []
    assert server_app.app_prepared() is False


def test_prepare_app_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server_app, "_prepared", False)
    monkeypatch.setattr(server_app, "_load_installed_package_plugins", lambda: calls.append("plugins"))
    monkeypatch.setattr(server_app, "_install_extension_routes", lambda: calls.append("routes"))

    server_app.prepare_app()
    server_app.prepare_app()

    assert calls == ["plugins", "routes"]


@pytest.mark.asyncio
async def test_middleware_an_extension_adds_while_the_server_starts_serves_the_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifespan runs inside the middleware stack the server already built;
    an extension registering then may still add middleware (Pro's workspace
    isolation does), and every request after the start goes through it."""
    import httpx
    from fastapi import FastAPI
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    application = FastAPI()

    @application.get("/ping")
    async def ping():
        return {"ok": True}

    application.middleware_stack = application.build_middleware_stack()  # the server has started

    async def tag(request, call_next):
        response = await call_next(request)
        response.headers["x-extension"] = "seen"
        return response

    def register_like_an_extension():
        # What Pro does: refuse once the stack is built, else append inside the guard.
        if application.middleware_stack is not None:
            raise RuntimeError("Cannot add middleware after an application has started")
        application.user_middleware.append(Middleware(BaseHTTPMiddleware, dispatch=tag))

    monkeypatch.setattr(server_app, "prepare_app", register_like_an_extension)

    await server_app._prepare_during_startup(application)

    assert application.middleware_stack is not None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/ping")
    assert response.status_code == 200
    assert response.headers.get("x-extension") == "seen"


@pytest.mark.asyncio
async def test_a_failed_start_still_leaves_a_middleware_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    application = FastAPI()
    application.middleware_stack = application.build_middleware_stack()

    def refuse():
        raise RuntimeError("critical plugin entrypoint failure")

    monkeypatch.setattr(server_app, "prepare_app", refuse)
    with pytest.raises(RuntimeError):
        await server_app._prepare_during_startup(application)
    assert application.middleware_stack is not None
