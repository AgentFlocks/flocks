import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.workflow.errors import RunCancelledError
from flocks.workflow.llm import LLMClient
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.models import Workflow
from flocks.provider.sdk.openai_base import LLMResponseTooLargeError


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeModel:
    def __init__(self, model_id: str):
        self.id = model_id


class _FakeProvider:
    def __init__(
        self,
        provider_id: str,
        behavior: str | list[str],
        *,
        configured: bool = True,
        models: list[str] | None = None,
        _shared_state: dict[str, object] | None = None,
    ):
        self.id = provider_id
        self._behavior = behavior
        self._configured = configured
        self._models = models or []
        self._shared_state = _shared_state or {"calls": 0, "last_config": None}

    @property
    def calls(self) -> int:
        return int(self._shared_state["calls"])

    @calls.setter
    def calls(self, value: int) -> None:
        self._shared_state["calls"] = value

    @property
    def last_config(self):
        return self._shared_state["last_config"]

    @last_config.setter
    def last_config(self, value) -> None:
        self._shared_state["last_config"] = value

    def __copy__(self):
        return _FakeProvider(
            self.id,
            self._behavior,
            configured=self._configured,
            models=list(self._models),
            _shared_state=self._shared_state,
        )

    def configure(self, cfg):  # pragma: no cover
        self.last_config = cfg
        return None

    def is_configured(self):
        return self._configured

    def get_models(self):
        return [_FakeModel(model_id) for model_id in self._models]

    async def chat(self, model_id: str, messages, **kwargs):
        self.calls += 1
        behavior = self._behavior
        if isinstance(behavior, list):
            current = behavior.pop(0) if behavior else "ok"
        else:
            current = behavior

        if current == "error":
            raise RuntimeError("simulated failure")
        if current == "too_large":
            raise LLMResponseTooLargeError("response exceeded limit")
        if current == "timeout":
            await asyncio.sleep(0.05)
            return _FakeResponse("late")
        if current == "slow":
            await asyncio.sleep(5)
            return _FakeResponse("late")
        return _FakeResponse(f"{self.id}:{model_id}")


def _patch_provider(monkeypatch, providers: dict[str, _FakeProvider]):
    from flocks.provider import provider as provider_mod

    monkeypatch.setattr(provider_mod.Provider, "_ensure_initialized", lambda: None)

    async def _noop_apply_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(provider_mod.Provider, "apply_config", _noop_apply_config)
    monkeypatch.setattr(provider_mod.Provider, "get", lambda pid: providers.get(pid))
    monkeypatch.setattr(provider_mod.Provider, "list_models", lambda provider_id=None: providers.get(provider_id).get_models() if provider_id and providers.get(provider_id) else [])

    # Avoid reading user/project config in unit tests.
    from flocks.workflow import llm as workflow_llm_mod

    async def _noop_config_get():
        class _Cfg:
            model = None

            def model_dump(self, **kwargs):  # pragma: no cover
                del kwargs
                return {}

        return _Cfg()

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _noop_config_get)

    async def _noop_default_llm():
        return None

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _noop_default_llm)


def test_llm_ask_uses_provider_chat(monkeypatch):
    provider = _FakeProvider("demo", "ok")
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello")

    assert out == "demo:m"
    assert provider.calls == 1


def test_llm_keeps_trust_env_from_workflow_config(monkeypatch):
    provider = _FakeProvider("demo", "ok")
    _patch_provider(monkeypatch, {"demo": provider})

    from flocks.workflow import llm as workflow_llm_mod

    async def _cfg_with_trust_env():
        class _Cfg:
            model = "demo/m"

            def model_dump(self, **kwargs):  # pragma: no cover
                del kwargs
                return {"workflow": {"llm": {"trust_env": True}}}

        return _Cfg()

    async def _resolve_default_llm():
        return {"provider_id": "demo", "model_id": "m"}

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _cfg_with_trust_env)
    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient()
    out = client.ask("hello")

    assert out == "demo:m"
    assert provider.last_config is not None
    assert provider.last_config.custom_settings.get("trust_env") is True


def test_llm_node_supports_provider_prefixed_model(monkeypatch):
    provider = _FakeProvider("demo", "ok", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    workflow = Workflow.from_dict(
        {
            "start": "summarize",
            "nodes": [
                {
                    "id": "summarize",
                    "type": "llm",
                    "prompt": "hello",
                    "model": "demo/m",
                    "output_key": "summary",
                }
            ],
            "edges": [],
        }
    )

    step = WorkflowEngine(workflow).run_node("summarize", {})

    assert step.outputs["summary"] == "demo:m"
    assert provider.calls == 1


def test_llm_falls_back_to_default_when_requested_model_missing(monkeypatch):
    requested = _FakeProvider("demo", "ok", models=["other"])
    fallback = _FakeProvider("fallback", "ok", models=["fallback-model"])
    _patch_provider(monkeypatch, {"demo": requested, "fallback": fallback})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient(provider_id="demo", model="missing-model")
    out = client.ask("hello")

    assert out == "fallback:fallback-model"
    assert requested.calls == 0
    assert fallback.calls == 1


def test_llm_falls_back_to_default_when_requested_call_fails(monkeypatch):
    requested = _FakeProvider("demo", "error", models=["m"])
    fallback = _FakeProvider("fallback", "ok", models=["fallback-model"])
    _patch_provider(monkeypatch, {"demo": requested, "fallback": fallback})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello")

    assert out == "fallback:fallback-model"
    assert requested.calls == 1
    assert fallback.calls == 1


def test_llm_retries_then_succeeds(monkeypatch):
    provider = _FakeProvider("demo", ["error", "error", "ok"], models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello", max_retries=2, retry_delay_s=0)

    assert out == "demo:m"
    assert provider.calls == 3


def test_llm_does_not_retry_an_oversized_response(monkeypatch):
    provider = _FakeProvider("demo", "too_large", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError, match="response exceeded limit"):
        client.ask("hello", max_retries=2, retry_delay_s=0)

    assert provider.calls == 1


@pytest.mark.parametrize("cancellable", [False, True])
def test_llm_timeout_retries_then_raises(monkeypatch, cancellable):
    provider = _FakeProvider("demo", "timeout", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000, "LLM timeout was swallowed by the cancellation poll loop"
        return False

    client = LLMClient(
        provider_id="demo", model="m", cancel_checker=never_cancel if cancellable else None,
    )
    with pytest.raises(ValueError, match="timed out after 0.01s"):
        client.ask("hello", timeout_s=0.01, max_retries=2, retry_delay_s=0)

    assert provider.calls == 3


def test_cancellable_llm_can_retry_after_timeout_and_succeed(monkeypatch):
    provider = _FakeProvider("demo", ["timeout", "ok"], models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000
        return False

    client = LLMClient(provider_id="demo", model="m", cancel_checker=never_cancel)
    assert client.ask("hello", timeout_s=0.01, max_retries=1, retry_delay_s=0) == "demo:m"
    assert provider.calls == 2


def test_llm_ask_honors_cancel_checker(monkeypatch):
    provider = _FakeProvider("demo", "slow", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    cancel_event = threading.Event()
    timer = threading.Timer(0.05, cancel_event.set)
    started = time.perf_counter()
    timer.start()
    try:
        client = LLMClient(
            provider_id="demo",
            model="m",
            cancel_checker=cancel_event.is_set,
        )
        with pytest.raises(RunCancelledError, match="Run cancelled"):
            client.ask("hello")
    finally:
        timer.cancel()

    assert time.perf_counter() - started < 1.0


def test_llm_raises_clear_error_when_default_is_unavailable(monkeypatch):
    provider = _FakeProvider("fallback", "ok", configured=False, models=["fallback-model"])
    _patch_provider(monkeypatch, {"fallback": provider})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient()
    with pytest.raises(ValueError, match="Workflow 默认模型不可用"):
        client.ask("hello")


def test_get_llm_client_does_not_stick_to_old_default(monkeypatch):
    first = _FakeProvider("first", "ok", models=["first-model"])
    second = _FakeProvider("second", "ok", models=["second-model"])
    _patch_provider(monkeypatch, {"first": first, "second": second})

    from flocks.workflow import llm as workflow_llm_mod

    state = {"provider_id": "first", "model_id": "first-model"}

    async def _resolve_default_llm():
        return dict(state)

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    out1 = workflow_llm_mod.get_llm_client().ask("hello")
    state.update({"provider_id": "second", "model_id": "second-model"})
    out2 = workflow_llm_mod.get_llm_client().ask("hello")

    assert out1 == "first:first-model"
    assert out2 == "second:second-model"


def test_workflow_options_are_rebuilt_for_actual_fallback_model(monkeypatch):
    from flocks.provider import options
    from flocks.workflow import llm as workflow_llm_mod

    first = _FakeProvider("first", "error", models=["requested"])
    fallback = _FakeProvider("fallback", "ok", models=["default"])
    _patch_provider(monkeypatch, {"first": first, "fallback": fallback})

    async def resolve_default():
        return {"provider_id": "fallback", "model_id": "default"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", resolve_default)
    built, sent = [], []

    def build(provider_id, model_id, **kwargs):
        # Workflow chat() is non-streaming: no session reasoning or max_tokens defaults.
        assert kwargs == {"reasoning_enabled": False, "resolve_max_tokens": False}
        built.append((provider_id, model_id))
        return {"reasoning_effort": f"mapped-for-{provider_id}/{model_id}", "max_tokens": 1234}

    original = _FakeProvider.chat

    async def capture(self, model_id, messages, **kwargs):
        sent.append((self.id, model_id, kwargs))
        return await original(self, model_id, messages, **kwargs)

    monkeypatch.setattr(options, "build_provider_options", build)
    monkeypatch.setattr(_FakeProvider, "chat", capture)
    assert LLMClient(provider_id="first", model="requested").ask("hello") == "fallback:default"
    assert built == [("first", "requested"), ("fallback", "default")]
    for provider_id, model_id, kwargs in sent:
        assert kwargs == {"temperature": 0.2, "max_tokens": 1234,
                          "reasoning_effort": f"mapped-for-{provider_id}/{model_id}"}


def test_workflow_hooks_receive_and_transform_effective_options(monkeypatch):
    from flocks.hooks.pipeline import HookPipeline, HookStage
    from flocks.provider import options

    provider = _FakeProvider("demo", "ok", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    monkeypatch.setattr(options, "build_provider_options", lambda *_, **__: {"max_tokens": 100})
    seen = []

    async def before(payload):
        assert payload["request"]["providerOptions"] == {"max_tokens": 100, "temperature": 0.2}
        return SimpleNamespace(output={"request": {"providerOptions": {"max_tokens": 50, "temperature": 0.1}}})

    async def chat(self, model_id, messages, **kwargs):
        seen.append(kwargs)
        return _FakeResponse("ok")

    monkeypatch.setattr(HookPipeline, "has_stage_handlers", AsyncMock(side_effect=lambda stage, *_: stage == HookStage.LLM_BEFORE))
    monkeypatch.setattr(HookPipeline, "run_llm_before", before)
    monkeypatch.setattr(_FakeProvider, "chat", chat)
    assert LLMClient(provider_id="demo", model="m").ask("hello") == "ok"
    assert seen == [{"max_tokens": 50, "temperature": 0.1}]


@pytest.mark.parametrize("provider_id, model_id", [
    ("anthropic", "claude-sonnet-4-5"),
    ("google", "gemini-2.5-pro"),
])
def test_workflow_chat_does_not_inherit_session_streaming_options(monkeypatch, provider_id, model_id):
    """Uses the real option builder. Session defaults would enable extended
    thinking with max_tokens 24192 for Claude, which the Anthropic SDK refuses
    to send without streaming (limit 21333), and would mix Gemini thought
    summaries into the answer of the non-streaming chat() call."""
    provider = _FakeProvider(provider_id, "ok", models=[model_id])
    _patch_provider(monkeypatch, {provider_id: provider})
    sent = []

    async def chat(self, model_id, messages, **kwargs):
        sent.append(kwargs)
        return _FakeResponse("ok")

    monkeypatch.setattr(_FakeProvider, "chat", chat)
    assert LLMClient(provider_id=provider_id, model=model_id).ask("hello") == "ok"
    (kwargs,) = sent
    assert "thinking" not in kwargs and "thinkingConfig" not in kwargs
    assert kwargs.get("max_tokens", 0) <= 21333


@pytest.mark.parametrize("behavior", ["ok", "error", "timeout", "retry", "cancel"])
def test_workflow_closes_private_client_on_owning_loop(monkeypatch, behavior):
    provider = _FakeProvider("demo", "ok", models=["m"])
    shared_client = SimpleNamespace(close=AsyncMock())
    provider._client = shared_client
    _patch_provider(monkeypatch, {"demo": provider})
    clients = []
    cancelled = threading.Event()
    closed = threading.Event()

    async def chat(self, model_id, messages, **kwargs):
        loop = asyncio.get_running_loop()
        client = SimpleNamespace(closed=False)

        async def close():
            assert asyncio.get_running_loop() is loop
            client.closed = True
            closed.set()

        client.close = close
        clients.append(client)
        self._client = client
        if behavior == "error" or behavior == "retry" and len(clients) == 1:
            raise RuntimeError("simulated failure")
        if behavior == "cancel":
            cancelled.set()
        if behavior in {"timeout", "cancel"}:
            await asyncio.sleep(5)
        return _FakeResponse("ok")

    monkeypatch.setattr(_FakeProvider, "chat", chat)
    client = LLMClient(provider_id="demo", model="m", cancel_checker=cancelled.is_set if behavior == "cancel" else None)
    kwargs = {"timeout_s": 0.01} if behavior == "timeout" else {"max_retries": 1, "retry_delay_s": 0}
    if behavior in {"error", "timeout", "cancel"}:
        with pytest.raises(RunCancelledError if behavior == "cancel" else ValueError):
            client.ask("hello", **kwargs)
    else:
        assert client.ask("hello", **kwargs) == "ok"
    # Cancellation returns from the worker before the loop finishes its finally.
    assert closed.wait(1)
    assert clients and all(item.closed for item in clients)
    if behavior == "retry":
        assert len(clients) == 2
    assert provider._client is shared_client
    shared_client.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("async_failure", [False, True])
async def test_workflow_closes_both_google_client_pools(monkeypatch, async_failure):
    from flocks.workflow import llm as workflow_llm_mod

    async def close_async():
        if async_failure:
            await asyncio.sleep(5)

    client = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock(side_effect=close_async)), close=Mock())
    provider = SimpleNamespace(_client=client)
    monkeypatch.setattr(workflow_llm_mod, "_CLIENT_CLOSE_TIMEOUT_S", 0.01)
    await workflow_llm_mod._close_provider_client(provider)
    assert provider._client is None
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


@pytest.mark.parametrize("failure", ["returns-self", "copy-error", "readonly-client"])
def test_workflow_refuses_clone_that_cannot_isolate_client(monkeypatch, failure):
    shared_client = SimpleNamespace(close=AsyncMock())
    if failure == "readonly-client":
        class ReadOnlyProvider(_FakeProvider):
            @property
            def _client(self):
                return shared_client

            def __copy__(self):
                return ReadOnlyProvider(self.id, self._behavior)

        provider = ReadOnlyProvider("demo", "ok")
    else:
        provider = _FakeProvider("demo", "ok")
        provider._client = shared_client

        def invalid_copy(self):
            if failure == "copy-error":
                raise RuntimeError("Cannot copy provider")
            return self

        monkeypatch.setattr(_FakeProvider, "__copy__", invalid_copy)
    _patch_provider(monkeypatch, {"demo": provider})
    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(RuntimeError):
        client._prepare_provider("demo")
    assert provider._client is shared_client
    shared_client.close.assert_not_awaited()
