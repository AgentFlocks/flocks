import asyncio

import pytest

from flocks.server import app as server_app


@pytest.fixture(autouse=True)
def restore_http_hooks():
    original = list(server_app._http_middleware_hooks)
    server_app._http_middleware_hooks.clear()
    yield
    server_app._http_middleware_hooks[:] = original


@pytest.mark.asyncio
async def test_non_critical_http_hook_failure_is_isolated():
    called: list[str] = []

    async def failing_hook(request, context):
        called.append("failing")
        raise RuntimeError("boom")

    async def success_hook(request, context):
        called.append(context["stage"])

    server_app.register_http_middleware(failing_hook, name="failing")
    server_app.register_http_middleware(success_hook, name="success")

    await server_app._run_http_middleware_hooks(object(), {"stage": "before_auth"})

    assert called == ["failing", "before_auth"]


@pytest.mark.asyncio
async def test_critical_http_hook_failure_propagates():
    async def failing_hook(request, context):
        raise RuntimeError("critical boom")

    server_app.register_http_middleware(failing_hook, name="critical", critical=True)

    with pytest.raises(RuntimeError, match="critical boom"):
        await server_app._run_http_middleware_hooks(object(), {"stage": "before_auth"})


@pytest.mark.asyncio
async def test_http_hook_timeout_can_propagate():
    async def slow_hook(request, context):
        await asyncio.sleep(0.05)

    server_app.register_http_middleware(
        slow_hook,
        name="critical-slow",
        timeout_seconds=0.01,
        fail_policy="propagate",
    )

    with pytest.raises(asyncio.TimeoutError):
        await server_app._run_http_middleware_hooks(object(), {"stage": "before_auth"})


def test_http_hook_registration_replaces_by_name():
    async def first_hook(request, context):
        return None

    async def second_hook(request, context):
        return None

    server_app.register_http_middleware(first_hook, name="same")
    server_app.register_http_middleware(second_hook, name="same")

    assert len(server_app._http_middleware_hooks) == 1
    assert server_app._http_middleware_hooks[0].hook is second_hook
